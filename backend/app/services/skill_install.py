"""Write a fetched community skill onto disk, in Claude's, Codex's or the
generic skills folder.

Client-free leaf — no HTTP, it takes an already-fetched
`skill_catalog.FetchedSkill`. Every write lands under the roots passed in, never
a path read from settings directly, and a failed write never leaves a
half-written skill folder: the whole tree is staged in a sibling directory
first, then swapped in with `os.replace`.

A skill made generic is one real folder under `~/.agents/skills` plus a link in
each agent folder that needs one (Claude's; Codex loads the generic folder
itself). Updating one replaces the real folder in place and leaves the links
alone; uninstalling one removes the links the agent folders hold to it, then
the folder.
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
    SkillNotManagedError,
)
from app.providers.base import DISABLED_DIR
from app.providers.generic import NATIVE_GENERIC_AGENTS
from app.services.skill_catalog import MAX_SKILL_BYTES, FetchedSkill

SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# anthropics/skills ships these four as document-conversion skills under an
# all-rights-reserved license that forbids extraction — refused before any fetch.
REFUSED_SOURCE_REPO = ("anthropics", "skills")
REFUSED_DOCUMENT_SKILLS = frozenset({"docx", "pdf", "pptx", "xlsx"})


def validate_slug(slug: str) -> None:
    if not SLUG_RE.match(slug):
        raise InvalidSkillNameError(f"not a valid skill slug: {slug!r}")


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


def installed_dir(slug: str, *, skills_root: Path) -> Path | None:
    """Where the installed copy lives: the skills root, or parked under
    `.disabled/` when the user switched it off. None when it is in neither."""
    for candidate in (skills_root / slug, skills_root / DISABLED_DIR / slug):
        if candidate.is_dir():
            return candidate
    return None


def read_installed_skill_md(slug: str, *, skills_root: Path) -> str | None:
    """The installed SKILL.md, or None when it is absent or unreadable — used to
    tell whether the copy on disk still matches the registry."""
    folder = installed_dir(slug, skills_root=skills_root)
    if folder is None:
        return None
    path = folder / "SKILL.md"
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def is_installed(slug: str, *, skills_root: Path) -> bool:
    return installed_dir(slug, skills_root=skills_root) is not None


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
    # An update of a switched-off skill lands in its parked folder, not beside it.
    target = (
        installed_dir(slug, skills_root=skills_root) if overwrite else None
    ) or skills_root / slug
    if target.is_symlink():
        if not overwrite:
            raise SkillAlreadyInstalledError(f"{slug} is already installed")
        # A migrated skill: rewrite the real folder, keep the link.
        target = target.resolve()
    return write_skill_folder(fetched, slug=slug, folder=target, overwrite=overwrite)


def write_skill_folder(
    fetched: FetchedSkill, *, slug: str, folder: Path, overwrite: bool = False
) -> Path:
    """Stage the skill beside `folder` and swap it in. `folder` is the real
    directory — never a link, whose replacement would orphan what it points at."""
    if not SLUG_RE.match(slug):
        raise InvalidSkillNameError(f"not a valid skill slug: {slug!r}")
    if folder.is_symlink():
        raise SkillNotManagedError(f"{folder} is a link; refusing to replace it with a folder")

    # Defense in depth behind fetch_skill's own cap — install_skill never trusts
    # a FetchedSkill was necessarily built by it.
    total_bytes = len(fetched.skill_md.encode("utf-8")) + sum(len(f.content) for f in fetched.files)
    if total_bytes > MAX_SKILL_BYTES:
        raise SkillFetchError(f"skill exceeds {MAX_SKILL_BYTES} bytes")

    parent = folder.parent
    parent.mkdir(parents=True, exist_ok=True)
    target = folder
    # Dot-named, so no agent (and no provider scan) ever mistakes them for a skill.
    staging = parent / f".masterwork-install-{slug}"
    old_aside = parent / f".masterwork-old-{slug}"

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


# --- where a skill lives -----------------------------------------------------


class SkillTarget(StrEnum):
    """The three skills folders masterwork installs into; each value is also the
    provider name in the skill's asset id."""

    claude = "claude"
    codex = "codex"
    generic = "generic"


@dataclass(frozen=True)
class SkillRoots:
    claude: Path
    codex: Path
    generic: Path

    def root(self, target: SkillTarget) -> Path:
        return {
            SkillTarget.claude: self.claude,
            SkillTarget.codex: self.codex,
            SkillTarget.generic: self.generic,
        }[target]

    def agent_roots(self) -> dict[str, Path]:
        return {SkillTarget.claude.value: self.claude, SkillTarget.codex.value: self.codex}


