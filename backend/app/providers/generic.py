"""Provider for the cross-agent skill folder (``~/.agents/skills``).

This is the Agent Skills layout every coding agent can share: one real copy of
``<root>/<name>/SKILL.md``, and each agent's own skills dir (``~/.claude/skills``,
``~/.codex/skills``) holds a symlink to it. The provider owns the real files;
the per-agent providers skip the links. Each asset's ``agents`` names the agents
whose dir actually links to it, so the UI can say "generic, but Codex does not
see it yet" rather than assuming every generic skill reaches everyone.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from app.providers.base import ScannedAsset, SnapshotTree, resolve_within_roots
from app.providers.claude import _KIND_SKILL, _safe_resolve, build_asset

PROVIDER_GENERIC = "generic"


class GenericSkillProvider:
    """Provider for skills shared by every coding agent."""

    name = PROVIDER_GENERIC

    def __init__(self, skills_root: Path, *, agent_roots: Mapping[str, Path]) -> None:
        self._skills_root = skills_root
        # agent name -> that agent's own skills dir, where a link would live.
        self._agent_roots = dict(agent_roots)

    @property
    def skills_root(self) -> Path:
        return self._skills_root

    @property
    def agent_roots(self) -> dict[str, Path]:
        return dict(self._agent_roots)

    def roots(self) -> list[Path]:
        return [self._skills_root]

    def linked_agents(self, name: str) -> tuple[str, ...]:
        """Agents whose skills dir resolves ``<name>`` to this folder's copy."""
        target = _safe_resolve(self._skills_root / name)
        if target is None:
            return ()
        return tuple(
            agent
            for agent, root in self._agent_roots.items()
            if (root / name).exists() and _safe_resolve(root / name) == target
        )

    def scan(self) -> Iterable[ScannedAsset]:
        if not self._skills_root.is_dir():
            return
        for entry in sorted(self._skills_root.iterdir()):
            skill_file = entry / "SKILL.md"
            if entry.name.startswith(".") or not entry.is_dir() or not skill_file.is_file():
                continue
            asset = build_asset(
                self.name,
                _KIND_SKILL,
                entry.name,
                skill_file,
                agents=self.linked_agents(entry.name),
            )
            if asset is not None:
                yield asset

    def snapshot_tree(self, path: Path) -> SnapshotTree | None:
        """~/.agents is the user's home too: versioned only if they made it a repo."""
        if resolve_within_roots(path, self.roots()) is None:
            return None
        return SnapshotTree(root=self._skills_root.parent)

    def asset_id_for_path(self, path: Path) -> str | None:
        resolved = _safe_resolve(path)
        root = _safe_resolve(self._skills_root)
        if resolved is None or root is None or resolved.name != "SKILL.md":
            return None
        parent = resolved.parent
        if parent.parent == root and not parent.name.startswith("."):
            return f"{self.name}:{_KIND_SKILL}:{parent.name}"
        return None
