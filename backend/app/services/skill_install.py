"""Write a fetched community skill onto disk under settings.claude_skills_root.

Client-free leaf — no HTTP, it takes an already-fetched
`skill_catalog.FetchedSkill`. Every write lands under the `skills_root` passed
in, never a path read from settings directly, and a failed write never leaves
a half-written skill folder: the whole tree is staged in a sibling directory
first, then swapped in with `os.replace`.
"""

from __future__ import annotations

import difflib
import hashlib
import os
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from app.core.exceptions import (
    InvalidSkillNameError,
    SkillAlreadyInstalledError,
    SkillFetchError,
    SkillLicenseRefusedError,
)
from app.services.skill_catalog import MAX_SKILL_BYTES, FetchedSkill

SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# anthropics/skills ships these four as document-conversion skills under an
# all-rights-reserved license that forbids extraction — refused before any fetch.
REFUSED_SOURCE_REPO = ("anthropics", "skills")
REFUSED_DOCUMENT_SKILLS = frozenset({"docx", "pdf", "pptx", "xlsx"})


def check_installable(owner: str, repo: str, skill: str) -> None:
    """Raise before any network call is made for a refused skill."""
    if (
        owner.lower(),
        repo.lower(),
    ) == REFUSED_SOURCE_REPO and skill.lower() in REFUSED_DOCUMENT_SKILLS:
        raise SkillLicenseRefusedError(
            f"{owner}/{repo}:{skill} carries an all-rights-reserved license and cannot be installed"
        )


def installed_slugs(skills_root: Path) -> set[str]:
    """Directory names already present under the skills root. Install keys on
    the directory name, so a slug collision is a real collision regardless of
    which repo the existing copy came from."""
    if not skills_root.is_dir():
        return set()
    return {entry.name for entry in skills_root.iterdir() if entry.is_dir()}


def read_installed_skill_md(slug: str, *, skills_root: Path) -> str | None:
    """The installed SKILL.md, or None when it is absent or unreadable — used to
    tell whether the copy on disk still matches the registry."""
    path = skills_root / slug / "SKILL.md"
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def is_installed(slug: str, *, skills_root: Path) -> bool:
    return (skills_root / slug).is_dir()


def _write_within(root: Path, relative_path: str, content: bytes) -> None:
    """Defense in depth behind fetch_skill's own escape check."""
    dest = (root / relative_path).resolve()
    if not dest.is_relative_to(root.resolve()):
        raise SkillFetchError(f"refusing to write outside the skill folder: {relative_path!r}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)


def install_skill(
    fetched: FetchedSkill, *, slug: str, skills_root: Path, overwrite: bool = False
) -> Path:
    if not SLUG_RE.match(slug):
        raise InvalidSkillNameError(f"not a valid skill slug: {slug!r}")

    # Defense in depth behind fetch_skill's own cap — install_skill never trusts
    # a FetchedSkill was necessarily built by it.
    total_bytes = len(fetched.skill_md.encode("utf-8")) + sum(len(f.content) for f in fetched.files)
    if total_bytes > MAX_SKILL_BYTES:
        raise SkillFetchError(f"skill exceeds {MAX_SKILL_BYTES} bytes")

    skills_root.mkdir(parents=True, exist_ok=True)
    target = skills_root / slug
    staging = skills_root / f".masterwork-install-{slug}"
    old_aside = skills_root / f".masterwork-old-{slug}"

    if target.exists():
        if not overwrite:
            raise SkillAlreadyInstalledError(f"{slug} is already installed")
        if old_aside.exists():
            shutil.rmtree(old_aside)
        os.replace(target, old_aside)  # moved aside, not deleted, until the write succeeds

    if staging.exists():
        shutil.rmtree(staging)  # leftover from a previous failed install

    try:
        staging.mkdir(parents=True)
        _write_within(staging, "SKILL.md", fetched.skill_md.encode("utf-8"))
        for file in fetched.files:
            _write_within(staging, file.relative_path, file.content)
        os.replace(staging, target)
    except BaseException:
        if old_aside.exists() and not target.exists():
            os.replace(old_aside, target)  # best-effort restore on a failed overwrite
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)

    if old_aside.exists():
        shutil.rmtree(old_aside)
    return target


