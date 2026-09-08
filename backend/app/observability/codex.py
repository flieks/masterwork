"""Codex observability: hook entries in `~/.codex/hooks.json`.

Codex reads hooks from `hooks.json` and from a `[hooks]` table in `config.toml`,
merging the two with a warning when both exist. Only the JSON file is ever
written here — `config.toml` holds the user's model, projects and MCP servers,
and a TOML rewrite would reorder and re-comment it. It is read once, for the
`[features] hooks = false` switch that would make every hook a no-op.

There is no spawn tool to match on: Codex reports its subagents through
`SubagentStart`/`SubagentStop`, so `PreToolUse` is not subscribed at all and
`PermissionRequest` stands in for Claude Code's permission `Notification`.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from app.observability.json_hooks import JsonHooksIntegration

EVENTS = [
    "SessionStart",
    "UserPromptSubmit",
    "PostToolUse",
    "PermissionRequest",
    "SubagentStart",
    "SubagentStop",
    "Stop",
    "Interrupt",
    "SessionEnd",
]


class CodexIntegration(JsonHooksIntegration):
    """Wires Codex's hooks to the ingest endpoint."""

    id = "codex"
    label = "Codex"
    events = EVENTS

    def __init__(
        self,
        *,
        settings_path: Path,
        hooks_dir: Path,
        forwarder: Path,
        ingest_url: str,
        media_dir: Path,
        config_toml: Path | None = None,
    ) -> None:
        super().__init__(
            settings_path=settings_path,
            hooks_dir=hooks_dir,
            forwarder=forwarder,
            ingest_url=ingest_url,
            media_dir=media_dir,
        )
        self._config_toml = config_toml or settings_path.with_name("config.toml")

    def _blocker(self) -> str | None:
        blocker = super()._blocker()
        if blocker:
            return blocker
        if self._hooks_switched_off():
            return (
                f"Hooks are switched off in {self._config_toml} ([features] hooks = false), so "
                "Codex would ignore anything written. Remove that line, then connect."
            )
        return None

    def _hooks_switched_off(self) -> bool:
        """Hooks are on by default; only an explicit `false` disables them. A
        config that will not parse is Codex's problem to report, not a blocker."""
        try:
            config = tomllib.loads(self._config_toml.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        features = config.get("features")
        return isinstance(features, dict) and features.get("hooks") is False
