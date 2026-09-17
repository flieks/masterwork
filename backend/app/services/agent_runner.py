"""The seam every assistant feature calls: one prompt in, one reply out, on
whichever coding-agent CLI the user picked.

Both CLIs use the local subscription (no API key), run read-only, and start in
the same working directory so their inspection runs look alike to the Sessions
rollups (see INSPECTION_CWDS in the coding service).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.config import Settings


class AgentRunnerError(Exception):
    """The CLI failed, timed out, or produced unparseable output."""


@dataclass(frozen=True)
class AgentResult:
    reply: str
    # The CLI's session (Claude) or thread (Codex) id; None when it reported none.
    session_id: str | None
    # model, duration_ms, num_turns, cost_usd, input/output/cache token counts.
    stats: dict[str, Any] = field(default_factory=dict)


class AgentRunner(Protocol):
    agent_id: str
    display_name: str

    async def run(
        self,
        prompt: str,
        *,
        resume_session_id: str | None = None,
        system_prompt: str | None = None,
    ) -> AgentResult: ...

    async def run_once(self, prompt: str) -> str:
        """No resume, no system prompt; just the reply text."""
        ...


def assistant_cwd_candidates(settings: Settings) -> tuple[Path, Path]:
    """~/.claude first (the assets live there), masterwork's own home otherwise."""
    return settings.claude_skills_root.parent, settings.masterwork_home


def assistant_cwd(settings: Settings) -> Path:
    preferred, fallback = assistant_cwd_candidates(settings)
    return preferred if preferred.is_dir() else fallback


@dataclass(frozen=True)
class CliOutput:
    returncode: int
    stdout: str
    stderr: str

    def failure_detail(self) -> str:
        return self.stderr.strip() or self.stdout.strip() or "no output"


async def run_cli(args: list[str], *, cwd: Path, timeout_seconds: int, label: str) -> CliOutput:
    """Run a CLI to completion; raise on launch failure or timeout (the process
    is killed). A nonzero exit is returned, since each CLI words its errors
    differently."""
    try:
        cwd.mkdir(parents=True, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(cwd),
            # An open stdin makes `codex exec` wait for more input forever.
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise AgentRunnerError(f"could not launch '{args[0]}': {exc}") from exc

    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except TimeoutError as exc:
        await _kill(proc)
        raise AgentRunnerError(f"{label} timed out after {timeout_seconds}s") from exc

    return CliOutput(
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=stdout_b.decode("utf-8", errors="replace") if stdout_b else "",
        stderr=stderr_b.decode("utf-8", errors="replace") if stderr_b else "",
    )


async def _kill(proc: asyncio.subprocess.Process) -> None:
    try:
        proc.kill()
        await proc.wait()
    except (ProcessLookupError, OSError):
        pass


def int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
