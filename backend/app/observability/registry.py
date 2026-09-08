"""Build the active set of observability integrations from configuration."""

from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.observability.base import Integration
from app.observability.claude_code import ClaudeCodeIntegration
from app.observability.codex import CodexIntegration

FORWARDERS = Path(__file__).resolve().parent / "forwarders"


def build_integrations(settings: Settings) -> list[Integration]:
    """Return every agent masterwork knows how to record. A new agent is a new
    `Integration` and a line here — nothing else in the stack changes.
    """
    hooks_dir = settings.masterwork_home / "hooks"
    return [
        ClaudeCodeIntegration(
            settings_path=settings.claude_settings_file,
            hooks_dir=hooks_dir,
            forwarder=FORWARDERS / "claude_code.py",
            ingest_url=settings.ingest_url,
            media_dir=settings.masterwork_media_root,
        ),
        CodexIntegration(
            settings_path=settings.codex_hooks_file,
            hooks_dir=hooks_dir,
            forwarder=FORWARDERS / "codex.py",
            ingest_url=settings.ingest_url,
            media_dir=settings.masterwork_media_root,
            config_toml=settings.codex_config_file,
        ),
    ]
