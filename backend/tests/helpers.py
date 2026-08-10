"""Test doubles: a fake claude runner and a tmp-rooted provider factory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.providers.base import Provider
from app.providers.claude import ClaudeProvider
from app.providers.claude_plugins import ClaudePluginProvider
from app.providers.masterwork_roles import MasterworkRoleProvider
from app.services.claude_runner import ClaudeResult, ClaudeRunnerError


class FakeRunner:
    """Stands in for ClaudeRunner: returns a scripted reply (or a sequence of
    replies, one per call — the last repeats) or raises."""

    def __init__(
        self,
        *,
        reply: str | None = None,
        replies: list[str] | None = None,
        session_id: str = "fake-session",
        error: str | None = None,
        stats: dict[str, Any] | None = None,
    ) -> None:
        self.reply = reply or ""
        self._queue = list(replies or [])
        self.session_id = session_id
        self.error = error
        self.stats = stats or {}
        self.calls: list[dict[str, Any]] = []

    async def run(
        self,
        prompt: str,
        *,
        resume_session_id: str | None = None,
        system_prompt: str | None = None,
    ) -> ClaudeResult:
        self.calls.append(
            {
                "prompt": prompt,
                "resume": resume_session_id,
                "system_prompt": system_prompt,
            }
        )
        if self.error is not None:
            raise ClaudeRunnerError(self.error)
        if self._queue:
            reply = self._queue.pop(0) if len(self._queue) > 1 else self._queue[0]
            return ClaudeResult(reply=reply, session_id=self.session_id, stats=self.stats)
        return ClaudeResult(reply=self.reply, session_id=self.session_id, stats=self.stats)

    async def run_once(self, prompt: str) -> str:
        result = await self.run(prompt)
        return result.reply


def providers_for(
    tree: tuple[Path, Path],
    plugins_root: Path | None = None,
    roles_root: Path | None = None,
) -> list[Provider]:
    skills_root, agents_root = tree
    providers: list[Provider] = [ClaudeProvider(skills_root=skills_root, agents_root=agents_root)]
    if plugins_root is not None:
        providers.append(ClaudePluginProvider(plugins_root=plugins_root))
    if roles_root is not None:
        providers.append(MasterworkRoleProvider(store_root=roles_root))
    return providers
