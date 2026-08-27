"""Write a fetched community skill onto disk under settings.claude_skills_root.

Client-free leaf — no HTTP, it takes an already-fetched
`skill_catalog.FetchedSkill`. Every write lands under the `skills_root` passed
in, never a path read from settings directly, and a failed write never leaves
a half-written skill folder: the whole tree is staged in a sibling directory
first, then swapped in with `os.replace`.
"""

from __future__ import annotations

import os
import re
import shutil
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
