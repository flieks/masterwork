"""Branch per run: where the commits land, and what the runner refuses to touch."""

from __future__ import annotations

from pathlib import Path

import run as cli
from adw import gitwork
from adw.pipeline import format_summary
from conftest import FakeCLI, git
from test_pipeline import BUILD_OK, DOCUMENT_OK, PLAN_OK, REVIEW_OK, run, subjects

SCRIPT = [PLAN_OK, BUILD_OK, REVIEW_OK, DOCUMENT_OK]
BUILT = [
    "document: Document the endpoint",
    "build: Add the health route",
    "plan: Plan the health endpoint",
]


def branches(repo: Path) -> list[str]:
    listed = git(repo, "branch", "--format=%(refname:short)").split()
    return sorted(listed)


def log_of(repo: Path, ref: str) -> list[str]:
    return git(repo, "log", "--pretty=%s", ref).strip().splitlines()


def tracked_on(repo: Path, ref: str) -> list[str]:
    return git(repo, "ls-tree", "-r", "--name-only", ref).split()


def fresh_repo(tmp_path: Path, name: str = "fresh") -> Path:
    """A repo with no commits at all — an unborn HEAD."""
    repo = tmp_path / name
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "factory@test.local")
    git(repo, "config", "user.name", "Factory Test")
    git(repo, "config", "commit.gpgsign", "false")
    return repo


# --- the default: a branch of its own --------------------------------------


def test_the_run_commits_onto_its_own_branch_and_leaves_the_original_alone(
    git_repo: Path, fake_cli: FakeCLI
):
    before = log_of(git_repo, "main")
    result, _ = run(git_repo, fake_cli, SCRIPT)

    assert result.accepted, result.reason
    assert result.branch is not None
    assert (result.branch.name, result.branch.origin) == ("factory/testrun1", "main")
    assert branches(git_repo) == ["factory/testrun1", "main"]
    assert log_of(git_repo, "factory/testrun1")[:3] == BUILT
    # The whole point: the branch the user had checked out is byte-for-byte as it was.
    assert log_of(git_repo, "main") == before == ["initial"]
    assert tracked_on(git_repo, "main") == ["README.md"]


def test_the_user_is_left_on_the_run_branch_with_the_work_checked_out(
    git_repo: Path, fake_cli: FakeCLI
):
    result, _ = run(git_repo, fake_cli, SCRIPT)

    assert gitwork.current_branch(git_repo) == "factory/testrun1"
    assert (git_repo / "app.py").is_file()  # the work is right there, not hidden on a ref
    assert git(git_repo, "status", "--porcelain").strip() == ""
    assert result.accepted


def test_the_summary_names_the_branch_the_commits_and_the_way_back(
    git_repo: Path, fake_cli: FakeCLI
):
    """A user who cannot find the commits concludes the run did nothing."""
    result, _ = run(git_repo, fake_cli, SCRIPT)
    summary = format_summary(result)

    assert "branch: factory/testrun1 (created from main) — you are on it now" in summary
    assert "3 commit(s): plan " in summary
    assert "see the work:   git log main..factory/testrun1" in summary
    assert "back to where you started: git checkout main" in summary


def test_a_named_branch_is_used_verbatim(git_repo: Path, fake_cli: FakeCLI):
    result, _ = run(git_repo, fake_cli, SCRIPT, branch="wip/health")

    assert result.accepted
    assert branches(git_repo) == ["main", "wip/health"]
    assert log_of(git_repo, "wip/health")[:3] == BUILT
    assert log_of(git_repo, "main") == ["initial"]


def test_a_failed_run_still_says_where_its_branch_is(git_repo: Path, fake_cli: FakeCLI):
    """Even with nothing committed the branch is named, so `git branch` holds no surprise."""
    no_envelope = {"session_id": "plan-session", "raw_reply": "Done! Everything works."}
    result, _ = run(git_repo, fake_cli, [no_envelope] * 4)
    summary = format_summary(result)

    assert not result.accepted
    assert "nothing was committed — factory/testrun1 is still exactly main" in summary
    assert "back to where you started: git checkout main" in summary


# --- --no-branch: exactly what the runner did before ------------------------


def test_no_branch_commits_onto_the_checked_out_branch_as_before(
    git_repo: Path, fake_cli: FakeCLI
):
    result, _ = run(git_repo, fake_cli, SCRIPT, branch=False)

    assert result.accepted, result.reason
    assert branches(git_repo) == ["main"]  # no ref was created at all
    assert gitwork.current_branch(git_repo) == "main"
    assert subjects(git_repo)[:3] == BUILT
    assert result.branch is not None and result.branch.name is None
    assert "branch: none — 3 commit(s) on main" in format_summary(result)
    assert "--no-branch" in format_summary(result)


