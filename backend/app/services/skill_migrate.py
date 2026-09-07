"""Move one agent's skill into the generic folder and link it back.

Client-free leaf: pure filesystem work on the roots it is handed. The result is
one real copy under the generic root, the agent's own dir holding a symlink to
it, and a symlink in every other agent's dir that has no entry of that name yet
— so the skill lives on disk once and every agent still finds it.

The "generic format" is the Agent Skills SKILL.md: the same file, with `name`
required and equal to the folder. Claude-only frontmatter keys are kept, not
stripped — other agents ignore keys they do not know, while stripping
`disable-model-invocation` would silently change how Claude uses the skill.
They are reported so the UI can say which keys only Claude honours.
"""

from __future__ import annotations

import filecmp
import os
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from app.core.exceptions import (
    GenericSkillExistsError,
    InvalidSkillNameError,
    SkillFetchError,
)
from app.providers.claude import parse_frontmatter
from app.services.skill_install import SLUG_RE

# Frontmatter keys the Agent Skills spec defines; everything else is agent-specific.
GENERIC_KEYS = frozenset(
    {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
)
# Claude Code's own extensions, as documented for skills.
CLAUDE_ONLY_KEYS = frozenset(
    {
        "disable-model-invocation",
        "user-invocable",
        "argument-hint",
        "model",
        "context",
        "agent",
        "hooks",
        "paths",
    }
)

_NAME_LINE = re.compile(r"^name\s*:")


@dataclass(frozen=True)
class MigrationOutcome:
    target: Path
    # Agents whose skills dir now links to the generic copy.
    linked: tuple[str, ...]
    # Agents that already had an unrelated entry of this name; left alone.
    skipped: tuple[str, ...]
    claude_only_keys: tuple[str, ...] = field(default_factory=tuple)
    # True when `name:` was added or corrected to match the folder.
    name_rewritten: bool = False
    # The generic folder already held an identical copy: nothing was copied,
    # the source simply became a link to it.
    adopted: bool = False
    # A differing generic copy was thrown away in favour of the source.
    replaced_generic: bool = False


def normalize_frontmatter(content: str, slug: str) -> tuple[str, tuple[str, ...], bool]:
    """(content with `name: <slug>` guaranteed, Claude-only keys present, rewritten?).

    Edits the frontmatter textually rather than re-dumping the YAML, so nothing
    but the one `name` line can change: a folded `description: >` block, key
    order and comments all survive the move byte for byte.
    """
    meta = parse_frontmatter(content)
    claude_only = tuple(sorted(k for k in meta if k in CLAUDE_ONLY_KEYS))
    if meta.get("name") == slug:
        return content, claude_only, False

    lines = content.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        # No frontmatter at all: add the minimum the spec requires.
        return f"---\nname: {slug}\n---\n{content}", claude_only, True
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return f"---\nname: {slug}\n---\n{content}", claude_only, True
    for i in range(1, end):
        if _NAME_LINE.match(lines[i]):
            lines[i] = f"name: {slug}\n"
            return "".join(lines), claude_only, True
    lines.insert(1, f"name: {slug}\n")
    return "".join(lines), claude_only, True


def migrate_to_generic(
    name: str,
    *,
    source_root: Path,
    generic_root: Path,
    agent_roots: Mapping[str, Path],
    replace_generic: bool = False,
) -> MigrationOutcome:
    """Copy `<source_root>/<name>` to `<generic_root>/<name>`, replace the source
    dir with a symlink, and link the other agents' dirs. `agent_roots` maps agent
    name -> its skills dir, and must include the one `source_root` belongs to.

    A generic copy that already exists and is identical is adopted: nothing is
    copied, the source just becomes the link. One that differs is refused
    unless `replace_generic`, which throws it away in favour of the source —
    the caller must have asked for that explicitly.

    Order is copy, then swap, then link: a failure before the swap leaves the
    source untouched, and a failure after it leaves a complete generic copy.
    """
    if not SLUG_RE.match(name):
        raise InvalidSkillNameError(f"not a valid skill slug: {name!r}")
    source = source_root / name
    if source.is_symlink():
        raise GenericSkillExistsError(f"{name!r} is already a link, not a folder of its own")
    if not (source / "SKILL.md").is_file():
        raise SkillFetchError(f"{source} has no SKILL.md")
    target = generic_root / name

    content, claude_only, rewritten = normalize_frontmatter(
        (source / "SKILL.md").read_text(encoding="utf-8", errors="replace"), name
    )

    adopted = replaced = False
    if target.exists() or target.is_symlink():
        if target.is_dir() and not target.is_symlink() and _trees_identical(source, target):
            adopted = True
        elif not replace_generic:
            raise GenericSkillExistsError(
                f"~/.agents/skills already holds a {name!r} that differs from this one"
            )
        else:
            replaced = True

    if not adopted:
        generic_root.mkdir(parents=True, exist_ok=True)
        staging = generic_root / f".masterwork-migrate-{name}"
        old_aside = generic_root / f".masterwork-old-{name}"
        for leftover in (staging, old_aside):
            if leftover.is_symlink() or leftover.exists():
                _remove(leftover)
        try:
            shutil.copytree(source, staging, symlinks=True)
            (staging / "SKILL.md").write_text(content, encoding="utf-8")
            if replaced:
                os.replace(target, old_aside)  # aside, not deleted, until the swap succeeds
            os.replace(staging, target)
        except BaseException:
            if old_aside.exists() and not target.exists():
                os.replace(old_aside, target)
            raise
        finally:
            if staging.exists():
                shutil.rmtree(staging)
        if old_aside.is_symlink() or old_aside.exists():
            _remove(old_aside)

    # The source becomes a link: the agent keeps finding the skill, on disk once.
    shutil.rmtree(source)
    source.symlink_to(target, target_is_directory=True)

    linked: list[str] = []
    skipped: list[str] = []
    for agent, root in agent_roots.items():
        link = root / name
        if link.is_symlink() or link.exists():
            if _resolves_to(link, target):
                linked.append(agent)
            else:
                skipped.append(agent)
            continue
        root.mkdir(parents=True, exist_ok=True)
        link.symlink_to(target, target_is_directory=True)
        linked.append(agent)

    return MigrationOutcome(
        target=target,
        linked=tuple(linked),
        skipped=tuple(skipped),
        claude_only_keys=claude_only,
        name_rewritten=False if adopted else rewritten,
        adopted=adopted,
        replaced_generic=replaced,
    )


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    else:
        shutil.rmtree(path)


def _trees_identical(a: Path, b: Path) -> bool:
    """Same files, same bytes, all the way down."""
    cmp = filecmp.dircmp(a, b, shallow=False)
    if cmp.left_only or cmp.right_only or cmp.diff_files or cmp.funny_files:
        return False
    return all(_trees_identical(a / sub, b / sub) for sub in cmp.common_dirs)


def _resolves_to(path: Path, target: Path) -> bool:
    try:
        return path.resolve() == target.resolve()
    except (OSError, RuntimeError):
        return False
