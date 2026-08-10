"""Ground truth for every agent claim: the real git tree, never the agent's word."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path


class GitError(Exception):
    """A git command failed."""


class BranchError(GitError):
    """The run branch cannot be created without destroying something."""


def git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


def is_repo(path: Path) -> bool:
    proc = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=str(path),
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def head_sha(repo: Path) -> str | None:
    """None before the first commit."""
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo), capture_output=True, text=True, check=False
    )
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def _porcelain_paths(repo: Path) -> set[str]:
    """Every path git currently reports as dirty or untracked, repo-relative."""
    raw = git(repo, "status", "--porcelain", "-z", "--untracked-files=all")
    fields = [f for f in raw.split("\0") if f != ""]
    paths: set[str] = set()
    i = 0
    while i < len(fields):
        entry = fields[i]
        i += 1
        if len(entry) < 4:
            continue
        status, path = entry[:2], entry[3:]
        paths.add(path)
        # Rename/copy entries carry their source path in the NEXT field.
        if ("R" in status or "C" in status) and i < len(fields):
            paths.add(fields[i])
            i += 1
    return paths


@dataclass(frozen=True)
class Snapshot:
    sha: str | None
    dirty: frozenset[str] = field(default_factory=frozenset)


def snapshot(repo: Path) -> Snapshot:
    """Pre-stage tree state: HEAD plus whatever was already dirty."""
    return Snapshot(sha=head_sha(repo), dirty=frozenset(_porcelain_paths(repo)))


def changed_paths(repo: Path, snap: Snapshot, *, exclude: tuple[str, ...] = ()) -> list[str]:
    """Paths this stage touched: diff vs the pre-stage sha, plus new untracked files.

    `exclude` carries the runner's own run-log prefix, but only when `runs_dir` was
    pointed back inside the target repo — by default the logs live outside it.
    """
    paths: set[str] = set()
    if snap.sha:
        diff = git(repo, "diff", "--name-only", "-z", snap.sha)
        paths |= {p for p in diff.split("\0") if p}
    paths |= _porcelain_paths(repo)
    # Anything already dirty before the stage is not this stage's doing.
    return sorted(p for p in paths - snap.dirty if not (exclude and p.startswith(exclude)))


def _is_tracked(repo: Path, path: str) -> bool:
    proc = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", path],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def revert(repo: Path, paths: list[str]) -> list[str]:
    """Undo out-of-boundary work: restore tracked files, delete untracked ones."""
    reverted: list[str] = []
    for path in paths:
        target = repo / path
        if _is_tracked(repo, path):
            git(repo, "checkout", "HEAD", "--", path, check=False)
            reverted.append(path)
        elif target.is_file() or target.is_symlink():
            target.unlink()
            reverted.append(path)
        elif target.is_dir():
            continue
    return reverted


def has_changes(repo: Path) -> bool:
    return bool(_porcelain_paths(repo))


def dirty_paths(repo: Path) -> set[str]:
    """Everything git currently calls dirty or untracked, repo-relative."""
    return _porcelain_paths(repo)


def rev_parse(repo: Path, ref: str) -> str | None:
    """The sha a ref points at, or None when the ref does not resolve."""
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def is_ancestor(repo: Path, sha: str, ref: str) -> bool:
    """Whether `sha` is actually part of `ref`'s history — the one question that
    turns "a stage said it committed" into "the commit is on this branch"."""
    proc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", sha, ref],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


def commit(repo: Path, message: str) -> str | None:
    """Commit everything in the tree; None when there was nothing to commit."""
    if not has_changes(repo):
        return None
    git(repo, "add", "-A")
    # --no-verify: an unattended run must not be blocked or prompted by a hook.
    git(repo, "commit", "--no-verify", "-q", "-m", message)
    return head_sha(repo)


# --- the run branch --------------------------------------------------------


def current_branch(repo: Path) -> str | None:
    """The checked-out branch, including an unborn one; None on a detached HEAD."""
    proc = subprocess.run(
        ["git", "symbolic-ref", "--quiet", "--short", "HEAD"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def branch_exists(repo: Path, name: str) -> bool:
    proc = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{name}"],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode == 0


NO_BRANCH_NOTE = "--no-branch: the run commits onto the branch that was already checked out"
NO_COMMITS_NOTE = (
    "this repo has no commits yet, so there is no HEAD to branch from — "
    "the run commits onto the branch that was already checked out"
)


@dataclass(frozen=True)
class RunBranch:
    """Where the run works, and how to get back to where the user was."""

    name: str | None  # the branch this run created; None when none was created
    origin: str  # the branch the run started from, or a sha on a detached HEAD
    detached: bool = False
    # Paths that were already dirty when the run started and came along on `checkout -b`.
    carried: tuple[str, ...] = ()
    note: str = ""  # why no branch was created, when `name` is None
    # True when this ref was picked up again by --resume rather than created here.
    resumed: bool = False

    @property
    def created(self) -> bool:
        return self.name is not None

    @property
    def return_command(self) -> str:
        """How the user gets back to exactly where they were before the run."""
        return f"git checkout {self.origin}"


def start_run_branch(repo: Path, name: str | None) -> RunBranch:
    """Create `name` from HEAD and switch to it, uncommitted work and all.

    `git checkout -b` never rewrites a ref and never discards a working tree, so the
    branch that was checked out keeps exactly the history and the files it had.
    """
    origin = current_branch(repo)
    if name is None:
        return RunBranch(None, origin or head_sha(repo) or "HEAD", note=NO_BRANCH_NOTE)
    if head_sha(repo) is None:
        # An unborn HEAD has nothing to branch from, and an empty repo has no
        # history to protect. Said out loud rather than crashing on `checkout -b`.
        return RunBranch(None, origin or "HEAD", note=NO_COMMITS_NOTE)
    if branch_exists(repo, name):
        raise BranchError(
            f"branch '{name}' already exists — the factory never commits into a branch "
            f"it did not create. Pass --branch with another name, or --no-branch to "
            f"commit onto {origin or 'the current HEAD'} as before."
        )
    carried = tuple(sorted(_porcelain_paths(repo)))
    started_at = origin or head_sha(repo) or "HEAD"
    git(repo, "checkout", "-b", name)
    return RunBranch(name, started_at, detached=origin is None, carried=carried)


RESUME_NOTE = "picked up again by --resume; the branch already existed"


def resume_run_branch(repo: Path, ref: str, name: str | None, origin: str) -> RunBranch:
    """Get back onto the ref a previous run committed to — never create one.

    The caller has already verified the ref exists and still points where the run
    left it; all that is left is to be standing on it.
    """
    if not branch_exists(repo, ref):
        raise BranchError(f"branch '{ref}' does not exist — there is nothing to resume onto")
    if current_branch(repo) != ref:
        git(repo, "checkout", ref)
    return RunBranch(name, origin or ref, note=RESUME_NOTE, resumed=True)
