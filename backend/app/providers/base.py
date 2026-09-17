"""Provider abstraction over locally-installed AI-coding assets.

An asset lives on disk; nothing about it is stored in the DB. A provider knows
where its assets live (its *roots*) and how to scan them into `ScannedAsset`s.
Adding a new tool (Cursor, Codex, ...) is a new `Provider` implementation and
nothing else.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ScannedAsset:
    """One installed skill or agent, read from disk."""

    provider: str
    kind: str  # "skill" | "agent"
    name: str
    title: str
    description: str
    path: Path
    updated_at: datetime
    content: str
    read_only: bool = False  # e.g. plugin-provided assets managed by a marketplace
    model: str | None = None  # frontmatter `model:`; None means it inherits the session model
    # Filesystem birth time; None where the platform has none. See `file_times`.
    created_at: datetime | None = None
    # Coding agents that load this asset ("claude", "codex"). A generic skill lists
    # Codex (native) plus each agent whose skills dir links to it; a factory role none.
    agents: tuple[str, ...] = ()
    # Parked under `<skills_root>/.disabled/`, where no coding agent looks.
    disabled: bool = False
    # Why it is disabled: DISABLED_BY_FOLDER or DISABLED_BY_CODEX_CONFIG; None when enabled.
    disabled_by: str | None = None

    @property
    def id(self) -> str:
        return f"{self.provider}:{self.kind}:{self.name}"


# `disabled_by` values. The folder is masterwork's to move; config.toml is not.
DISABLED_BY_FOLDER = "folder"
DISABLED_BY_CODEX_CONFIG = "codex-config"


@dataclass(frozen=True)
class AssetRef:
    """An asset's identity without its content — what id resolution needs."""

    provider: str
    kind: str
    name: str

    @property
    def id(self) -> str:
        return f"{self.provider}:{self.kind}:{self.name}"


# A skill moved here is invisible to every coding agent, which only read one
# level deep — the reversible alternative to deleting it.
DISABLED_DIR = ".disabled"


def iter_skill_dirs(root: Path, *, skip_hidden: bool) -> Iterable[tuple[Path, bool]]:
    """(skill dir, disabled?) for every `<root>/<name>/SKILL.md`, then every
    `<root>/.disabled/<name>/SKILL.md`. The `.disabled` folder itself is never a
    skill; `skip_hidden` also drops the other dot-dirs (Codex's `.system`)."""
    if not root.is_dir():
        return
    for entry in sorted(root.iterdir()):
        if entry.name == DISABLED_DIR or (skip_hidden and entry.name.startswith(".")):
            continue
        if entry.is_dir() and (entry / "SKILL.md").is_file():
            yield entry, False
    disabled_root = root / DISABLED_DIR
    if not disabled_root.is_dir():
        return
    for entry in sorted(disabled_root.iterdir()):
        if not entry.name.startswith(".") and entry.is_dir() and (entry / "SKILL.md").is_file():
            yield entry, True


def skill_dir_name(root: Path, skill_file: Path) -> tuple[str, bool] | None:
    """(name, disabled?) when `skill_file` is `<root>/<name>/SKILL.md` or
    `<root>/.disabled/<name>/SKILL.md`; None otherwise. Both already resolved."""
    if skill_file.name != "SKILL.md":
        return None
    skill_dir = skill_file.parent
    if skill_dir.name.startswith("."):
        return None
    if skill_dir.parent == root:
        return skill_dir.name, False
    if skill_dir.parent == root / DISABLED_DIR:
        return skill_dir.name, True
    return None


def file_times(path: Path) -> tuple[datetime, datetime | None]:
    """(modified, created) for one asset file, from a single stat.

    `st_birthtime` is macOS and the BSDs; Linux's stat carries no birth time at
    all, and there the created half is **None rather than the mtime** — an asset
    edited yesterday would otherwise claim to have been written yesterday, and
    the field exists precisely to tell an old asset from a new one. A confidently
    wrong date is worse than an absent one.

    A birth time later than the mtime means the inode was replaced (a copy, a
    restore, a checkout) while the content is provably older, so the earlier of
    the two is reported: created after updated is a contradiction on its face.
    """
    stat = path.stat()
    updated = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
    birthtime = getattr(stat, "st_birthtime", None)
    if birthtime is None:
        return updated, None
    return updated, min(datetime.fromtimestamp(birthtime, tz=UTC), updated)


