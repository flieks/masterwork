"""Best-effort git snapshots of the writable asset trees.

Every write the API makes to an asset — a direct edit, an accepted proposal, an
applied simulation suggestion — is committed to the repo that owns the file, so
it stays diffable and revertible. Two trees qualify today: the user's ~/.claude
(skills/ + agents/ only, via its own .gitignore) and masterwork's role store,
whose prompts drive the factory pipeline and are just as rewritable by the
improvement loop.

Which tree owns a path is the *provider's* answer, never a constant here: that
is what stops a test writing to a temp tree from committing to the real one.

Best-effort throughout: no repo → silent no-op; a git failure must never fail
the write itself.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from pathlib import Path

from app.providers.base import Provider, SnapshotTree

logger = logging.getLogger(__name__)

# Commit subject for whatever was on disk before the API wrote anything.
_BASELINE = "masterwork: baseline snapshot"


async def _git(root: Path, *args: str) -> int | None:
    """Run one git command in `root`. Returns its exit code, or None if git
    could not be executed at all."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(root),
            *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        return await proc.wait()
    except OSError:
        logger.warning("git %s in %s failed", args[0] if args else "", root, exc_info=True)
        return None


async def commit_snapshot(root: Path, message: str) -> None:
    """`git add -A && git commit` in `root` if it is a git repo."""
    if not (root / ".git").is_dir():
        return
    await _git(root, "add", "-A")
    # Exits non-zero when there is nothing to commit — that's fine.
    await _git(root, "commit", "-q", "-m", message)


async def _create_repo(root: Path, message: str) -> None:
    """Turn a masterwork-owned tree into a snapshot repo, first commit and all."""
    if await _git(root, "init", "-q") != 0:
        return
    # Machine-made commits: a fresh machine may have no global git identity,
    # which would make every snapshot fail silently, and a signing config would
    # block the subprocess on a passphrase prompt.
    await _git(root, "config", "user.name", "masterwork")
    await _git(root, "config", "user.email", "masterwork@localhost")
    await _git(root, "config", "commit.gpgsign", "false")
    try:
        (root / ".gitignore").write_text(".DS_Store\n", encoding="utf-8")
    except OSError:
        logger.warning("could not write .gitignore in %s", root, exc_info=True)
    await commit_snapshot(root, message)


async def _ensure_repo(tree: SnapshotTree, message: str) -> None:
    if not tree.may_create_repo or not tree.root.is_dir():
        return  # the store is not seeded yet: nothing to version
    if not (tree.root / ".git").is_dir():
        await _create_repo(tree.root, message)


def _trees_for(providers: Iterable[Provider], paths: Iterable[Path]) -> list[SnapshotTree]:
    """The versioned trees that own these paths, each listed once."""
    trees: dict[Path, SnapshotTree] = {}
    for path in paths:
        for provider in providers:
            tree = provider.snapshot_tree(path)
            if tree is not None:
                trees.setdefault(tree.root, tree)
                break
    return list(trees.values())


async def prepare_snapshots(providers: Iterable[Provider], paths: Iterable[Path]) -> None:
    """Call BEFORE writing, so the write that follows is a commit of its own.

    A masterwork-owned tree that is not a repo yet becomes one now. Either way
    anything already pending is committed first: the factory seeds and rewrites
    the role store directly, and `git add -A` would otherwise fold that into the
    write's commit — a diff that shows two changes and a revert that undoes both.
    """
    for tree in _trees_for(providers, paths):
        await _ensure_repo(tree, _BASELINE)
        await commit_snapshot(tree.root, _BASELINE)  # no-op when the tree is clean


async def snapshot_writes(
    providers: Iterable[Provider], paths: Iterable[Path], message: str
) -> None:
    """Call AFTER writing: commit every tree these paths landed in."""
    for tree in _trees_for(providers, paths):
        # Repeated because the write itself may have created the store (a
        # proposal can add the first role to an empty ~/.masterwork). There is no
        # prior state to baseline in that case, so this write IS the first commit.
        await _ensure_repo(tree, message)
        await commit_snapshot(tree.root, message)
