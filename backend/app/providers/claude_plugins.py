"""Provider for skills/agents shipped by installed Claude Code plugins.

Plugins are managed by their marketplace (install/update/remove via the CLI),
so their assets are exposed READ-ONLY: `roots()` is empty, which keeps every
write path (updateAsset, proposal file changes) from ever touching them.

On-disk layout (verified against Claude Code 2.1):
    ~/.claude/plugins/installed_plugins.json
        {"version": 2, "plugins": {"<plugin>@<marketplace>": [{"installPath": ...}]}}
    <installPath>/skills/<name>/SKILL.md
    <installPath>/agents/<name>.md          (ignore *.md.tmpl templates)

Asset names are prefixed with the plugin's short name, matching how Claude Code
surfaces them (e.g. skill "vercel:bootstrap"), so ids look like
"claude-plugin:skill:vercel:bootstrap" — note the extra colon in the name.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.providers.base import ScannedAsset, SnapshotTree
from app.providers.claude import _KIND_AGENT, _KIND_SKILL, build_asset

_MANIFEST = "installed_plugins.json"


class ClaudePluginProvider:
    """Read-only provider over the skills/agents of installed plugins."""

    name = "claude-plugin"

    def __init__(self, plugins_root: Path) -> None:
        self._plugins_root = plugins_root

    def roots(self) -> list[Path]:
        return []  # read-only: no writable roots

    def snapshot_tree(self, path: Path) -> SnapshotTree | None:
        return None  # nothing is written here, and the marketplace owns the files

    def _install_paths(self) -> list[tuple[str, Path]]:
        """(plugin short name, install path) for every installed plugin entry."""
        manifest = self._plugins_root / _MANIFEST
        try:
            data: Any = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        plugins = data.get("plugins") if isinstance(data, dict) else None
        if not isinstance(plugins, dict):
            return []
        found: list[tuple[str, Path]] = []
        for key, entries in plugins.items():
            if not isinstance(key, str) or not isinstance(entries, list):
                continue
            plugin = key.split("@", 1)[0]
            for entry in entries:
                if isinstance(entry, dict) and isinstance(entry.get("installPath"), str):
                    found.append((plugin, Path(entry["installPath"])))
        return found

    def scan(self) -> Iterable[ScannedAsset]:
        seen: set[tuple[str, str]] = set()
        for plugin, install_path in self._install_paths():
            for kind, path in _plugin_asset_files(install_path):
                name = f"{plugin}:{path.parent.name if kind == _KIND_SKILL else path.stem}"
                if (kind, name) in seen:
                    continue  # same plugin installed under several scopes
                asset = build_asset(self.name, kind, name, path, read_only=True)
                if asset is not None:
                    seen.add((kind, name))
                    yield asset

    def asset_id_for_path(self, path: Path) -> str | None:
        try:
            resolved = path.resolve()
        except (OSError, RuntimeError):
            return None
        for plugin, install_path in self._install_paths():
            try:
                root = install_path.resolve()
            except (OSError, RuntimeError):
                continue
            if resolved.name == "SKILL.md" and resolved.parent.parent == root / "skills":
                return f"{self.name}:{_KIND_SKILL}:{plugin}:{resolved.parent.name}"
            if resolved.suffix == ".md" and resolved.parent == root / "agents":
                return f"{self.name}:{_KIND_AGENT}:{plugin}:{resolved.stem}"
        return None


def _plugin_asset_files(install_path: Path) -> Iterable[tuple[str, Path]]:
    """Yield (kind, file) for every skill/agent file a plugin ships."""
    skills_dir = install_path / "skills"
    if skills_dir.is_dir():
        for entry in sorted(skills_dir.iterdir()):
            skill_file = entry / "SKILL.md"
            if entry.is_dir() and skill_file.is_file():
                yield _KIND_SKILL, skill_file
    agents_dir = install_path / "agents"
    if agents_dir.is_dir():
        for entry in sorted(agents_dir.iterdir()):
            if entry.is_file() and entry.suffix == ".md":
                yield _KIND_AGENT, entry
