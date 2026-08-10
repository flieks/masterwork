"""Provider abstraction over locally-installed AI-coding assets.

An asset lives on disk; nothing about it is stored in the DB. A provider knows
where its assets live (its *roots*) and how to scan them into `ScannedAsset`s.
Adding a new tool (Cursor, Codex, ...) is a new `Provider` implementation and
nothing else.
"""

from __future__ import annotations

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

    @property
    def id(self) -> str:
        return f"{self.provider}:{self.kind}:{self.name}"


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
