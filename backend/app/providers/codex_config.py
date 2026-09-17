"""Read-only view of the switches in Codex's ``~/.codex/config.toml``.

Masterwork never writes this file: it holds the user's model, projects and MCP
servers, and a TOML rewrite would reorder and re-comment it. Two tables matter
to the asset providers:

    [plugins."<plugin>@<marketplace>"]
    enabled = true | false

    [[skills.config]]
    path = "/abs/path/to/SKILL.md"
    enabled = false

A missing or unparseable file reads as "nothing configured" — reporting Codex's
own config errors is Codex's job, not a reason to hide assets.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CodexConfig:
    # Resolved SKILL.md paths a `[[skills.config]]` entry switches off.
    disabled_skill_paths: frozenset[Path] = field(default_factory=frozenset)
    # "<plugin>@<marketplace>" -> its `enabled` value, for entries that state one.
    plugins_enabled: dict[str, bool] = field(default_factory=dict)

    def skill_disabled(self, skill_file: Path) -> bool:
        return _real(skill_file) in self.disabled_skill_paths


def read_codex_config(path: Path | None) -> CodexConfig:
    if path is None:
        return CodexConfig()
    try:
        data: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return CodexConfig()
    return CodexConfig(
        disabled_skill_paths=frozenset(_disabled_skills(data)),
        plugins_enabled=_plugins(data),
    )


def _disabled_skills(data: dict[str, Any]) -> list[Path]:
    skills = data.get("skills")
    entries = skills.get("config") if isinstance(skills, dict) else None
    if not isinstance(entries, list):
        return []
    found: list[Path] = []
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("enabled") is not False:
            continue
        raw = entry.get("path")
        if isinstance(raw, str) and raw.strip():
            found.append(_real(Path(os.path.expanduser(raw.strip()))))
    return found


def _plugins(data: dict[str, Any]) -> dict[str, bool]:
    plugins = data.get("plugins")
    if not isinstance(plugins, dict):
        return {}
    return {
        key: table["enabled"]
        for key, table in plugins.items()
        if isinstance(table, dict) and isinstance(table.get("enabled"), bool)
    }


def _real(path: Path) -> Path:
    # Codex deduplicates links by their target, so a config path is compared resolved.
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return path
