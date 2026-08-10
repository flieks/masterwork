"""Build the active set of providers from configuration."""

from __future__ import annotations

from app.config import Settings
from app.providers.base import Provider
from app.providers.claude import ClaudeProvider
from app.providers.claude_plugins import ClaudePluginProvider
from app.providers.masterwork_roles import MasterworkRoleProvider


def build_providers(settings: Settings) -> list[Provider]:
    """Return every enabled provider. Add a line here to register Cursor/Codex
    once their `Provider` implementation exists.
    """
    return [
        ClaudeProvider(
            skills_root=settings.claude_skills_root,
            agents_root=settings.claude_agents_root,
        ),
        ClaudePluginProvider(plugins_root=settings.claude_plugins_root),
        MasterworkRoleProvider(store_root=settings.masterwork_agents_root),
    ]
