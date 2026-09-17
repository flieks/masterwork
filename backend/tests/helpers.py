"""Test doubles: a fake agent runner and a tmp-rooted provider factory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.providers.base import Provider
from app.providers.claude import ClaudeProvider
from app.providers.claude_plugins import ClaudePluginProvider
from app.providers.codex import CodexProvider
from app.providers.codex_plugins import CodexPluginProvider
from app.providers.generic import GenericSkillProvider
from app.providers.masterwork_roles import MasterworkRoleProvider
from app.services.agent_runner import AgentResult, AgentRunnerError


class FakeRunner:
    """Stands in for an AgentRunner: returns a scripted reply (or a sequence of
    replies, one per call — the last repeats) or raises."""

    def __init__(
        self,
        *,
        reply: str | None = None,
        replies: list[str] | None = None,
        session_id: str | None = "fake-session",
        error: str | None = None,
        stats: dict[str, Any] | None = None,
        agent_id: str = "claude",
        display_name: str = "Claude Code",
    ) -> None:
        self.agent_id = agent_id
        self.display_name = display_name
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
    ) -> AgentResult:
        self.calls.append(
            {
                "prompt": prompt,
                "resume": resume_session_id,
                "system_prompt": system_prompt,
            }
        )
        if self.error is not None:
            raise AgentRunnerError(self.error)
        if self._queue:
            reply = self._queue.pop(0) if len(self._queue) > 1 else self._queue[0]
            return AgentResult(reply=reply, session_id=self.session_id, stats=self.stats)
        return AgentResult(reply=self.reply, session_id=self.session_id, stats=self.stats)

    async def run_once(self, prompt: str) -> str:
        result = await self.run(prompt)
        return result.reply


def providers_for(
    tree: tuple[Path, Path],
    plugins_root: Path | None = None,
    roles_root: Path | None = None,
    generic_root: Path | None = None,
    codex_root: Path | None = None,
    codex_agents_root: Path | None = None,
    codex_plugins_root: Path | None = None,
    codex_config: Path | None = None,
) -> list[Provider]:
    skills_root, agents_root = tree
    providers: list[Provider] = [
        ClaudeProvider(skills_root=skills_root, agents_root=agents_root, generic_root=generic_root)
    ]
    if plugins_root is not None:
        providers.append(ClaudePluginProvider(plugins_root=plugins_root))
    if roles_root is not None:
        providers.append(MasterworkRoleProvider(store_root=roles_root))
    if codex_root is not None:
        providers.append(
            CodexProvider(
                skills_root=codex_root,
                agents_root=codex_agents_root,
                generic_root=generic_root,
                config_file=codex_config,
            )
        )
    if codex_plugins_root is not None:
        providers.append(
            CodexPluginProvider(plugins_root=codex_plugins_root, config_file=codex_config)
        )
    if generic_root is not None:
        agent_roots = {"claude": skills_root}
        if codex_root is not None:
            agent_roots["codex"] = codex_root
        providers.append(
            GenericSkillProvider(
                skills_root=generic_root, agent_roots=agent_roots, codex_config_file=codex_config
            )
        )
    return providers