def uninstall_skill(slug: str, *, skills_root: Path) -> None:
    """Remove the skill directory. Callers must confirm masterwork installed
    it (an `installed_skills` row) before calling this — see skills/service.py."""
    if not SLUG_RE.match(slug):
        raise InvalidSkillNameError(f"not a valid skill slug: {slug!r}")

    target = (skills_root / slug).resolve()
    if not target.is_relative_to(skills_root.resolve()):
        raise InvalidSkillNameError(f"refusing to remove a path outside the skills root: {slug!r}")
    if target.is_dir():
        shutil.rmtree(target)


# --- drift ----------------------------------------------------------------


class DriftStatus(StrEnum):
    """How the installed copy relates to what was installed and to upstream."""

    current = "current"
    edited_locally = "edited_locally"
    upstream_changed = "upstream_changed"
    diverged = "diverged"
    unknown_origin = "unknown_origin"


class FileChange(StrEnum):
    added = "added"
    removed = "removed"
    changed = "changed"


@dataclass(frozen=True)
class ChangedPath:
    path: str
    change: FileChange


def tree_hash(files: Mapping[str, bytes]) -> str:
    """sha256 over (sorted relative path, content) pairs — the same digest for
    a fetched skill and the folder it was written to, so a local edit shows
    without the network."""
    digest = hashlib.sha256()
    for relative_path in sorted(files):
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(files[relative_path])
        digest.update(b"\0")
    return digest.hexdigest()


def fetched_files(fetched: FetchedSkill) -> dict[str, bytes]:
    files = {f.relative_path: f.content for f in fetched.files}
    files["SKILL.md"] = fetched.skill_md.encode("utf-8")
    return files


def read_installed_files(slug: str, *, skills_root: Path) -> dict[str, bytes] | None:
    """Every regular file under the skill folder keyed by relative path, or
    None when the folder is missing. Symlinked files are read, not followed as
    trees — a generic skill reaches here through a folder link."""
    root = skills_root / slug
    if not root.is_dir():
        return None
    files: dict[str, bytes] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not (Path(dirpath) / d).is_symlink()]
        for filename in filenames:
            path = Path(dirpath) / filename
            if not path.is_file():
                continue
            try:
                files[path.relative_to(root).as_posix()] = path.read_bytes()
            except OSError:
                continue
    return files


def classify_drift(
    *,
    local_hash: str,
    upstream_hash: str,
    installed_hash: str | None,
    installed_sha: str | None,
    upstream_sha: str | None,
) -> DriftStatus:
    """Which side moved since the install.

    The install-time hash is the baseline for local edits; the install-time sha
    is the baseline for upstream, with a content comparison standing in when
    either sha is unknown. With no hash at all (a row older than the columns)
    only "the two copies agree" is knowable, so a difference is reported as
    diverged — the status that guards the update — since nothing can say whose
    change it is.
    """
    if installed_hash is None:
        return DriftStatus.current if local_hash == upstream_hash else DriftStatus.diverged
    local_edited = local_hash != installed_hash
    if installed_sha is not None and upstream_sha is not None:
        upstream_changed = upstream_sha != installed_sha
    else:
        upstream_changed = upstream_hash != installed_hash
    if local_edited and upstream_changed:
        return DriftStatus.diverged
    if local_edited:
        return DriftStatus.edited_locally
    if upstream_changed:
        return DriftStatus.upstream_changed
    return DriftStatus.current


def skill_md_diff(local: bytes | None, upstream: bytes) -> str:
    """Unified diff of the installed SKILL.md against upstream; "" when equal."""
    local_text = local.decode("utf-8", errors="replace") if local is not None else ""
    upstream_text = upstream.decode("utf-8", errors="replace")
    if local_text == upstream_text:
        return ""
    return "".join(
        difflib.unified_diff(
            local_text.splitlines(keepends=True),
            upstream_text.splitlines(keepends=True),
            fromfile="SKILL.md (installed)",
            tofile="SKILL.md (upstream)",
        )
    )


def changed_paths(local: Mapping[str, bytes], upstream: Mapping[str, bytes]) -> list[ChangedPath]:
    """Companion files that differ between the two trees, SKILL.md excluded
    (it gets a real diff instead), sorted by path."""
    changes: list[ChangedPath] = []
    for path in sorted(set(local) | set(upstream)):
        if path == "SKILL.md":
            continue
        if path not in local:
            changes.append(ChangedPath(path, FileChange.added))
        elif path not in upstream:
            changes.append(ChangedPath(path, FileChange.removed))
        elif local[path] != upstream[path]:
            changes.append(ChangedPath(path, FileChange.changed))
    return changes
