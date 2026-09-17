"""Provider for the skills shipped by Codex plugins — read-only, like Claude's.

Plugins are installed and updated by Codex from their marketplace, so `roots()`
is empty and nothing here can be written through the API.

On-disk layout (verified against codex-cli 0.153):
    <plugins_root>/cache/<marketplace>/<plugin>/<version>/.codex-plugin/plugin.json
        {"name": ..., "skills": "./skills/"}
    <plugins_root>/cache/<marketplace>/<plugin>/<version>/skills/<name>/SKILL.md

Several versions of one plugin can sit side by side, and `latest` is often a
link to one of them. One is picked per plugin: the newest by modification time
of the (resolved) version folder, ties going to a real folder over a link and
then to the greater name — so the pick is stable across scans, and the asset's
`path` says which version it came from.

On/off lives in ``config.toml`` as ``[plugins."<plugin>@<marketplace>"] enabled``.
A plugin is reported enabled only when that entry says ``true``: the cache also
holds marketplace plugins that were never switched on. Hidden dirs (the
``.plugin-appserver`` beside ``cache``) are ignored.

Ids mirror the Claude plugin provider: ``codex-plugin:skill:<plugin>:<skill>``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.providers.base import DISABLED_BY_CODEX_CONFIG, AssetRef, ScannedAsset, SnapshotTree
from app.providers.claude import _KIND_SKILL, _safe_resolve, build_asset
from app.providers.codex_config import read_codex_config

PROVIDER_CODEX_PLUGIN = "codex-plugin"
_MANIFEST = Path(".codex-plugin") / "plugin.json"
_DEFAULT_SKILLS = "./skills/"


@dataclass(frozen=True)
class CodexPlugin:
    marketplace: str
    name: str
    # The chosen version folder, unresolved (so a `latest` link stays visible).
    version_dir: Path

    @property
    def key(self) -> str:
        return f"{self.name}@{self.marketplace}"


class CodexPluginProvider:
    """Read-only provider over the skills of cached Codex plugins."""

    name = PROVIDER_CODEX_PLUGIN

    def __init__(self, plugins_root: Path, *, config_file: Path | None = None) -> None:
        self._plugins_root = plugins_root
        self._config_file = config_file

    def roots(self) -> list[Path]:
        return []  # read-only: Codex owns the cache

    def snapshot_tree(self, path: Path) -> SnapshotTree | None:
        return None

    def plugins(self) -> list[CodexPlugin]:
        """One entry per (marketplace, plugin), with its picked version."""
        cache = self._plugins_root / "cache"
        found: list[CodexPlugin] = []
        for marketplace in _visible_dirs(cache):
            for plugin in _visible_dirs(marketplace):
                version = _pick_version(plugin)
                if version is not None:
                    found.append(CodexPlugin(marketplace.name, plugin.name, version))
        return found

    def _skill_files(self) -> Iterable[tuple[CodexPlugin, str, Path]]:
        seen: set[str] = set()
        for plugin in self.plugins():
            for skill_dir in _visible_dirs(_skills_dir(plugin.version_dir)):
                skill_file = skill_dir / "SKILL.md"
                name = f"{plugin.name}:{skill_dir.name}"
                # The same plugin from two marketplaces: first (sorted) one wins.
                if name in seen or not skill_file.is_file():
                    continue
                seen.add(name)
                yield plugin, name, skill_file

    def scan(self) -> Iterable[ScannedAsset]:
        config = read_codex_config(self._config_file)
        for plugin, name, skill_file in self._skill_files():
            off = config.plugins_enabled.get(plugin.key) is not True or config.skill_disabled(
                skill_file
            )
            asset = build_asset(
                self.name,
                _KIND_SKILL,
                name,
                skill_file,
                read_only=True,
                agents=() if off else ("codex",),
                disabled=off,
                disabled_by=DISABLED_BY_CODEX_CONFIG if off else None,
            )
            if asset is not None:
                yield asset

    def asset_refs(self) -> Iterable[AssetRef]:
        for _plugin, name, _file in self._skill_files():
            yield AssetRef(self.name, _KIND_SKILL, name)

    def asset_id_for_path(self, path: Path) -> str | None:
        resolved = _safe_resolve(path)
        if resolved is None:
            return None
        for _plugin, name, skill_file in self._skill_files():
            if _safe_resolve(skill_file) == resolved:
                return f"{self.name}:{_KIND_SKILL}:{name}"
        return None


def _visible_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return []
    return [e for e in entries if not e.name.startswith(".") and e.is_dir()]


def _pick_version(plugin_dir: Path) -> Path | None:
    """The newest version folder holding a plugin manifest, or None."""
    candidates: list[tuple[float, bool, str, Path]] = []
    for version in _visible_dirs(plugin_dir):
        if not (version / _MANIFEST).is_file():
            continue
        try:
            mtime = version.resolve().stat().st_mtime
        except (OSError, RuntimeError):
            continue
        candidates.append((mtime, not version.is_symlink(), version.name, version))
    return max(candidates)[3] if candidates else None


def _skills_dir(version_dir: Path) -> Path:
    """Where the manifest says the skills are, never outside the version folder."""
    try:
        data: Any = json.loads((version_dir / _MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    raw = data.get("skills") if isinstance(data, dict) else None
    relative = raw if isinstance(raw, str) and raw.strip() else _DEFAULT_SKILLS
    candidate = (version_dir / relative).resolve()
    if not candidate.is_relative_to(version_dir.resolve()):
        return version_dir / "skills"
    return candidate
