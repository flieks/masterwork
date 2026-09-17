"""Best-effort git snapshots of the writable asset trees.

Every write the API makes to an asset — a direct edit, an accepted proposal, an
applied simulation suggestion — is committed to the repo that owns the file, so
it stays diffable and revertible. The user's own homes (~/.claude, ~/.codex,
~/.agents) qualify only when they made them repos, and every commit there is
pathspec-scoped to the written asset's own folder or file: ~/.codex holds
auth.json and hundreds of MB of sqlite, and `git add -A` would sweep them in.
Masterwork's role store is its own repo and is committed whole.

Which tree owns a path is the *provider's* answer, never a constant here: that
is what stops a test writing to a temp tree from committing to the real one.

Best-effort throughout: no repo → silent no-op; a git failure must never fail
the write itself.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
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


async def commit_snapshot(root: Path, message: str, pathspecs: Sequence[str] | None = None) -> None:
    """`git add -A && git commit` in `root` if it is a git repo — limited to
    `pathspecs` when given, both when staging and when committing, so neither
    unrelated files nor anything the user had staged themselves ride along."""
    if not (root / ".git").is_dir():
        return
    if pathspecs is None:
        await _git(root, "add", "-A")
        # Exits non-zero when there is nothing to commit — that's fine.
        await _git(root, "commit", "-q", "-m", message)
        return
    known: list[str] = []
    for spec in dict.fromkeys(pathspecs):
        # Fails harmlessly when the path neither exists nor was ever tracked.
        await _git(root, "--literal-pathspecs", "add", "-A", "--", spec)
        if await _tracked(root, spec):
            known.append(spec)
    if known:
        # `commit -- paths` refuses a path git has never seen, hence the filter.
        await _git(root, "--literal-pathspecs", "commit", "-q", "-m", message, "--", *known)


async def _tracked(root: Path, spec: str) -> bool:
    """Is `spec` in the index or in HEAD (a staged deletion is only in HEAD)?"""
    for extra in (("--with-tree=HEAD",), ()):
        args = ("--literal-pathspecs", "ls-files", "--error-unmatch", *extra, "--", spec)
        if await _git(root, *args) == 0:
            return True
    return False


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


@dataclass
class _TreeWrite:
    tree: SnapshotTree
    # None = the whole tree; otherwise every asset unit written in it.
    pathspecs: list[str] | None = field(default_factory=list)


def _trees_for(providers: Iterable[Provider], paths: Iterable[Path]) -> list[_TreeWrite]:
    """The versioned trees that own these paths, each listed once with the
    asset units written in it. Every provider may claim a path: a skill link
    changes in its agent's tree while the folder it points at changes in another."""
    providers = list(providers)
    trees: dict[Path, _TreeWrite] = {}
    for path in paths:
        for provider in providers:
            tree = provider.snapshot_tree(path)
            if tree is None:
                continue
            write = trees.setdefault(tree.root, _TreeWrite(tree))
            if tree.pathspec is None:
                write.pathspecs = None
            elif write.pathspecs is not None:
                write.pathspecs.append(tree.pathspec)
    return list(trees.values())


async def prepare_snapshots(providers: Iterable[Provider], paths: Iterable[Path]) -> None:
    """Call BEFORE writing, so the write that follows is a commit of its own.

    A masterwork-owned tree that is not a repo yet becomes one now. Either way
    anything already pending is committed first: the factory seeds and rewrites
    the role store directly, and `git add -A` would otherwise fold that into the
    write's commit — a diff that shows two changes and a revert that undoes both.
    """
    for write in _trees_for(providers, paths):
        await _ensure_repo(write.tree, _BASELINE)
        # No-op when the tree (or the scoped part of it) is clean.
        await commit_snapshot(write.tree.root, _BASELINE, write.pathspecs)


async def snapshot_writes(
    providers: Iterable[Provider], paths: Iterable[Path], message: str
) -> None:
    """Call AFTER writing: commit every tree these paths landed in."""
    for write in _trees_for(providers, paths):
        # Repeated because the write itself may have created the store (a
        # proposal can add the first role to an empty ~/.masterwork). There is no
        # prior state to baseline in that case, so this write IS the first commit.
        await _ensure_repo(write.tree, message)
        await commit_snapshot(write.tree.root, message, write.pathspecs)
