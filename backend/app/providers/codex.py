"""Codex provider — scans the skills Codex loads from its own home.

Root: ``<skills_root>/<name>/SKILL.md`` (``~/.codex/skills``). Codex ships its
built-in skills under a hidden ``.system`` dir there; hidden entries are skipped
because they are Codex's, not the user's. A dir that is a link into the generic
folder is skipped too — the generic provider scans the real files once.
Codex has no subagent-file format, so this provider yields skills only.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from app.providers.base import (
    ScannedAsset,
    SnapshotTree,
    iter_skill_dirs,
    resolve_within_roots,
    resolves_under,
    skill_dir_name,
)
from app.providers.claude import _KIND_SKILL, _safe_resolve, build_asset

AGENT_CODEX = "codex"


class CodexProvider:
    """Provider for skills in Codex's own skills dir."""

    name = "codex"

    def __init__(self, skills_root: Path, *, generic_root: Path | None = None) -> None:
        self._skills_root = skills_root
        self._generic_root = generic_root

    def roots(self) -> list[Path]:
        return [self._skills_root]

    def scan(self) -> Iterable[ScannedAsset]:
        for entry, disabled in iter_skill_dirs(self._skills_root, skip_hidden=True):
            if resolves_under(entry, self._generic_root):
                continue
            asset = build_asset(
                self.name,
                _KIND_SKILL,
                entry.name,
                entry / "SKILL.md",
                agents=() if disabled else (AGENT_CODEX,),
                disabled=disabled,
            )
            if asset is not None:
                yield asset

    def snapshot_tree(self, path: Path) -> SnapshotTree | None:
        """~/.codex is the user's own home, like ~/.claude: committed to only when
        they made it a repo themselves."""
        if resolve_within_roots(path, self.roots()) is None:
            return None
        return SnapshotTree(root=self._skills_root.parent)

    def asset_id_for_path(self, path: Path) -> str | None:
        resolved = _safe_resolve(path)
        root = _safe_resolve(self._skills_root)
        if resolved is None or root is None:
            return None
        skill = skill_dir_name(root, resolved)
        return None if skill is None else f"{self.name}:{_KIND_SKILL}:{skill[0]}"
