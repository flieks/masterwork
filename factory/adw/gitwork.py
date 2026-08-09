"""Ground truth for every agent claim: the real git tree, never the agent's word."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# Run logs are the runner's own output, never a stage's work.
RUNNER_PATHS = ("factory/runs/",)


class GitError(Exception):
    """A git command failed."""


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


def changed_paths(repo: Path, snap: Snapshot) -> list[str]:
    """Paths this stage touched: diff vs the pre-stage sha, plus new untracked files."""
    paths: set[str] = set()
    if snap.sha:
        diff = git(repo, "diff", "--name-only", "-z", snap.sha)
        paths |= {p for p in diff.split("\0") if p}
    paths |= _porcelain_paths(repo)
    # Anything already dirty before the stage is not this stage's doing.
    return sorted(p for p in paths - snap.dirty if not p.startswith(RUNNER_PATHS))


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


def commit(repo: Path, message: str) -> str | None:
    """Commit everything in the tree; None when there was nothing to commit."""
    if not has_changes(repo):
        return None
    git(repo, "add", "-A")
    # --no-verify: an unattended run must not be blocked or prompted by a hook.
    git(repo, "commit", "--no-verify", "-q", "-m", message)
    return head_sha(repo)
