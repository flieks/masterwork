"""Provider abstraction over locally-installed AI-coding assets.

An asset lives on disk; nothing about it is stored in the DB. A provider knows
where its assets live (its *roots*) and how to scan them into `ScannedAsset`s.
Adding a new tool (Cursor, Codex, ...) is a new `Provider` implementation and
nothing else.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
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

    @property
    def id(self) -> str:
        return f"{self.provider}:{self.kind}:{self.name}"


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
