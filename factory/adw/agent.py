"""The agent CLI seam: one session per stage, corrections resume it.

`AgentSession` is what the pipeline talks to. `CliSession` owns the parts every CLI
shares — spawn, stream, time out, stay killable — and each subclass speaks one
dialect: `ClaudeSession` here (`claude -p … --resume`), `CodexSession` in
`adw/codex.py` (`codex exec --json` / `codex exec resume`). Both normalise their
stream into the same `AgentTurn`.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

# How a stage child says whose it is. masterwork reads these off the child session
# instead of pattern-matching the process ancestry, which depended on how the runner
# happened to be invoked (`factory/run.py` vs `run.py` from inside the directory).
RUN_ID_ENV = "MASTERWORK_FACTORY_RUN_ID"
STAGE_ENV = "MASTERWORK_FACTORY_STAGE"

CLAUDE = "claude"
CODEX = "codex"
AGENTS = (CLAUDE, CODEX)
DEFAULT_AGENT = CLAUDE


class AgentError(Exception):
    """The CLI could not be launched."""


# Every agent subprocess this process currently has open. A hung run is hung inside
# one of them, so `--kill` would otherwise orphan a live CLI behind the runner it
# just terminated.
_LIVE: set[subprocess.Popen] = set()


def terminate_live() -> int:
    """Ask every open agent subprocess to stop. Returns how many were signalled."""
    open_now = list(_LIVE)
    for proc in open_now:
        try:
            proc.terminate()
        except (OSError, ValueError):  # already gone
            pass
    return len(open_now)


@dataclass(frozen=True)
class ToolEvent:
    kind: str  # "use" | "result"
    name: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentTurn:
    text: str = ""
    session_id: str | None = None
    exit_code: int = 0
    error: str | None = None
    duration_ms: int = 0
    num_turns: int = 0
    # None when the CLI reports no price at all (Codex) — never read that as $0.
    cost_usd: float | None = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    # The prompt the last message actually carried — the live context size, not a sum.
    context_tokens: int = 0
    tool_events: list[ToolEvent] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and self.error is None


class AgentSession(Protocol):
    """What the pipeline needs from a stage session, whichever CLI runs it."""

    stage: str
    session_id: str | None
    turns: int
    system_prompt: str

    def send(self, prompt: str) -> AgentTurn: ...


class CliSession:
    """One stage = one CLI session. The first send starts it; later sends resume it."""

    cli_name = "agent"
    # None inherits the runner's stdin; a CLI that waits on an open stdin sets DEVNULL.
    stdin: int | None = None

    def __init__(
        self,
        *,
        stage: str,
        cwd: Path,
        timeout_seconds: int = 1800,
        system_prompt: str = "",
        run_id: str = "",
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.stage = stage
        self.cwd = cwd
        # Stamped onto every child of this session, first turn and resumes alike.
        self.run_id = run_id
        # The role's identity. Set once per session and re-sent on every resume,
        # because each CLI process builds its own system prompt from scratch.
        self.system_prompt = system_prompt
        self.timeout_seconds = timeout_seconds
        self.session_id: str | None = None
        self.turns = 0
        self._on_event = on_event
        self._tool_started: dict[str, float] = {}
        self._turn_started = 0.0

    @property
    def binary(self) -> str:
        raise NotImplementedError

    def build_args(self, prompt: str, *, resume: bool) -> list[str]:
        raise NotImplementedError

    def build_env(self) -> dict[str, str]:
        """The user's own environment — PATH, credentials, CLI config — plus this
        run's identity. Set, never merely defaulted: a factory launched from inside
        another factory's child must not inherit that run's id."""
        return {**os.environ, RUN_ID_ENV: self.run_id, STAGE_ENV: self.stage}

    def send(self, prompt: str) -> AgentTurn:
        self._tool_started.clear()
        self._before_send()
        args = self.build_args(prompt, resume=self.session_id is not None)
        try:
            proc = subprocess.Popen(
                args,
                cwd=str(self.cwd),
                env=self.build_env(),
                stdin=self.stdin,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except (OSError, FileNotFoundError) as exc:
            raise AgentError(f"could not launch '{self.binary}': {exc}") from exc

        _LIVE.add(proc)
        stderr_chunks: list[str] = []
        drain = threading.Thread(target=_drain, args=(proc.stderr, stderr_chunks), daemon=True)
        drain.start()
        killer = threading.Timer(self.timeout_seconds, proc.kill)
        killer.start()

        turn = AgentTurn()
        self._start_turn(turn)
        assistant_text: list[str] = []
        self._turn_started = time.monotonic()
        try:
            for line in proc.stdout or ():
                event = _json_line(line)
                if event is not None:
                    self._consume(event, turn, assistant_text)
        finally:
            killer.cancel()
            proc.wait()
            drain.join(timeout=1)
            _LIVE.discard(proc)

        turn.exit_code = proc.returncode
        if not turn.text:
            turn.text = "".join(assistant_text)
        if proc.returncode != 0 and turn.error is None:
            detail = self._failure_detail(stderr_chunks)
            turn.error = f"{self.cli_name} exited with code {proc.returncode}: {detail[:500]}"
        if turn.session_id:
            self.session_id = turn.session_id
        self.turns += 1
        return turn

    def _before_send(self) -> None:
        """Last chance to resolve anything the argv needs; may raise AgentError."""

    def _start_turn(self, turn: AgentTurn) -> None:
        """Per-turn defaults a dialect needs before the first event arrives."""

    def _failure_detail(self, stderr_chunks: list[str]) -> str:
        return "".join(stderr_chunks).strip() or "no output"

    def _consume(self, event: dict[str, Any], turn: AgentTurn, assistant_text: list[str]) -> None:
        raise NotImplementedError

    def _tool_use(self, turn: AgentTurn, key: str, name: str, value: object) -> None:
        detail = {"input": _summarize_input(value)}
        turn.tool_events.append(ToolEvent("use", name, detail))
        self._tool_started[key] = time.monotonic()
        self._emit("tool_call", {"kind": "use", "name": name, **detail})

    def _tool_result(self, turn: AgentTurn, key: str, *, is_error: bool) -> None:
        detail = {"is_error": is_error}
        turn.tool_events.append(ToolEvent("result", "tool_result", detail))
        started = self._tool_started.pop(key, None)
        # duration_ms travels to the POST body only; the caller strips it
        # before the payload is written to the JSONL record.
        elapsed = {"duration_ms": int((time.monotonic() - started) * 1000)} if started else {}
        self._emit("tool_call", {"kind": "result", "name": "tool_result", **detail, **elapsed})

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self._on_event:
            self._on_event(event_type, payload)


class ClaudeSession(CliSession):
    """`claude -p --output-format stream-json`, resumed with `--resume <session_id>`."""

    cli_name = CLAUDE

    def __init__(
        self,
        *,
        stage: str,
        model: str,
        cwd: Path,
        disallowed_tools: tuple[str, ...] = (),
        claude_bin: str = "claude",
        timeout_seconds: int = 1800,
        system_prompt: str = "",
        run_id: str = "",
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        super().__init__(
            stage=stage,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            system_prompt=system_prompt,
            run_id=run_id,
            on_event=on_event,
        )
        self.model = model
        self.disallowed_tools = disallowed_tools
        self.claude_bin = claude_bin

    @property
    def binary(self) -> str:
        return self.claude_bin

    def build_args(self, prompt: str, *, resume: bool) -> list[str]:
        args = [
            self.claude_bin,
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",  # required for stream-json on -p
            "--model",
            self.model,
            "--permission-mode",
            "acceptEdits",
        ]
        if self.system_prompt:
            args += ["--append-system-prompt", self.system_prompt]
        if self.disallowed_tools:
            # Deny wins over the user's global auto-approve settings; the write
            # boundary itself is still enforced post-hoc by git.
            args += ["--disallowedTools", *self.disallowed_tools]
        # Never inherit the user's global MCP servers — they may expose write tools.
        args.append("--strict-mcp-config")
        if resume and self.session_id:
            args += ["--resume", self.session_id]
        return args

    def _consume(self, event: dict[str, Any], turn: AgentTurn, assistant_text: list[str]) -> None:
        session_id = event.get("session_id")
        if isinstance(session_id, str) and session_id:
            turn.session_id = session_id

        kind = event.get("type")
        if kind == "assistant":
            self._consume_assistant(event, turn, assistant_text)
        elif kind == "user":
            self._consume_tool_results(event, turn)
        elif kind == "result":
            self._consume_result(event, turn)

    def _consume_assistant(
        self, event: dict[str, Any], turn: AgentTurn, assistant_text: list[str]
    ) -> None:
        message = event.get("message")
        if not isinstance(message, dict):
            return
        for block in message.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                assistant_text.append(str(block.get("text") or ""))
            elif block.get("type") == "tool_use":
                name = str(block.get("name") or "tool")
                self._tool_use(turn, str(block.get("id") or ""), name, block.get("input"))
        usage = message.get("usage")
        if isinstance(usage, dict):
            prompt_tokens = (
                _int(usage.get("input_tokens"))
                + _int(usage.get("cache_read_input_tokens"))
                + _int(usage.get("cache_creation_input_tokens"))
            )
            turn.input_tokens += prompt_tokens
            turn.output_tokens += _int(usage.get("output_tokens"))
            # Newest message wins: its prompt IS the context, where the sum above is billing.
            turn.context_tokens = prompt_tokens or turn.context_tokens

    def _consume_tool_results(self, event: dict[str, Any], turn: AgentTurn) -> None:
        message = event.get("message")
        if not isinstance(message, dict):
            return
        for block in message.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                self._tool_result(
                    turn, str(block.get("tool_use_id") or ""), is_error=bool(block.get("is_error"))
                )

    def _consume_result(self, event: dict[str, Any], turn: AgentTurn) -> None:
        result = event.get("result")
        if isinstance(result, str):
            turn.text = result
        turn.duration_ms = _int(event.get("duration_ms"))
        turn.num_turns = _int(event.get("num_turns"))
        cost = event.get("total_cost_usd")
        turn.cost_usd = float(cost) if isinstance(cost, int | float) else 0.0
        usage = event.get("usage")
        if isinstance(usage, dict):
            # The result message carries session totals — prefer them.
            totals_in = (
                _int(usage.get("input_tokens"))
                + _int(usage.get("cache_read_input_tokens"))
                + _int(usage.get("cache_creation_input_tokens"))
            )
            turn.input_tokens = max(turn.input_tokens, totals_in)
            turn.output_tokens = max(turn.output_tokens, _int(usage.get("output_tokens")))
            if not turn.context_tokens:
                turn.context_tokens = totals_in
        if event.get("is_error"):
            turn.error = f"claude reported an error result: {str(result)[:300]}"


def _json_line(line: str) -> dict[str, Any] | None:
    """One stream line as an object, or None — non-JSON noise on stdout is not fatal."""
    line = line.strip()
    if not line:
        return None
    try:
        event = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    return event if isinstance(event, dict) else None


def _drain(stream: Any, into: list[str]) -> None:
    """Read stderr concurrently so a chatty CLI cannot deadlock on a full pipe."""
    if stream is None:
        return
    try:
        for line in stream:
            into.append(line)
    except (OSError, ValueError):
        pass


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _summarize_input(value: object, limit: int = 300) -> str:
    try:
        text = json.dumps(value, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return text[:limit]
