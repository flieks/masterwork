"""Switch a skill off and on without deleting it.

Client-free leaf: pure filesystem work on the roots it is handed. Coding agents
read `<skills_root>/<name>/SKILL.md` one level deep and nothing else, so a
folder parked under `<skills_root>/.disabled/` is invisible to every one of
them while staying editable, diffable and one rename away from coming back.

A skill in one agent's own folder is a single rename. A generic skill is the
real folder under `~/.agents/skills` plus a symlink in each agent's folder, so
all of them move together: the real folder into the generic `.disabled/`, each
link into its agent's `.disabled/`, re-pointed at the folder's new home. Enable
reverses it, restoring exactly the links found parked — an agent that never
linked the skill does not gain a link on the way back.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from app.core.exceptions import InvalidSkillNameError, SkillToggleConflictError
from app.providers.base import DISABLED_DIR
from app.services.skill_install import SLUG_RE


@dataclass(frozen=True)
class ToggleOutcome:
    # Where the skill folder now is.
    target: Path
    # Agents whose link to a generic skill moved along with it.
    relinked_agents: tuple[str, ...]


def set_skill_enabled(
    name: str,
    *,
    enabled: bool,
    skills_root: Path,
    agent_roots: Mapping[str, Path] | None = None,
) -> ToggleOutcome:
    """Move `<skills_root>/<name>` into or out of `<skills_root>/.disabled/`.

    `agent_roots` (agent -> its skills dir) is given for a generic skill only;
    every link to the folder found in those dirs moves with it. Never
    overwrites: a destination that already exists is a conflict, checked for
    every path before anything moves. A failure halfway is rolled back, so the
    tree is either fully toggled or as it was.
    """
    if not SLUG_RE.match(name):
        raise InvalidSkillNameError(f"not a valid skill slug: {name!r}")
    source, target = _endpoints(skills_root, name, enabled)
    if not source.is_dir() or source.is_symlink():
        raise SkillToggleConflictError(f"{source} is not a skill folder of its own")
    if target.exists() or target.is_symlink():
        raise SkillToggleConflictError(f"{target} already exists; not overwriting it")

    # Links are found before the folder moves, while they still resolve to it.
    links: list[tuple[str, Path, Path]] = []
    for agent, root in (agent_roots or {}).items():
        old, new = _endpoints(root, name, enabled)
        if not old.is_symlink() or not _resolves_to(old, source):
            continue
        if new.exists() or new.is_symlink():
            raise SkillToggleConflictError(f"{new} already exists; not overwriting it")
        links.append((agent, old, new))

    undo: list[Callable[[], None]] = []
    try:
        target.parent.mkdir(exist_ok=True)
        os.rename(source, target)
        undo.append(lambda: os.rename(target, source))
        for _agent, old, new in links:
            new.parent.mkdir(exist_ok=True)
            new.symlink_to(target, target_is_directory=True)
            undo.append(new.unlink)
            old.unlink()
            undo.append(partial(old.symlink_to, source, target_is_directory=True))
    except BaseException:
        for step in reversed(undo):
            step()
        raise
    # An emptied .disabled/ is noise; leave it only if something is still parked there.
    if enabled:
        for parent in [source.parent, *(old.parent for _a, old, _n in links)]:
            _rmdir_if_empty(parent)
    return ToggleOutcome(target=target, relinked_agents=tuple(agent for agent, _o, _n in links))


def _endpoints(root: Path, name: str, enabled: bool) -> tuple[Path, Path]:
    """(where the entry is now, where it goes) for this direction."""
    live, parked = root / name, root / DISABLED_DIR / name
    return (parked, live) if enabled else (live, parked)


def _resolves_to(path: Path, target: Path) -> bool:
    try:
        return path.resolve() == target.resolve()
    except (OSError, RuntimeError):
        return False


def _rmdir_if_empty(path: Path) -> None:
    if path.name != DISABLED_DIR:
        return
    with contextlib.suppress(OSError):  # not empty, or already gone
        path.rmdir()
