"""Provider for the cross-agent skill folder (``~/.agents/skills``).

This is the Agent Skills layout every coding agent can share: one real copy of
``<root>/<name>/SKILL.md``. Codex loads this folder itself; Claude Code reads only
``~/.claude/skills``, so it reaches a generic skill through a symlink there. The
provider owns the real files; the per-agent providers skip links into it. Each
asset's ``agents`` names the agents that actually load it, so the UI can say
"generic, but Claude does not see it yet" rather than assuming it reaches everyone.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from app.providers.base import (
    AssetRef,
    ScannedAsset,
    SnapshotTree,
    iter_skill_dirs,
    skill_dir_name,
    user_tree_snapshot,
)
from app.providers.claude import _KIND_SKILL, _safe_resolve, build_asset
from app.providers.codex_config import read_codex_config

PROVIDER_GENERIC = "generic"
# Agents that load ~/.agents/skills natively (Codex 0.153+); the rest need a link in their own dir.
NATIVE_GENERIC_AGENTS = frozenset({"codex"})


class GenericSkillProvider:
    """Provider for skills shared by every coding agent."""

    name = PROVIDER_GENERIC

    def __init__(
        self,
        skills_root: Path,
        *,
        agent_roots: Mapping[str, Path],
        codex_config_file: Path | None = None,
    ) -> None:
        self._skills_root = skills_root
        # agent name -> that agent's own skills dir, where a link would live.
        self._agent_roots = dict(agent_roots)
        # A `[[skills.config]]` entry there switches the skill off for Codex alone.
        self._codex_config_file = codex_config_file

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

    def loading_agents(self, name: str) -> tuple[str, ...]:
        """Agents that load ``<name>``: the native ones always, the rest only through a link."""
        linked = set(self.linked_agents(name))
        return tuple(a for a in self._agent_roots if a in NATIVE_GENERIC_AGENTS or a in linked)

    def scan(self) -> Iterable[ScannedAsset]:
        codex = read_codex_config(self._codex_config_file)
        for entry, disabled in iter_skill_dirs(self._skills_root, skip_hidden=True):
            agents = () if disabled else self.loading_agents(entry.name)
            if codex.skill_disabled(entry / "SKILL.md"):
                agents = tuple(agent for agent in agents if agent != "codex")
            # Disabled: the agents' links moved into their own .disabled/, so none loads it.
            asset = build_asset(
                self.name,
                _KIND_SKILL,
                entry.name,
                entry / "SKILL.md",
                agents=agents,
                disabled=disabled,
            )
            if asset is not None:
                yield asset

    def asset_refs(self) -> Iterable[AssetRef]:
        for entry, _disabled in iter_skill_dirs(self._skills_root, skip_hidden=True):
            yield AssetRef(self.name, _KIND_SKILL, entry.name)

    def snapshot_tree(self, path: Path) -> SnapshotTree | None:
        """~/.agents is the user's home too: versioned only if they made it a
        repo, and then only the written skill's folder is committed."""
        return user_tree_snapshot(path, self.roots())

    def asset_id_for_path(self, path: Path) -> str | None:
        resolved = _safe_resolve(path)
        root = _safe_resolve(self._skills_root)
        if resolved is None or root is None:
            return None
        skill = skill_dir_name(root, resolved)
        return None if skill is None else f"{self.name}:{_KIND_SKILL}:{skill[0]}"
