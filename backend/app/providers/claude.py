"""Claude Code provider — scans globally installed skills and subagents.

Roots:
- skills: ``<skills_root>/<name>/SKILL.md``, plus the switched-off ones under
  ``<skills_root>/.disabled/<name>/SKILL.md`` that Claude Code never reads
- agents: ``<agents_root>/<name>.md``
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

from app.providers.base import (
    DISABLED_BY_FOLDER,
    AssetRef,
    ScannedAsset,
    SnapshotTree,
    file_times,
    iter_skill_dirs,
    resolves_under,
    skill_dir_name,
    user_tree_snapshot,
)

_KIND_SKILL = "skill"
_KIND_AGENT = "agent"
AGENT_CLAUDE = "claude"


def parse_frontmatter(content: str) -> dict[str, Any]:
    """Return the YAML frontmatter as a dict. Never raises: malformed or missing
    frontmatter yields an empty dict so a single bad file can't break the scan.
    """
    if not content.startswith("---"):
        return {}
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return {}
    block = "\n".join(lines[1:end])
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _meta_title(meta: dict[str, Any], fallback: str) -> str:
    for key in ("name", "title"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def _meta_description(meta: dict[str, Any]) -> str:
    value = meta.get("description")
    return value.strip() if isinstance(value, str) else ""


def _meta_model(meta: dict[str, Any]) -> str | None:
    value = meta.get("model")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def build_asset(
    provider: str,
    kind: str,
    name: str,
    path: Path,
    *,
    read_only: bool = False,
    agents: tuple[str, ...] = (),
    disabled: bool = False,
    disabled_by: str | None = None,
) -> ScannedAsset | None:
    """Read one asset file into a ScannedAsset; None if the file is unreadable."""
    try:
        content = _read(path)
        updated_at, created_at = file_times(path)
    except OSError:
        return None
    meta = parse_frontmatter(content)
    return ScannedAsset(
        provider=provider,
        kind=kind,
        name=name,
        title=_meta_title(meta, name),
        description=_meta_description(meta),
        path=path,
        updated_at=updated_at,
        content=content,
        read_only=read_only,
        model=_meta_model(meta),
        created_at=created_at,
        agents=agents,
        disabled=disabled,
        # A parked folder is the only way a skill is off unless the caller says otherwise.
        disabled_by=(disabled_by or DISABLED_BY_FOLDER) if disabled else None,
    )


class ClaudeProvider:
    """Provider for Claude Code skills and subagents."""

    name = "claude"

    def __init__(
        self, skills_root: Path, agents_root: Path, *, generic_root: Path | None = None
    ) -> None:
        self._skills_root = skills_root
        self._agents_root = agents_root
        # Skill dirs that are links into here belong to the generic provider.
        self._generic_root = generic_root

    def roots(self) -> list[Path]:
        return [self._skills_root, self._agents_root]

    def scan(self) -> Iterable[ScannedAsset]:
        yield from self._scan_skills()
        yield from self._scan_agents()

    def asset_refs(self) -> Iterable[AssetRef]:
        for entry, _disabled in self._skill_entries():
            yield AssetRef(self.name, _KIND_SKILL, entry.name)
        for entry in self._agent_files():
            yield AssetRef(self.name, _KIND_AGENT, entry.stem)

    def _skill_entries(self) -> Iterable[tuple[Path, bool]]:
        for entry, disabled in iter_skill_dirs(self._skills_root, skip_hidden=False):
            if not resolves_under(entry, self._generic_root):
                yield entry, disabled

    def _agent_files(self) -> Iterable[Path]:
        if not self._agents_root.is_dir():
            return
        for entry in sorted(self._agents_root.iterdir()):
            if entry.is_file() and entry.suffix == ".md":
                yield entry

    def _scan_skills(self) -> Iterable[ScannedAsset]:
        for entry, disabled in self._skill_entries():
            # A disabled skill is loaded by nobody, so it claims no agent.
            asset = build_asset(
                self.name,
                _KIND_SKILL,
                entry.name,
                entry / "SKILL.md",
                agents=() if disabled else (AGENT_CLAUDE,),
                disabled=disabled,
            )
            if asset is not None:
                yield asset

    def _scan_agents(self) -> Iterable[ScannedAsset]:
        for entry in self._agent_files():
            asset = build_asset(self.name, _KIND_AGENT, entry.stem, entry, agents=(AGENT_CLAUDE,))
            if asset is not None:
                yield asset

    def snapshot_tree(self, path: Path) -> SnapshotTree | None:
        """~/.claude, where the repo sits when the user made one — scoped to the
        written asset, so its .gitignore is no longer the only thing between a
        commit and the rest of that home."""
        return user_tree_snapshot(path, self.roots())

    def asset_id_for_path(self, path: Path) -> str | None:
        """Map a resolved absolute path back to an asset id, if it is one."""
        try:
            resolved = path.resolve()
        except (OSError, RuntimeError):
            return None
        skills_root = _safe_resolve(self._skills_root)
        agents_root = _safe_resolve(self._agents_root)
        if skills_root is not None:
            skill = skill_dir_name(skills_root, resolved)
            if skill is not None:
                return f"{self.name}:{_KIND_SKILL}:{skill[0]}"
        if agents_root is not None and resolved.suffix == ".md" and resolved.parent == agents_root:
            return f"{self.name}:{_KIND_AGENT}:{resolved.stem}"
        return None


def _safe_resolve(path: Path) -> Path | None:
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return None
