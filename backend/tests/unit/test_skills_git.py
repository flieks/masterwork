"""Best-effort git snapshots: commit when a repo exists, no-op otherwise."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from app.services.skills_git import commit_snapshot


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


async def test_commits_changes_when_repo_exists(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "SKILL.md").write_text("v1", encoding="utf-8")

    await commit_snapshot(tmp_path, "masterwork: apply suggestion: test")
    assert _git(tmp_path, "log", "--oneline").count("\n") == 0  # exactly one commit
    assert "apply suggestion: test" in _git(tmp_path, "log", "-1", "--format=%s")

    # Nothing changed → no second commit, and no error either.
    await commit_snapshot(tmp_path, "masterwork: empty")
    assert "apply suggestion: test" in _git(tmp_path, "log", "-1", "--format=%s")


async def test_no_repo_is_a_silent_noop(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    await commit_snapshot(tmp_path, "message")  # must not raise
    assert not (tmp_path / ".git").exists()


def test_sync_wrapper_not_needed() -> None:
    # commit_snapshot is async; ensure it is awaitable without a loop error.
    assert asyncio.iscoroutinefunction(commit_snapshot)
