"""Provider for the factory's vendor-neutral role store (``~/.masterwork/agents``).

The factory pipeline runs four roles — plan, build, review, document — whose
prompts used to be Python string literals. They now live on disk so masterwork's
own improvement machinery (edit, chat proposals, project links, simulations) can
work on them like any other asset:

    <store>/<role>/system.md   static identity            -> "<role>:system"
    <store>/<role>/user.md     per-turn task template     -> "<role>:user"
    <store>/<role>/role.json   model / writes / purpose   -> NOT an asset

One asset per editable file, because `update_asset` writes a whole file and every
LLM writer in this app emits prose: a role-as-one-asset would need a synthetic
multi-file envelope that chat and simulations would have to reproduce byte-exactly
or corrupt both halves at once.

`role.json` is read for its `model` and `purpose` but is never exposed as an
asset: it is machine config (including the `writes` boundary that decides what a
headless agent may touch), and the improvement loop only knows how to write
markdown.

The store may not exist yet — the factory seeds it on first run — so an absent
directory scans to zero assets, never an error.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.providers.base import ScannedAsset, SnapshotTree, resolve_within_roots
from app.providers.claude import _KIND_AGENT, _safe_resolve

# Roles are indexed under the "agent" kind: the store is literally a directory of
# agents, and reusing the kind keeps them inside the existing Agents UI, filters
# and usage rollups instead of widening the AssetKind enum.
_KIND = _KIND_AGENT

_CONFIG_FILE = "role.json"

# part -> filename, in list order.
_PARTS = {"system": "system.md", "user": "user.md"}

_PART_TITLE = {"system": "system prompt", "user": "task template"}
_PART_BLURB = {
    "system": "Static identity, sent as the system prompt on every turn.",
    "user": "Per-turn task; the runner fills its {{placeholders}}.",
}

# Role names become the first colon-separated half of an asset name, so they must
# not contain a colon (the id would stop round-tripping), a slash or a space.
# Lowercase only: on a case-insensitive filesystem "Plan" and "plan" are the same
# directory but would be two different ids. Anything else is skipped, not mangled.
_ROLE_NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


def is_valid_role_name(name: str) -> bool:
    return _ROLE_NAME.fullmatch(name) is not None


@dataclass(frozen=True)
class RoleConfig:
    """The two `role.json` fields the asset list needs. Never raises: a missing or
    malformed config just leaves the description and model empty."""

    model: str | None = None
    purpose: str = ""


def read_role_config(role_dir: Path) -> RoleConfig:
    try:
        data = json.loads((role_dir / _CONFIG_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return RoleConfig()
    if not isinstance(data, dict):
        return RoleConfig()
    model = data.get("model")
    purpose = data.get("purpose")
    return RoleConfig(
        model=model.strip() if isinstance(model, str) and model.strip() else None,
        purpose=purpose.strip() if isinstance(purpose, str) else "",
    )


class MasterworkRoleProvider:
    """Writable provider over the global role store."""

    name = "masterwork"

    def __init__(self, store_root: Path) -> None:
        self._store_root = store_root

    def roots(self) -> list[Path]:
        # Writable, unlike the plugin provider: the whole store, so a proposal can
        # also create a role that does not exist yet.
        return [self._store_root]

    def snapshot_tree(self, path: Path) -> SnapshotTree | None:
        """The store itself is the repo — deliberately not its parent.

        `~/.masterwork` also holds `masterwork.db` and `runs/` (append-only
        telemetry, unbounded). Rooting the repo one level up and excluding them
        with an ignore file would make "the database is not in git" a rule that
        has to hold on every future write; rooting it here makes it
        unreachable — `git add -A` cannot see outside its own worktree.
        """
        if resolve_within_roots(path, [self._store_root]) is None:
            return None
        return SnapshotTree(root=self._store_root, may_create_repo=True)

    def scan(self) -> Iterable[ScannedAsset]:
        if not self._store_root.is_dir():
            return  # not seeded yet
        try:
            entries = sorted(self._store_root.iterdir())
        except OSError:
            return
        for entry in entries:
            if not entry.is_dir() or not is_valid_role_name(entry.name):
                continue
            config = read_role_config(entry)
            for part, filename in _PARTS.items():
                path = entry / filename
                if not path.is_file():
                    continue
                asset = _build_role_asset(self.name, entry.name, part, path, config)
                if asset is not None:
                    yield asset

    def asset_id_for_path(self, path: Path) -> str | None:
        resolved = _safe_resolve(path)
        root = _safe_resolve(self._store_root)
        if resolved is None or root is None:
            return None
        role_dir = resolved.parent
        if role_dir.parent != root or not is_valid_role_name(role_dir.name):
            return None
        for part, filename in _PARTS.items():
            if resolved.name == filename:
                return f"{self.name}:{_KIND}:{role_dir.name}:{part}"
        return None  # role.json and anything else is not an asset


def _build_role_asset(
    provider: str, role: str, part: str, path: Path, config: RoleConfig
) -> ScannedAsset | None:
    """One role prompt file. Frontmatter is deliberately NOT parsed — the file is
    sent to the model verbatim, so a `---` block would be prompt text, not metadata.
    """
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        stat = path.stat()
    except OSError:
        return None
    blurb = _PART_BLURB[part]
    return ScannedAsset(
        provider=provider,
        kind=_KIND,
        name=f"{role}:{part}",
        title=f"{role} · {_PART_TITLE[part]}",
        description=f"{config.purpose} — {blurb}" if config.purpose else blurb,
        path=path,
        updated_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
        content=content,
        read_only=False,
        model=config.model,
    )