def test_the_no_branch_flag_reaches_the_config(git_repo: Path, capsys):
    (git_repo / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert cli.main(["--repo", str(git_repo), "--dry-run", "--no-branch", "x"]) == 0
    assert "branch:                none (--no-branch) — commits would land on main" in (
        capsys.readouterr().out
    )


# --- uncommitted work -------------------------------------------------------


def test_uncommitted_work_is_carried_onto_the_branch_and_survives_the_run(
    git_repo: Path, fake_cli: FakeCLI
):
    (git_repo / "notes.md").write_text("MY UNCOMMITTED WORK\n", encoding="utf-8")
    (git_repo / "README.md").write_text("# my own edit\n", encoding="utf-8")

    result, _ = run(git_repo, fake_cli, SCRIPT)

    assert result.accepted, result.reason
    # On disk, exactly as the user left it.
    assert (git_repo / "notes.md").read_text() == "MY UNCOMMITTED WORK\n"
    assert (git_repo / "README.md").read_text() == "# my own edit\n"
    # And on the run branch, where it can be recovered — never on the original.
    assert result.branch is not None
    assert set(result.branch.carried) >= {"README.md", "notes.md"}
    assert "MY UNCOMMITTED WORK" in git(git_repo, "show", "factory/testrun1:notes.md")
    assert "notes.md" not in tracked_on(git_repo, "main")
    assert git(git_repo, "show", "main:README.md") == "# fixture repo\n"
    assert "you had uncommitted came along onto this branch" in format_summary(result)


def test_pre_existing_dirt_is_still_not_charged_to_the_first_stage(
    git_repo: Path, fake_cli: FakeCLI
):
    """The changed_files and boundary gates read a pre-stage snapshot — branching
    happens before the first one, so the baselines say exactly what they said before."""
    (git_repo / "scratch.txt").write_text("left over from before the run\n", encoding="utf-8")

    result, _ = run(git_repo, fake_cli, SCRIPT)

    # The planner claimed plan.md only. If the branch step had disturbed the baseline,
    # scratch.txt would read as an undeclared write and cost a correction.
    assert result.accepted, result.reason
    assert result.corrections == 0
    assert [o.corrections for o in result.outcomes] == [0] * 5


# --- the three shapes that must not crash mid-run ---------------------------


def test_an_existing_branch_name_is_refused_before_any_agent_runs(
    git_repo: Path, fake_cli: FakeCLI
):
    git(git_repo, "branch", "factory/testrun1")
    head = git(git_repo, "rev-parse", "factory/testrun1").strip()

    result, _ = run(git_repo, fake_cli, SCRIPT)

    assert not result.accepted
    assert result.exit_code == 1
    assert "branch 'factory/testrun1' already exists" in result.reason
    assert "--no-branch" in result.reason
    assert fake_cli.calls == []  # refused before a single token was spent
    # The branch that was already there is untouched, and so is the checkout.
    assert git(git_repo, "rev-parse", "factory/testrun1").strip() == head
    assert log_of(git_repo, "factory/testrun1") == ["initial"]
    assert gitwork.current_branch(git_repo) == "main"


def test_a_detached_head_gets_a_branch_and_a_way_back_to_the_sha(
    git_repo: Path, fake_cli: FakeCLI
):
    (git_repo / "second.txt").write_text("second\n", encoding="utf-8")
    git(git_repo, "add", "-A")
    git(git_repo, "commit", "-q", "-m", "second")
    sha = git(git_repo, "rev-parse", "HEAD").strip()
    git(git_repo, "checkout", "-q", "--detach", sha)
    assert gitwork.current_branch(git_repo) is None

    result, _ = run(git_repo, fake_cli, SCRIPT)

    assert result.accepted, result.reason
    assert result.branch is not None
    assert result.branch.detached and result.branch.origin == sha
    assert log_of(git_repo, "factory/testrun1")[:3] == BUILT
    assert log_of(git_repo, "main") == ["second", "initial"]
    assert f"back to where you started: git checkout {sha}" in format_summary(result)


def test_a_repo_with_no_commits_runs_without_a_branch_and_says_why(
    tmp_path: Path, fake_cli: FakeCLI
):
    repo = fresh_repo(tmp_path)
    assert gitwork.head_sha(repo) is None

    result, _ = run(repo, fake_cli, SCRIPT)

    assert result.accepted, result.reason
    assert result.branch is not None and result.branch.name is None
    assert "no commits yet" in result.branch.note
    assert branches(repo) == ["main"]  # the run's own commits made `main` real
    assert log_of(repo, "main")[:3] == BUILT
    assert "branch: none — 3 commit(s) on main" in format_summary(result)


def test_the_dry_run_says_what_it_would_do_to_git(git_repo: Path, capsys):
    (git_repo / "pyproject.toml").write_text("[project]\nname='x'\n")

    assert cli.main(["--repo", str(git_repo), "--dry-run", "--branch", "wip/x", "x"]) == 0
    assert "branch:                wip/x — would be created from main at run start" in (
        capsys.readouterr().out
    )

    git(git_repo, "branch", "wip/x")
    assert cli.main(["--repo", str(git_repo), "--dry-run", "--branch", "wip/x", "x"]) == 0
    assert "wip/x — ALREADY EXISTS, so the run would refuse to start" in capsys.readouterr().out


def test_branch_and_no_branch_cannot_both_be_given(git_repo: Path):
    parser = cli.build_parser()
    try:
        parser.parse_args(["--branch", "x", "--no-branch", "req"])
    except SystemExit as exc:
        assert exc.code == 2
    else:  # pragma: no cover - the guard is the point of the test
        raise AssertionError("--branch and --no-branch must be mutually exclusive")


# --- the promises the branch feature makes ----------------------------------


def test_the_runner_owns_no_destructive_git_verb():
    """Never force, never delete, never touch a remote — enforced on the source."""
    source = Path(gitwork.__file__).read_text(encoding="utf-8")
    for verb in ("push", "fetch", "remote", "reset", "--force", "--hard", '"-D"', '"-B"', "clean"):
        assert verb not in source, f"gitwork must never reach for `{verb}`"
