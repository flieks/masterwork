"""Run the local ``codex exec`` CLI as an async subprocess.

Read-only sandbox, web search and the tool families that could act outside the
sandbox switched off, JSONL on stdout. A chat turn resumes the stored thread
with ``codex exec resume``; the system prompt rides along as
``developer_instructions`` on every call that has one.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from app.services.agent_runner import AgentResult, AgentRunnerError, int_or_none, run_cli

# Feature families that reach past a read-only file inspection.
_DISABLED_FEATURES = (
    "plugins",
    "apps",
    "image_generation",
    "browser_use",
    "computer_use",
    "multi_agent",
)


def toml_string(value: str) -> str:
    """A TOML basic string for a `-c key=value` override. Raw UTF-8 rather than
    \\u escapes (JSON escapes astral characters as surrogate pairs, which TOML
    rejects); DEL is the one raw character JSON leaves that TOML forbids."""
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


class CodexRunner:
    """Thin async wrapper around the ``codex`` CLI."""

    agent_id = "codex"
    display_name = "Codex"

    def __init__(
        self,
        *,
        bin: str,
        model: str | None,
        timeout_seconds: int,
        cwd: Path,
        reasoning_effort: str | None = None,
    ) -> None:
        self._bin = bin
        self._model = model
        self._timeout = timeout_seconds
        self._cwd = cwd
        self._effort = reasoning_effort

    def _build_args(
        self, prompt: str, *, resume_session_id: str | None, system_prompt: str | None
    ) -> list[str]:
        options = ["--json", "--skip-git-repo-check"]
        if self._model:
            options += ["-m", self._model]
        config = ["web_search=" + toml_string("disabled")]
        if resume_session_id:
            # `resume` takes no -s; the thread keeps its sandbox, and this pins it anyway.
            config.append("sandbox_mode=" + toml_string("read-only"))
        if self._effort:
            config.append("model_reasoning_effort=" + toml_string(self._effort))
        if system_prompt:
            config.append("developer_instructions=" + toml_string(system_prompt))
        for override in config:
            options += ["-c", override]
        for feature in _DISABLED_FEATURES:
            options += ["--disable", feature]
        # `--` so a prompt that starts with a dash is never read as a flag.
        if resume_session_id:
            return [self._bin, "exec", "resume", resume_session_id, *options, "--", prompt]
        return [self._bin, "exec", *options, "-s", "read-only", "--", prompt]

    async def run(
        self,
        prompt: str,
        *,
        resume_session_id: str | None = None,
        system_prompt: str | None = None,
    ) -> AgentResult:
        args = self._build_args(
            prompt, resume_session_id=resume_session_id, system_prompt=system_prompt
        )
        started = time.monotonic()
        output = await run_cli(args, cwd=self._cwd, timeout_seconds=self._timeout, label="codex")
        duration_ms = int((time.monotonic() - started) * 1000)
        parsed = _parse_events(output.stdout)
        if output.returncode != 0:
            detail = "; ".join(parsed.errors) or output.failure_detail()
            raise AgentRunnerError(f"codex exited with code {output.returncode}: {detail}")
        if parsed.failed:
            raise AgentRunnerError(f"codex failed: {'; '.join(parsed.errors)}")
        if parsed.reply is None:
            detail = "; ".join(parsed.errors) or "no agent message in its output"
            raise AgentRunnerError(f"codex returned no reply: {detail}")
        stats = {
            "model": self._model,
            "duration_ms": duration_ms,
            "num_turns": parsed.turns,
            "cost_usd": None,  # codex reports no price
            "input_tokens": parsed.usage.get("input_tokens"),
            "output_tokens": parsed.usage.get("output_tokens"),
            "cache_read_tokens": parsed.usage.get("cached_input_tokens"),
            "cache_creation_tokens": parsed.usage.get("cache_write_input_tokens"),
        }
        return AgentResult(reply=parsed.reply, session_id=parsed.thread_id, stats=stats)

    async def run_once(self, prompt: str) -> str:
        result = await self.run(prompt)
        return result.reply


class _Events:
    def __init__(self) -> None:
        self.thread_id: str | None = None
        self.reply: str | None = None
        self.turns = 0
        self.usage: dict[str, int] = {}
        self.errors: list[str] = []
        self.failed = False
        self.completed = False


def _error_message(event: dict[str, Any]) -> str:
    message = event.get("message")
    error = event.get("error")
    if not isinstance(message, str) and isinstance(error, dict):
        message = error.get("message")
    if not isinstance(message, str) and isinstance(error, str):
        message = error
    return message if isinstance(message, str) and message else json.dumps(event)[:300]


def _parse_events(stdout: str) -> _Events:
    """Fold the JSONL stream; lines that are not JSON objects are skipped."""
    parsed = _Events()
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "thread.started" and isinstance(event.get("thread_id"), str):
            parsed.thread_id = event["thread_id"]
        elif kind == "item.completed":
            item = event.get("item")
            if (
                isinstance(item, dict)
                and item.get("type") == "agent_message"
                and isinstance(item.get("text"), str)
            ):
                parsed.reply = item["text"]  # the last one is the answer
        elif kind == "turn.completed":
            parsed.turns += 1
            parsed.completed = True
            usage = event.get("usage")
            if isinstance(usage, dict):
                for key, value in usage.items():
                    count = int_or_none(value)
                    if count is not None:
                        parsed.usage[key] = parsed.usage.get(key, 0) + count
        elif kind == "turn.failed":
            parsed.failed = True
            parsed.errors.append(_error_message(event))
        elif kind == "error":
            # Stream retries surface as `error` too; only a turn that never
            # completed makes one fatal.
            parsed.errors.append(_error_message(event))
    if parsed.errors and not parsed.completed:
        parsed.failed = True
    return parsed
