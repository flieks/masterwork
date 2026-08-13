"""Build the active set of observability integrations from configuration."""

from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.observability.base import Integration
from app.observability.claude_code import ClaudeCodeIntegration

FORWARDERS = Path(__file__).resolve().parent / "forwarders"


def build_integrations(settings: Settings) -> list[Integration]:
    """Return every agent masterwork knows how to record. Add a line here once a
    Codex/Cursor `Integration` exists — nothing else in the stack changes.
    """
    return [
        ClaudeCodeIntegration(
            settings_path=settings.claude_settings_file,
            hooks_dir=settings.masterwork_home / "hooks",
            forwarder=FORWARDERS / "claude_code.py",
            ingest_url=settings.ingest_url,
            media_dir=settings.masterwork_media_root,
        )
    ]