@dataclass(frozen=True)
class SkillLocation:
    target: SkillTarget
    # `<root>/<slug>` or `<root>/.disabled/<slug>`; a link only when `outside` is set.
    entry: Path
    # The real folder the files are in.
    folder: Path
    # A link to somewhere that is none of the three folders — not masterwork's to write.
    outside: bool = False


# Generic first: once a skill is generic, the agents' entries are only links to it.
_LOOKUP_ORDER = (SkillTarget.generic, SkillTarget.claude, SkillTarget.codex)


def skill_locations(slug: str, roots: SkillRoots) -> list[SkillLocation]:
    """Every copy of `slug` across the three folders, generic first. An agent
    entry that links into the generic folder is that copy, not one of its own."""
    generic_real = _real(roots.generic)
    found: list[SkillLocation] = []
    for target in _LOOKUP_ORDER:
        entry = installed_dir(slug, skills_root=roots.root(target))
        if entry is None:
            continue
        if not entry.is_symlink():
            found.append(SkillLocation(target, entry, entry))
            continue
        real = _real(entry)
        if target is SkillTarget.generic or not real.is_relative_to(generic_real):
            found.append(SkillLocation(target, entry, real, outside=True))
    return found


def installed_anywhere(roots: SkillRoots) -> dict[str, list[SkillTarget]]:
    """slug -> the folders holding it, for badging a whole result page at once."""
    slugs: set[str] = set()
    for target in _LOOKUP_ORDER:
        root = roots.root(target)
        for parent in (root, root / DISABLED_DIR):
            slugs |= {name for name in installed_slugs(parent) if SLUG_RE.match(name)}
    return {
        slug: [loc.target for loc in locations]
        for slug in slugs
        if (locations := skill_locations(slug, roots))
    }


def primary_location(
    locations: list[SkillLocation], prefer: SkillTarget | None = None
) -> SkillLocation | None:
    """The copy an update or uninstall acts on: the preferred folder's when it
    holds one, else the first found."""
    for location in locations:
        if location.target is prefer:
            return location
    return locations[0] if locations else None


def ensure_managed(location: SkillLocation, slug: str) -> None:
    if location.outside:
        raise SkillNotManagedError(
            f"{location.entry} is a link to {location.folder}, outside the skills folders "
            f"masterwork manages; change {slug!r} there instead"
        )


def link_into_agents(slug: str, folder: Path, roots: SkillRoots) -> list[str]:
    """Link a generic skill into every non-native agent folder with no entry of
    that name yet, as migrating one does. Returns the agents linked."""
    linked: list[str] = []
    for agent, root in roots.agent_roots().items():
        link = root / slug
        if agent in NATIVE_GENERIC_AGENTS or link.exists() or link.is_symlink():
            continue
        root.mkdir(parents=True, exist_ok=True)
        link.symlink_to(folder, target_is_directory=True)
        linked.append(agent)
    return linked


def remove_location(slug: str, location: SkillLocation, roots: SkillRoots) -> list[Path]:
    """Delete one copy: first every link in the three folders (live or parked)
    that points at it, then the folder. Links elsewhere on disk cannot be found
    and are left dangling. Returns the links removed."""
    if not SLUG_RE.match(slug):
        raise InvalidSkillNameError(f"not a valid skill slug: {slug!r}")
    ensure_managed(location, slug)
    folder = _real(location.folder)
    root = _real(roots.root(location.target))
    if not folder.is_relative_to(root):
        raise InvalidSkillNameError(f"refusing to remove a path outside the skills root: {slug!r}")
    removed: list[Path] = []
    for target in _LOOKUP_ORDER:
        base = roots.root(target)
        for link in (base / slug, base / DISABLED_DIR / slug):
            if link.is_symlink() and _real(link) == folder:
                link.unlink()
                removed.append(link)
    shutil.rmtree(folder)
    return removed


def _real(path: Path) -> Path:
    try:
        return path.resolve()
    except (OSError, RuntimeError):
        return path


def uninstall_skill(slug: str, *, skills_root: Path) -> None:
    """Remove the skill directory. Callers must confirm masterwork installed
    it (an `installed_skills` row) before calling this — see skills/service.py."""
    if not SLUG_RE.match(slug):
        raise InvalidSkillNameError(f"not a valid skill slug: {slug!r}")

    folder = installed_dir(slug, skills_root=skills_root)
    if folder is None:
        return
    target = folder.resolve()
    if not target.is_relative_to(skills_root.resolve()):
        raise InvalidSkillNameError(f"refusing to remove a path outside the skills root: {slug!r}")
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
    root = installed_dir(slug, skills_root=skills_root)
    return None if root is None else read_folder_files(root)


def read_folder_files(root: Path) -> dict[str, bytes]:
    """`read_installed_files` for a folder already located."""
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
