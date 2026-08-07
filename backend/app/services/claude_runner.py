"""Run the local ``claude -p`` CLI as an async subprocess.

Uses the local Claude subscription (no API key). The CLI gets read-only tools
and runs with cwd ``~/.claude`` so it can inspect the user's installed skills
and subagents. On the first message of a session we inject app context and the
proposal-block protocol via ``--append-system-prompt``; later messages
``--resume`` the stored CLI session id.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

APP_SYSTEM_PROMPT = """\
You are the Masterwork assistant. You help the user manage the AI-coding \
assets installed globally on this machine: Claude Code skills (each at \
~/.claude/skills/<name>/SKILL.md) and subagents (each at ~/.claude/agents/<name>.md).

You have read-only tools (Read, Glob, Grep) and your working directory is \
~/.claude, so you may read any skill or agent file to answer questions or ground \
your suggestions. Always inspect the relevant files before proposing changes.

WHEN — and only when — you want to propose concrete file changes, end your reply \
with exactly one fenced code block whose info string is `proposal` containing JSON \
of this shape:

```proposal
{
  "summary": "one-line summary of the change",
  "changes": [
    {
      "path": "/absolute/path/to/file",
      "action": "update" | "create" | "delete",
      "new_content": "the full new file content, or null for a delete",
      "description": "what this change does"
    }
  ]
}
```

Rules for the proposal block:
- Include it ONLY when you are proposing edits the user can accept; for plain \
answers, questions, or discussion, do NOT include it.
- Use absolute paths under ~/.claude/skills or ~/.claude/agents.
- `new_content` must be the COMPLETE new file content (not a diff) for "update" \
and "create" — never null, never omitted, no placeholders. Only a "delete" \
takes null. The backend rejects a proposal whose content is missing.
- If you are asking WHETHER to make a change, do not emit the block yet; emit \
it only once it carries the ready-to-apply content.
- Emit at most one proposal block, as the very last thing in your reply.
The backend applies accepted changes itself; you never write files.
"""

ASSET_CHAT_INSTRUCTIONS = """\
This chat is scoped to ONE asset — the skill or agent shown below. Answer about \
that asset by default; the user is looking at it while chatting, so keep replies \
short and specific and skip restating what the file already says. Its current \
content is included below, but re-Read the file before proposing changes — the \
user may have edited it since. When you propose changes, target this asset's \
path unless the user clearly asks about another file.
"""

PROJECT_BLOCK_INSTRUCTIONS = """\
This chat is scoped to a PROJECT — a persistent workspace with a goal, a set of \
linked assets, and a Mermaid flow diagram describing how those assets work \
together. In ADDITION to the `proposal` block, you may propose updates to the \
project itself by ending your reply with a fenced code block whose info string \
is `project` containing JSON of this shape:

```project
{
  "name": "new project name, or null to leave unchanged",
  "goal": "new goal markdown, or null",
  "flow_mermaid": "valid mermaid source, or null",
  "asset_ids": ["claude:skill:foo", "claude:agent:bar"],
  "description": "one-line human-readable summary of the update"
}
```

Rules for the project block:
- `null` on any field means leave that field unchanged.
- `asset_ids`, when present, is the COMPLETE new list of linked assets (not a \
delta) — include every asset that should remain linked. Use `null` to leave the \
current links untouched.
- `flow_mermaid` must be valid Mermaid (e.g. `flowchart TD`) showing how the \
linked assets collaborate to serve the project goal.
- Emit the project block ONLY when proposing project changes. You MAY emit both \
a `proposal` block (file changes) and a `project` block in the same reply.
"""


class ClaudeRunnerError(Exception):
    """The CLI failed, timed out, or produced unparseable output."""


@dataclass(frozen=True)
class ClaudeResult:
    reply: str
    session_id: str
    # Run metadata reported by the CLI (model, duration_ms, tokens, cost_usd, …).
    stats: dict[str, Any] = field(default_factory=dict)


class ClaudeRunner:
    """Thin async wrapper around the ``claude`` CLI."""

    def __init__(
        self,
        *,
        bin: str,
        model: str,
        timeout_seconds: int,
        cwd: Path | None = None,
    ) -> None:
        self._bin = bin
        self._model = model
        self._timeout = timeout_seconds
        self._cwd = cwd or (Path.home() / ".claude")

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
    ) -> ClaudeResult:
        args = self._build_args(
            prompt, resume_session_id=resume_session_id, system_prompt=system_prompt
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=str(self._cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (OSError, FileNotFoundError) as exc:
            raise ClaudeRunnerError(f"could not launch '{self._bin}': {exc}") from exc

        try:
            stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
        except TimeoutError as exc:
            await self._kill(proc)
            raise ClaudeRunnerError(f"claude timed out after {self._timeout}s") from exc

        stdout = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
        stderr = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""

        if proc.returncode != 0:
            detail = stderr.strip() or stdout.strip() or "no output"
            raise ClaudeRunnerError(f"claude exited with code {proc.returncode}: {detail}")

        return self._parse(stdout)

    async def run_once(self, prompt: str) -> str:
        """One-shot invocation: no session resume, no stored session id, no
        system prompt. Returns just the reply text. Used for diagram generation.
        """
        result = await self.run(prompt)
        return result.reply

    @staticmethod
    async def _kill(proc: asyncio.subprocess.Process) -> None:
        try:
            proc.kill()
            await proc.wait()
        except (ProcessLookupError, OSError):
            pass

    @staticmethod
    def _parse(stdout: str) -> ClaudeResult:
        try:
            data = json.loads(stdout)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ClaudeRunnerError(f"claude output was not valid JSON: {stdout[:200]!r}") from exc
        if not isinstance(data, dict):
            raise ClaudeRunnerError("claude output was not a JSON object")
        reply = data.get("result")
        session_id = data.get("session_id")
        if not isinstance(reply, str) or not isinstance(session_id, str):
            raise ClaudeRunnerError("claude output missing 'result' or 'session_id'")
        return ClaudeResult(reply=reply, session_id=session_id, stats=_extract_stats(data))


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _extract_stats(data: dict[str, Any]) -> dict[str, Any]:
    """Pull run metadata from the CLI's result JSON; every field is optional."""
    raw_usage = data.get("usage")
    usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
    raw_models = data.get("modelUsage")
    model_usage: dict[str, Any] = raw_models if isinstance(raw_models, dict) else {}
    cost = data.get("total_cost_usd")
    return {
        "model": " + ".join(model_usage) or None,
        "duration_ms": _int_or_none(data.get("duration_ms")),
        "num_turns": _int_or_none(data.get("num_turns")),
        "cost_usd": cost if isinstance(cost, int | float) else None,
        "input_tokens": _int_or_none(usage.get("input_tokens")),
        "output_tokens": _int_or_none(usage.get("output_tokens")),
        "cache_read_tokens": _int_or_none(usage.get("cache_read_input_tokens")),
        "cache_creation_tokens": _int_or_none(usage.get("cache_creation_input_tokens")),
    }
