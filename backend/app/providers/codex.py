"""Codex provider — scans the skills and custom agents Codex loads from its home.

Roots:
- skills: ``<skills_root>/<name>/SKILL.md`` (``~/.codex/skills``). Codex ships its
  built-in skills under a hidden ``.system`` dir there; hidden entries are skipped
  because they are Codex's, not the user's. A dir that is a link into the generic
  folder is skipped too — the generic provider scans the real files once.
- agents: ``<agents_root>/<name>.toml`` (``~/.codex/agents``), one custom agent per
  file with the required string keys ``name``, ``description`` and
  ``developer_instructions``; any other config key may sit beside them. The
  built-in agents (default, worker, explorer) have no file and are not assets.

A skill switched off by a ``[[skills.config]]`` entry in ``config.toml`` is
reported disabled, by ``codex-config`` — masterwork reads that file, never writes it.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.core.exceptions import InvalidAssetContentError
from app.providers.base import (
    DISABLED_BY_CODEX_CONFIG,
    AssetRef,
    ScannedAsset,
    SnapshotTree,
    file_times,
    iter_skill_dirs,
    resolves_under,
    skill_dir_name,
    user_tree_snapshot,
)
from app.providers.claude import _KIND_AGENT, _KIND_SKILL, _safe_resolve, build_asset
from app.providers.codex_config import read_codex_config

AGENT_CODEX = "codex"
AGENT_SUFFIX = ".toml"
# Codex refuses to load a custom agent without all three.
REQUIRED_AGENT_KEYS = ("name", "description", "developer_instructions")


def parse_agent_toml(content: str) -> dict[str, Any]:
    """The agent table, or raise InvalidAssetContentError naming what is wrong."""
    try:
        data = tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        raise InvalidAssetContentError(f"not valid TOML: {exc}") from exc
    missing = [key for key in REQUIRED_AGENT_KEYS if key not in data]
    if missing:
        raise InvalidAssetContentError(
            f"a Codex custom agent needs {', '.join(REQUIRED_AGENT_KEYS)}; missing: "
            + ", ".join(missing)
        )
    wrong = [key for key in REQUIRED_AGENT_KEYS if not isinstance(data[key], str)]
    if wrong:
        raise InvalidAssetContentError(f"must be strings: {', '.join(wrong)}")
    return data


class CodexProvider:
    """Provider for skills and custom agents in Codex's own home."""

    name = "codex"

    def __init__(
        self,
        skills_root: Path,
        *,
        agents_root: Path | None = None,
        generic_root: Path | None = None,
        config_file: Path | None = None,
    ) -> None:
        self._skills_root = skills_root
        # None means custom agents are not indexed (and not writable).
        self._agents_root = agents_root
        self._generic_root = generic_root
        self._config_file = config_file

    @property
    def agents_root(self) -> Path | None:
        return self._agents_root

    def roots(self) -> list[Path]:
        return [self._skills_root] + ([self._agents_root] if self._agents_root else [])

    def scan(self) -> Iterable[ScannedAsset]:
        config = read_codex_config(self._config_file)
        for entry, parked in self._skill_entries():
            skill_file = entry / "SKILL.md"
            off = not parked and config.skill_disabled(skill_file)
            asset = build_asset(
                self.name,
                _KIND_SKILL,
                entry.name,
                skill_file,
                agents=() if parked or off else (AGENT_CODEX,),
                disabled=parked or off,
                disabled_by=DISABLED_BY_CODEX_CONFIG if off else None,
            )
            if asset is not None:
                yield asset
        for path in self._agent_files():
            asset = _build_agent(self.name, path)
            if asset is not None:
                yield asset

    def asset_refs(self) -> Iterable[AssetRef]:
        for entry, _parked in self._skill_entries():
            yield AssetRef(self.name, _KIND_SKILL, entry.name)
        for path in self._agent_files():
            yield AssetRef(self.name, _KIND_AGENT, path.stem)

    def _skill_entries(self) -> Iterable[tuple[Path, bool]]:
        for entry, parked in iter_skill_dirs(self._skills_root, skip_hidden=True):
            if not resolves_under(entry, self._generic_root):
                yield entry, parked

    def _agent_files(self) -> Iterable[Path]:
        if self._agents_root is None or not self._agents_root.is_dir():
            return
        for entry in sorted(self._agents_root.iterdir()):
            if not entry.name.startswith(".") and entry.suffix == AGENT_SUFFIX and entry.is_file():
                yield entry

    def is_agent_file(self, path: Path) -> bool:
        """Does a write to `path` land on a custom agent file (existing or new)?"""
        root = _safe_resolve(self._agents_root) if self._agents_root else None
        resolved = _safe_resolve(path)
        return (
            root is not None
            and resolved is not None
            and resolved.parent == root
            and resolved.suffix == AGENT_SUFFIX
        )

    def snapshot_tree(self, path: Path) -> SnapshotTree | None:
        """~/.codex is the user's own home, like ~/.claude: committed to only when
        they made it a repo themselves, and then only the written asset."""
        return user_tree_snapshot(path, self.roots())

    def asset_id_for_path(self, path: Path) -> str | None:
        resolved = _safe_resolve(path)
        root = _safe_resolve(self._skills_root)
        if resolved is None or root is None:
            return None
        skill = skill_dir_name(root, resolved)
        if skill is not None:
            return f"{self.name}:{_KIND_SKILL}:{skill[0]}"
        if self.is_agent_file(resolved):
            return f"{self.name}:{_KIND_AGENT}:{resolved.stem}"
        return None


def _build_agent(provider: str, path: Path) -> ScannedAsset | None:
    """A custom agent file as an asset. A file that does not parse is still
    listed — under its file name — so it can be opened and fixed."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        updated_at, created_at = file_times(path)
    except OSError:
        return None
    try:
        data: dict[str, Any] = tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        data = {}
    return ScannedAsset(
        provider=provider,
        kind=_KIND_AGENT,
        name=path.stem,
        title=_text(data.get("name")) or path.stem,
        description=_text(data.get("description")) or "",
        path=path,
        updated_at=updated_at,
        content=content,
        model=_text(data.get("model")),
        created_at=created_at,
        agents=(AGENT_CODEX,),
    )


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
