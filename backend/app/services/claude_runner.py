"""Run the local ``claude -p`` CLI as an async subprocess.

The CLI gets read-only tools only and ``--output-format json``; a chat turn
``--resume``s the stored session id. The system prompt is passed on every call,
because a resumed ``claude -p`` drops the one it started with.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.agent_runner import AgentResult, AgentRunnerError, int_or_none, run_cli


class ClaudeRunner:
    """Thin async wrapper around the ``claude`` CLI."""

    agent_id = "claude"
    display_name = "Claude Code"

    def __init__(self, *, bin: str, model: str, timeout_seconds: int, cwd: Path) -> None:
        self._bin = bin
        self._model = model
        self._timeout = timeout_seconds
        self._cwd = cwd

    def _build_args(
        self, prompt: str, *, resume_session_id: str | None, system_prompt: str | None
    ) -> list[str]:
        args = [
            self._bin,
            "-p",
            prompt,
            "--model",
            self._model,
            "--output-format",
            "json",
            "--allowedTools",
            "Read",
            "Glob",
            "Grep",
            # Read-only must be enforced by DENY: the user's global settings
            # (e.g. permissions.defaultMode "auto") can auto-approve edit tools,
            # and --allowedTools only adds approvals. Deny always wins.
            "--disallowedTools",
            "Bash",
            "Edit",
            "MultiEdit",
            "Write",
            "NotebookEdit",
            "Task",
            # Don't load the user's global MCP servers (they may expose write tools).
            "--strict-mcp-config",
        ]
        if system_prompt:
            args += ["--append-system-prompt", system_prompt]
        if resume_session_id:
            args += ["--resume", resume_session_id]
        return args

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
        output = await run_cli(args, cwd=self._cwd, timeout_seconds=self._timeout, label="claude")
        if output.returncode != 0:
            detail = _error_result(output.stdout) or output.failure_detail()
            raise AgentRunnerError(f"claude exited with code {output.returncode}: {detail}")
        return self._parse(output.stdout)

    async def run_once(self, prompt: str) -> str:
        result = await self.run(prompt)
        return result.reply

    @staticmethod
    def _parse(stdout: str) -> AgentResult:
        try:
            data = json.loads(stdout)
        except (json.JSONDecodeError, ValueError) as exc:
            raise AgentRunnerError(f"claude output was not valid JSON: {stdout[:200]!r}") from exc
        if not isinstance(data, dict):
            raise AgentRunnerError("claude output was not a JSON object")
        reply = data.get("result")
        session_id = data.get("session_id")
        if not isinstance(reply, str) or not isinstance(session_id, str):
            raise AgentRunnerError("claude output missing 'result' or 'session_id'")
        return AgentResult(reply=reply, session_id=session_id, stats=_extract_stats(data))


def _error_result(stdout: str) -> str | None:
    """The CLI's own message from an `is_error` result (e.g. a 429 usage limit),
    so the user reads that instead of the whole JSON blob."""
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(data, dict) and data.get("is_error") and isinstance(data.get("result"), str):
        return str(data["result"])
    return None


def _extract_stats(data: dict[str, Any]) -> dict[str, Any]:
    """Pull run metadata from the CLI's result JSON; every field is optional."""
    raw_usage = data.get("usage")
    usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
    raw_models = data.get("modelUsage")
    model_usage: dict[str, Any] = raw_models if isinstance(raw_models, dict) else {}
    cost = data.get("total_cost_usd")
    return {
        "model": " + ".join(model_usage) or None,
        "duration_ms": int_or_none(data.get("duration_ms")),
        "num_turns": int_or_none(data.get("num_turns")),
        "cost_usd": cost if isinstance(cost, int | float) else None,
        "input_tokens": int_or_none(usage.get("input_tokens")),
        "output_tokens": int_or_none(usage.get("output_tokens")),
        "cache_read_tokens": int_or_none(usage.get("cache_read_input_tokens")),
        "cache_creation_tokens": int_or_none(usage.get("cache_creation_input_tokens")),
    }
