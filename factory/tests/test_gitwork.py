"""Git ground truth on real temp repos — nothing here is mocked."""

from __future__ import annotations

from pathlib import Path

from adw import gitwork
from conftest import git


def test_is_repo(git_repo: Path, tmp_path: Path):
    assert gitwork.is_repo(git_repo)
    plain = tmp_path / "plain"
    plain.mkdir()
    assert not gitwork.is_repo(plain)


def test_changed_paths_sees_new_and_modified_files(git_repo: Path):
    snap = gitwork.snapshot(git_repo)
    assert snap.sha
    (git_repo / "app").mkdir()
    (git_repo / "app" / "main.py").write_text("print('hi')\n")
    (git_repo / "README.md").write_text("# changed\n")
    assert gitwork.changed_paths(git_repo, snap) == ["README.md", "app/main.py"]


def test_changed_paths_ignores_pre_existing_dirt(git_repo: Path):
    (git_repo / "scratch.txt").write_text("left over from before the run\n")
    snap = gitwork.snapshot(git_repo)
    (git_repo / "new.py").write_text("x = 1\n")
    assert gitwork.changed_paths(git_repo, snap) == ["new.py"]


def test_changed_paths_excludes_an_in_repo_run_dir_when_told_to(git_repo: Path):
    """Only reachable via a `runs_dir` override — by default the logs live elsewhere."""
    snap = gitwork.snapshot(git_repo)
    run_dir = git_repo / "factory" / "runs" / "abc123"
    run_dir.mkdir(parents=True)
    (run_dir / "telemetry.jsonl").write_text('{"event": "run_end"}\n')
    (git_repo / "app.py").write_text("x = 1\n")

    assert gitwork.changed_paths(git_repo, snap, exclude=("factory/runs/abc123/",)) == ["app.py"]
    # Without the exclusion nothing is hidden: the runner does not silently drop paths.
    assert gitwork.changed_paths(git_repo, snap) == [
        "app.py",
        "factory/runs/abc123/telemetry.jsonl",
    ]


def test_changed_paths_after_a_commit_is_empty(git_repo: Path):
    snap = gitwork.snapshot(git_repo)
    (git_repo / "a.py").write_text("a\n")
    gitwork.commit(git_repo, "build: add a")
    # The stage's work is committed, but it is still what changed since the snapshot.
    assert gitwork.changed_paths(git_repo, snap) == ["a.py"]
    after = gitwork.snapshot(git_repo)
    assert gitwork.changed_paths(git_repo, after) == []


def test_changed_paths_sees_staged_renames(git_repo: Path):
    (git_repo / "old.py").write_text("x = 1\n")
    git(git_repo, "add", "-A")
    git(git_repo, "commit", "-q", "-m", "add old")
    snap = gitwork.snapshot(git_repo)
    git(git_repo, "mv", "old.py", "new.py")
    assert gitwork.changed_paths(git_repo, snap) == ["new.py", "old.py"]


def test_revert_deletes_untracked_and_restores_tracked(git_repo: Path):
    (git_repo / "src").mkdir()
    (git_repo / "src" / "hack.py").write_text("out of bounds\n")
    (git_repo / "README.md").write_text("vandalised\n")

    reverted = gitwork.revert(git_repo, ["src/hack.py", "README.md"])

    assert sorted(reverted) == ["README.md", "src/hack.py"]
    assert not (git_repo / "src" / "hack.py").exists()
    assert (git_repo / "README.md").read_text() == "# fixture repo\n"


def test_commit_returns_a_sha_and_none_when_clean(git_repo: Path):
    (git_repo / "a.py").write_text("a\n")
    sha = gitwork.commit(git_repo, "build: add a")
    assert sha and len(sha) == 40
    assert gitwork.commit(git_repo, "build: nothing to do") is None
    assert git(git_repo, "log", "-1", "--pretty=%s").strip() == "build: add a"


def test_commit_body_can_carry_assumptions(git_repo: Path):
    (git_repo / "a.py").write_text("a\n")
    gitwork.commit(git_repo, "build: add a\n\nAssumption: sqlite is fine for dev")
    body = git(git_repo, "log", "-1", "--pretty=%B")
    assert "Assumption: sqlite is fine for dev" in body


def test_head_sha_is_none_before_the_first_commit(tmp_path: Path):
    repo = tmp_path / "fresh"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    assert gitwork.head_sha(repo) is None
    (repo / "a.py").write_text("a\n")
    snap = gitwork.Snapshot(sha=None, dirty=frozenset())
    assert gitwork.changed_paths(repo, snap) == ["a.py"]
