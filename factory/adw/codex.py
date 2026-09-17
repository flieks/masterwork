"""The Codex CLI dialect: `codex exec --json`, resumed with `codex exec resume <thread>`.

Codex has no per-tool allow/deny list, so a role's policy is its sandbox, derived
from the write boundary: `[]` runs `read-only`, anything else `workspace-write`
(writes confined to the repo). Its shell cannot be denied; the git boundary gate
still decides what a stage may change.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from adw.agent import CODEX, AgentError, AgentTurn, CliSession, _int

# Where the binary ships when it is not on PATH: the desktop apps bundle their own.
CODEX_FALLBACKS: tuple[Path, ...] = (
    Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
    Path("/Applications/Codex.app/Contents/Resources/codex"),
    Path.home() / ".local" / "bin" / "codex",
)

READ_ONLY_SANDBOX = "read-only"
WRITE_SANDBOX = "workspace-write"

# Tools the envelope contract never accounts for; `multi_agent` mirrors Claude's denied Task.
DISABLED_FEATURES = (
    "plugins",
    "apps",
    "image_generation",
    "browser_use",
    "computer_use",
    "multi_agent",
)

# The built-in roles were written for Claude Code; this squares them with what Codex has.
RUNNER_NOTE = """RUNNER NOTE (Codex CLI): where the text above says you have no shell or names the
Skill tool, read it for this CLI — a sandboxed shell exists, and a skill is loaded by
reading its SKILL.md (~/.codex/skills/<name>/ or ~/.agents/skills/<name>/) or by
mentioning $skill-name. The runner still executes the repo's checks and verifies every
write against git: never run git commands that move HEAD, branches or the index
(commit, reset, checkout, switch, stash, add)."""

# Codex item types mapped onto the tool names the telemetry already reads.
_TOOL_NAMES = {
    "command_execution": "Bash",
    "file_change": "Edit",
    "web_search": "WebSearch",
    "todo_list": "TodoWrite",
}
# Items that are conversation, not tool calls; `error` items are warnings.
_NOT_TOOLS = frozenset({"agent_message", "reasoning", "user_message", "error"})
_FAILED_STATUSES = frozenset({"failed", "declined"})
_STDIN_NOTICE = "Reading additional input from stdin"


def sandbox_for(read_only: bool) -> str:
    return READ_ONLY_SANDBOX if read_only else WRITE_SANDBOX


def resolve_codex_bin(configured: str | None = None) -> str:
    """PATH first, then the app bundles — the CLI is often installed only inside one."""
    wanted = (configured or "").strip() or "codex"
    if os.sep in wanted or wanted.startswith("~"):
        path = Path(wanted).expanduser()
        if _executable(path):
            return str(path)
        raise AgentError(f'codex_bin "{wanted}" is not an executable file')
    found = shutil.which(wanted)
    if found:
        return found
    tried = [f"'{wanted}' on PATH"]
    if wanted == "codex":
        for candidate in CODEX_FALLBACKS:
            if _executable(candidate):
                return str(candidate)
            tried.append(str(candidate))
    raise AgentError(
        f"could not find the Codex CLI (tried {', '.join(tried)}) — install it, or set "
        '"codex_bin" in factory.config.json'
    )


def _executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def toml_string(text: str) -> str:
    """A `-c key=value` value: JSON string escapes are valid TOML, bar a raw DEL."""
    return json.dumps(text, ensure_ascii=False).replace("\x7f", "\\u007f")


class CodexSession(CliSession):
    """One stage on `codex exec`; the thread id is the session id."""

    cli_name = CODEX
    # An open stdin makes `codex exec` wait for more prompt, forever.
    stdin = subprocess.DEVNULL

    def __init__(
        self,
        *,
        stage: str,
        model: str | None,
        cwd: Path,
        read_only: bool,
        codex_bin: str = "codex",
        reasoning_effort: str | None = None,
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
        # None omits -m, so the user's ~/.codex/config.toml default applies.
        self.model = model
        self.read_only = read_only
        self.codex_bin = codex_bin
        self.reasoning_effort = reasoning_effort
        self._seen_items: set[str] = set()
        self._last_error = ""

    @property
    def binary(self) -> str:
        return self.codex_bin

    @property
    def sandbox(self) -> str:
        return sandbox_for(self.read_only)

    def build_args(self, prompt: str, *, resume: bool) -> list[str]:
        resuming = bool(resume and self.session_id)
        args = [self.codex_bin, "exec"]
        if resuming:
            args += ["resume", str(self.session_id)]
        args += ["--json", "--skip-git-repo-check"]
        if not resuming:
            # resume refuses -C/-s: the thread keeps its sandbox, the process keeps cwd.
            args += ["-C", str(self.cwd), "-s", self.sandbox]
        if self.model:
            args += ["-m", self.model]
        for key, value in self.config_overrides():
            args += ["-c", f"{key}={value}"]
        for feature in DISABLED_FEATURES:
            args += ["--disable", feature]
        args += ["--", prompt]
        return args

    def config_overrides(self) -> list[tuple[str, str]]:
        # Re-sent every turn: each `codex exec` process builds its instructions afresh.
        instructions = "\n\n".join(part for part in (self.system_prompt, RUNNER_NOTE) if part)
        overrides = [("developer_instructions", toml_string(instructions))]
        if self.reasoning_effort:
            overrides.append(("model_reasoning_effort", toml_string(self.reasoning_effort)))
        overrides.append(("web_search", toml_string("disabled")))
        return overrides

    def _before_send(self) -> None:
        self.codex_bin = resolve_codex_bin(self.codex_bin)

    def _start_turn(self, turn: AgentTurn) -> None:
        turn.cost_usd = None  # Codex reports tokens, never a price
        self._seen_items.clear()
        self._last_error = ""

    def _failure_detail(self, stderr_chunks: list[str]) -> str:
        if self._last_error:
            return self._last_error
        lines = [
            line
            for line in "".join(stderr_chunks).splitlines()
            if line.strip() and not line.startswith(_STDIN_NOTICE)
        ]
        return "\n".join(lines).strip() or "no output"

    def _consume(self, event: dict[str, Any], turn: AgentTurn, assistant_text: list[str]) -> None:
        kind = event.get("type")
        if kind == "thread.started":
            thread_id = event.get("thread_id")
            if isinstance(thread_id, str) and thread_id:
                turn.session_id = thread_id
        elif kind in ("item.started", "item.updated", "item.completed"):
            item = event.get("item")
            if isinstance(item, dict):
                self._consume_item(str(kind), item, turn)
        elif kind == "turn.completed":
            self._consume_usage(event.get("usage"), turn)
            turn.num_turns += 1
            turn.duration_ms = self._elapsed_ms()
        elif kind == "turn.failed":
            turn.error = f"codex turn failed: {_error_message(event.get('error'))[:500]}"
            turn.duration_ms = self._elapsed_ms()
        elif kind == "error":
            # Also sent for retries the turn recovers from: only turn.failed or the exit fails it.
            self._last_error = _error_message(event)

    def _consume_item(self, kind: str, item: dict[str, Any], turn: AgentTurn) -> None:
        item_type = str(item.get("type") or "")
        if item_type == "agent_message":
            text = item.get("text")
            if kind == "item.completed" and isinstance(text, str):
                turn.text = text  # the last message is the reply
            return
        if not item_type or item_type in _NOT_TOOLS:
            return
        key = str(item.get("id") or f"anonymous-{len(turn.tool_events)}")
        if key not in self._seen_items:
            self._seen_items.add(key)
            self._tool_use(turn, key, _tool_name(item), _tool_input(item, self.cwd))
        if kind == "item.completed":
            self._tool_result(turn, key, is_error=_item_failed(item))

    @staticmethod
    def _consume_usage(usage: object, turn: AgentTurn) -> None:
        if not isinstance(usage, dict):
            return
        # input_tokens already includes the cached part, output_tokens the reasoning part.
        # Summed over the turn's requests, so it is billing — context_tokens stays unknown.
        turn.input_tokens += _int(usage.get("input_tokens"))
        turn.output_tokens += _int(usage.get("output_tokens"))

    def _elapsed_ms(self) -> int:
        return int((time.monotonic() - self._turn_started) * 1000)


def _tool_name(item: dict[str, Any]) -> str:
    item_type = str(item.get("type"))
    if item_type == "mcp_tool_call":
        return f"mcp__{item.get('server') or 'mcp'}__{item.get('tool') or 'tool'}"
    return _TOOL_NAMES.get(item_type, item_type)


def _tool_input(item: dict[str, Any], cwd: Path) -> object:
    item_type = item.get("type")
    if item_type == "command_execution":
        return {"command": item.get("command")}
    if item_type == "file_change":
        changes = [
            {"path": _relative(str(change.get("path") or ""), cwd), "kind": change.get("kind")}
            for change in item.get("changes") or []
            if isinstance(change, dict)
        ]
        return {"file_path": changes[0]["path"]} if len(changes) == 1 else {"changes": changes}
    if item_type == "mcp_tool_call":
        return {"arguments": item.get("arguments")}
    if item_type == "web_search":
        return {"query": item.get("query")}
    return {k: v for k, v in item.items() if k not in ("id", "type", "status")}


def _item_failed(item: dict[str, Any]) -> bool:
    exit_code = item.get("exit_code")
    return (
        item.get("status") in _FAILED_STATUSES
        or (isinstance(exit_code, int) and exit_code != 0)
        or bool(item.get("error"))
    )


def _relative(path: str, cwd: Path) -> str:
    try:
        return Path(path).resolve().relative_to(cwd.resolve()).as_posix()
    except (ValueError, OSError):
        return path


def _error_message(value: object) -> str:
    """The human sentence, unwrapped from the API error JSON Codex nests in `message`."""
    message = value.get("message") if isinstance(value, dict) else value
    text = str(message or "unknown error")
    try:
        inner = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return text
    if isinstance(inner, dict):
        nested = inner.get("error")
        if isinstance(nested, dict) and nested.get("message"):
            return str(nested["message"])
        if inner.get("message"):
            return str(inner["message"])
    return text
