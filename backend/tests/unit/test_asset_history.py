"""Best-effort git snapshots: which tree owns a write, and when a repo is made.

The role store is masterwork's own, so the snapshot repo is created for it; the
user's ~/.claude is not, so it is only ever committed to when they made it a repo.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from app.providers.claude import ClaudeProvider
from app.providers.masterwork_roles import MasterworkRoleProvider
from app.services.asset_history import (
    commit_snapshot,
    prepare_snapshots,
    snapshot_writes,
)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _subjects(root: Path) -> list[str]:
    return _git(root, "log", "--format=%s").splitlines()


def _claude_tree(tmp_path: Path) -> tuple[ClaudeProvider, Path]:
    home = tmp_path / "claude-home"
    skills, agents = home / "skills", home / "agents"
    skills.mkdir(parents=True)
    agents.mkdir()
    return ClaudeProvider(skills_root=skills, agents_root=agents), home


def _role_store(tmp_path: Path) -> tuple[MasterworkRoleProvider, Path]:
    """A store inside a ~/.masterwork lookalike that also holds the db and runs."""
    home = tmp_path / "masterwork-home"
    store = home / "agents"
    (store / "plan").mkdir(parents=True)
    (store / "plan" / "system.md").write_text("seeded identity\n", encoding="utf-8")
    (home / "masterwork.db").write_bytes(b"SQLite format 3\x00")
    (home / "runs").mkdir()
    (home / "runs" / "run-1.jsonl").write_text('{"event":"big"}\n', encoding="utf-8")
    return MasterworkRoleProvider(store_root=store), store


# --- the primitive ------------------------------------------------------------


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


# --- the role store -----------------------------------------------------------


async def test_role_store_becomes_a_repo_and_baselines_before_the_write(tmp_path: Path) -> None:
    provider, store = _role_store(tmp_path)
    target = store / "plan" / "system.md"

    await prepare_snapshots([provider], [target])
    assert (store / ".git").is_dir()
    assert _subjects(store) == ["masterwork: baseline snapshot"]

    target.write_text("REWRITTEN BY A SUGGESTION\n", encoding="utf-8")
    await snapshot_writes([provider], [target], "masterwork: apply suggestion: sharpen plan")

    # The edit is its own commit, so it is a diff against the seeded prompt and
    # not an unreadable initial import.
    assert _subjects(store) == [
        "masterwork: apply suggestion: sharpen plan",
        "masterwork: baseline snapshot",
    ]
    diff = _git(store, "show", "--format=", "HEAD")
    assert "-seeded identity" in diff and "+REWRITTEN BY A SUGGESTION" in diff
    assert _git(store, "cat-file", "-p", "HEAD~1:plan/system.md") == "seeded identity"


async def test_database_and_run_logs_are_outside_the_repo(tmp_path: Path) -> None:
    provider, store = _role_store(tmp_path)
    target = store / "plan" / "system.md"

    await prepare_snapshots([provider], [target])
    target.write_text("v2\n", encoding="utf-8")
    await snapshot_writes([provider], [target], "masterwork: edit")

    assert sorted(_git(store, "ls-files").splitlines()) == [".gitignore", "plan/system.md"]
    # The repo is rooted at the store, so the db and the run logs are not merely
    # ignored — they are outside the worktree entirely.
    assert not (store.parent / ".git").exists()
    assert (store.parent / "masterwork.db").is_file()


async def test_absent_store_is_a_noop_and_initializes_on_the_first_write(tmp_path: Path) -> None:
    store = tmp_path / "masterwork-home" / "agents"  # not seeded yet
    provider = MasterworkRoleProvider(store_root=store)
    target = store / "build" / "user.md"

    await prepare_snapshots([provider], [target])  # must not raise, must not create
    assert not store.exists()

    target.parent.mkdir(parents=True)  # what a proposal's apply_change does
    target.write_text("Build it.\n", encoding="utf-8")
    await snapshot_writes([provider], [target], "masterwork: accept proposal: seed build")

    assert (store / ".git").is_dir()
    assert _subjects(store) == ["masterwork: accept proposal: seed build"]


async def test_repo_is_created_once_not_per_write(tmp_path: Path) -> None:
    provider, store = _role_store(tmp_path)
    target = store / "plan" / "system.md"
    for i in range(3):
        await prepare_snapshots([provider], [target])
        target.write_text(f"v{i}\n", encoding="utf-8")
        await snapshot_writes([provider], [target], f"masterwork: edit {i}")
    assert _subjects(store) == [
        "masterwork: edit 2",
        "masterwork: edit 1",
        "masterwork: edit 0",
        "masterwork: baseline snapshot",
    ]


# --- ownership ----------------------------------------------------------------


async def test_claude_tree_is_never_turned_into_a_repo(tmp_path: Path) -> None:
    provider, home = _claude_tree(tmp_path)
    target = home / "skills" / "deploy" / "SKILL.md"
    target.parent.mkdir()
    target.write_text("v1", encoding="utf-8")

    await prepare_snapshots([provider], [target])
    await snapshot_writes([provider], [target], "masterwork: edit asset")
    assert not (home / ".git").exists()  # the user's home, not ours to convert


async def test_each_write_lands_in_its_own_tree(tmp_path: Path) -> None:
    claude, home = _claude_tree(tmp_path)
    _git(home, "init", "-q")
    _git(home, "config", "user.email", "t@t")
    _git(home, "config", "user.name", "t")
    roles, store = _role_store(tmp_path)

    skill = home / "skills" / "deploy" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text("v1", encoding="utf-8")
    role = store / "plan" / "system.md"

    providers = [claude, roles]
    await prepare_snapshots(providers, [skill, role])
    skill.write_text("v2", encoding="utf-8")
    role.write_text("v2\n", encoding="utf-8")
    await snapshot_writes(providers, [skill, role], "masterwork: accept proposal: both trees")

    assert _subjects(home)[0] == "masterwork: accept proposal: both trees"
    assert _subjects(store)[0] == "masterwork: accept proposal: both trees"
    # A path in neither tree is nobody's business.
    stray = tmp_path / "elsewhere" / "notes.md"
    stray.parent.mkdir()
    stray.write_text("x", encoding="utf-8")
    await snapshot_writes(providers, [stray], "masterwork: stray")
    assert not (stray.parent / ".git").exists()
    assert len(_subjects(store)) == 2


async def test_out_of_band_changes_are_baselined_not_folded_into_the_write(
    tmp_path: Path,
) -> None:
    """The factory rewrites the store directly. Its changes must not end up
    inside the commit that records an API write."""
    provider, store = _role_store(tmp_path)
    target = store / "plan" / "system.md"
    await prepare_snapshots([provider], [target])

    # The factory re-seeds while the repo already exists.
    (store / "review").mkdir()
    (store / "review" / "system.md").write_text("You are the REVIEW stage.\n", encoding="utf-8")

    await prepare_snapshots([provider], [target])
    target.write_text("v2\n", encoding="utf-8")
    await snapshot_writes([provider], [target], "masterwork: edit asset: plan:system")

    assert _subjects(store) == [
        "masterwork: edit asset: plan:system",
        "masterwork: baseline snapshot",
        "masterwork: baseline snapshot",
    ]
    # The recorded write touches exactly the file the API wrote.
    assert _git(store, "show", "--format=", "--name-only", "HEAD") == "plan/system.md"