@dataclass(frozen=True)
class SnapshotTree:
    """A git-versioned asset tree — where a write is recorded so it can be
    diffed and reverted."""

    root: Path
    # True only for a tree masterwork created itself. ~/.claude is the user's
    # own home: masterwork commits there when *they* made it a repo, but never
    # turns it into one behind their back.
    may_create_repo: bool = False
    # The asset's own folder or file, relative to `root`, that a commit may
    # stage. None only for a masterwork-owned tree: a user's home repo holds
    # auth tokens and databases that `git add -A` must never reach.
    pathspec: str | None = None


@runtime_checkable
class Provider(Protocol):
    """A source of installed assets (e.g. Claude Code, Cursor, Codex)."""

    name: str

    def roots(self) -> list[Path]:
        """Directories where this provider's assets may be WRITTEN — used to
        validate asset updates and proposal file changes. Read-only providers
        return [] so their files can never be written through the API."""
        ...

    def scan(self) -> Iterable[ScannedAsset]:
        """Yield every asset currently on disk."""
        ...

    def asset_id_for_path(self, path: Path) -> str | None:
        """Map an absolute file path to an asset id, or None if it isn't one."""
        ...

    def snapshot_tree(self, path: Path) -> SnapshotTree | None:
        """The versioned tree that must record a write to `path`, or None when
        this provider does not own the path or its files are not versioned.

        Asking the provider (rather than reading a root out of config) is what
        keeps a write to a temp tree from being committed to the real one.
        """
        ...


@runtime_checkable
class IndexedProvider(Protocol):
    """A provider that can list its asset ids without reading every file."""

    name: str

    def asset_refs(self) -> Iterable[AssetRef]: ...


def user_tree_snapshot(path: Path, roots: Iterable[Path]) -> SnapshotTree | None:
    """The snapshot for a write under one asset root of a user-owned home.

    Scoped to the asset's own unit — `<root>/<name>` (or `.disabled/<name>`)
    for a skill folder, `<root>/<file>` for an agent file — so a commit can
    never stage anything else in that home, whatever its .gitignore says.

    A path counts when it resolves under a root, and also when it merely sits
    there: a skill link's entry changes in this tree while its target changes in
    another, and a scoped commit only sees what it is pointed at.
    """
    for root in roots:
        real_root = _real(root)
        resolved = resolve_within_roots(path, [root])
        candidates = [(resolved, real_root)] if resolved is not None else []
        lexical = Path(os.path.abspath(path))
        candidates += [(lexical, Path(os.path.abspath(root))), (lexical, real_root)]
        for candidate, base in candidates:
            if candidate is None or not candidate.is_relative_to(base):
                continue
            parts = candidate.relative_to(base).parts
            unit = parts[:2] if parts[:1] == (DISABLED_DIR,) else parts[:1]
            spec = Path(real_root.name, *unit).as_posix()
            return SnapshotTree(root=real_root.parent, pathspec=spec)
    return None


def _real(path: Path) -> Path:
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return path


def resolves_under(path: Path, root: Path | None) -> bool:
    """True when `path` (a symlink, typically) resolves to somewhere inside
    `root`. Lets an agent's own skills dir skip the entries that are links into
    the generic folder, so one skill is scanned once, by the provider that owns
    the real files."""
    if root is None:
        return False
    try:
        return path.resolve().is_relative_to(root.resolve())
    except (OSError, RuntimeError):
        return False


def resolve_within_roots(path: Path, roots: Iterable[Path]) -> Path | None:
    """Resolve `path` (following symlinks, normalizing `..`) and return it iff the
    real path lives inside one of `roots`. Returns None on any escape.

    Works for not-yet-existing paths (create actions): the tail is resolved
    lexically while existing parents/symlinks are followed.
    """
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        return None
    for root in roots:
        try:
            root_resolved = root.resolve()
        except (OSError, RuntimeError):
            continue
        if resolved == root_resolved or resolved.is_relative_to(root_resolved):
            return resolved
    return None
