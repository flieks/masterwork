"""Build the active set of providers from configuration."""

from __future__ import annotations

from app.config import Settings
from app.providers.base import Provider
from app.providers.claude import ClaudeProvider
from app.providers.claude_plugins import ClaudePluginProvider
from app.providers.codex import CodexProvider
from app.providers.generic import GenericSkillProvider
from app.providers.masterwork_roles import MasterworkRoleProvider


def build_providers(settings: Settings) -> list[Provider]:
    """Return every enabled provider. Add a line here to register Cursor once
    its `Provider` implementation exists.
    """
    generic_root = settings.generic_skills_root
    return [
        ClaudeProvider(
            skills_root=settings.claude_skills_root,
            agents_root=settings.claude_agents_root,
            generic_root=generic_root,
        ),
        ClaudePluginProvider(plugins_root=settings.claude_plugins_root),
        CodexProvider(skills_root=settings.codex_skills_root, generic_root=generic_root),
        GenericSkillProvider(
            skills_root=generic_root,
            agent_roots={
                "claude": settings.claude_skills_root,
                "codex": settings.codex_skills_root,
            },
        ),
        MasterworkRoleProvider(store_root=settings.masterwork_agents_root),
    ]
