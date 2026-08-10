"""The thin CLI: argument handling and the --dry-run stage table."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import run as cli  # factory/run.py, importable via the conftest path insert


def dry_run(repo: Path, capsys, *args: str) -> str:
    code = cli.main(["--repo", str(repo), "--dry-run", *args])
    assert code == 0
    return capsys.readouterr().out


def test_dry_run_prints_the_resolved_stage_table(git_repo: Path, capsys):
    (git_repo / "pyproject.toml").write_text("[project]\nname='x'\n")
    out = dry_run(git_repo, capsys, "Add a /health endpoint")

    assert "Add a /health endpoint" in out
    for stage in ("plan", "build", "checks", "review", "document"):
        assert stage in out
    assert "opus" in out and "sonnet" in out
    assert "docs/specs/**" in out
    assert "(unrestricted)" in out
    assert "(read-only)" in out
    assert "uv run pytest -q" in out
    assert "uv run ruff check ." in out
    assert "max corrections/stage: 2" in out
    assert "max review rounds:     2" in out
    assert "telemetry.jsonl" in out


def test_dry_run_never_calls_the_agent(git_repo: Path, capsys, fake_cli):
    (git_repo / "pyproject.toml").write_text("[project]\nname='x'\n")
    dry_run(git_repo, capsys, "Add a /health endpoint")
    assert fake_cli.calls == []


def test_dry_run_reflects_config_and_flag_overrides(git_repo: Path, capsys):
    (git_repo / "factory.config.json").write_text(
        json.dumps({"checks": ["make ci"], "telemetry_url": None}), encoding="utf-8"
    )
    out = dry_run(git_repo, capsys, "--model", "haiku", "--max-review-rounds", "1", "x")

    assert "make ci" in out
    assert "haiku" in out
    assert "opus" not in out
    assert "max review rounds:     1" in out
    assert "telemetry POST:        (disabled)" in out


def test_an_undetectable_repo_is_refused_before_any_agent_runs(
    git_repo: Path, capsys, fake_cli
):
    assert cli.main(["--repo", str(git_repo), "x"]) == 2
    err = capsys.readouterr().err
    assert "pyproject.toml" in err and "package.json" in err
    assert '"checks"' in err and "factory.config.json" in err
    assert "--no-checks" in err
    assert fake_cli.calls == []  # refused before the CLI was ever launched


def test_dry_run_refuses_too_instead_of_printing_a_happy_table(git_repo: Path, capsys):
    assert cli.main(["--repo", str(git_repo), "--dry-run", "x"]) == 2
    captured = capsys.readouterr()
    assert "nothing to verify" in captured.err
    assert "STAGE" not in captured.out


def test_an_explicit_opt_out_is_allowed_and_marked_unverified(git_repo: Path, capsys):
    out = dry_run(git_repo, capsys, "--no-checks", "x")
    assert "(none — this run would be UNVERIFIED — no checks ran)" in out
    assert "warning:" in out and "NO checks" in out


def test_an_empty_checks_array_in_the_config_is_allowed(git_repo: Path, capsys):
    (git_repo / "factory.config.json").write_text(json.dumps({"checks": []}), encoding="utf-8")
    out = dry_run(git_repo, capsys, "x")
    assert "UNVERIFIED" in out
    assert "explicitly empty" in out


def test_dry_run_shows_the_runs_dir_outside_the_repo(git_repo: Path, capsys):
    (git_repo / "pyproject.toml").write_text("[project]\nname='x'\n")
    out = dry_run(git_repo, capsys, "x")
    telemetry_line = next(ln for ln in out.splitlines() if ln.startswith("telemetry:"))
    assert str(git_repo) not in telemetry_line
    assert str(Path.home() / ".masterwork" / "runs") in telemetry_line


def test_the_runs_dir_flag_is_honoured_in_the_dry_run(git_repo: Path, tmp_path: Path, capsys):
    (git_repo / "pyproject.toml").write_text("[project]\nname='x'\n")
    out = dry_run(git_repo, capsys, "--runs-dir", str(tmp_path / "logs"), "x")
    assert f"{tmp_path / 'logs'}" in out


def test_the_first_run_seeds_the_role_library_and_says_so(
    git_repo: Path, tmp_path: Path, capsys, isolated_roles: Path
):
    (git_repo / "pyproject.toml").write_text("[project]\nname='x'\n")
    out = dry_run(git_repo, capsys, "x")

    assert f"seeded 13 default role file(s) into {isolated_roles}" in out
    assert (isolated_roles / "plan" / "system.md").is_file()
    # Seeded on this run, so the table already reports the files, not the built-ins.
    assert str(isolated_roles / "plan" / "role.json") in out
    assert "(built-in)" not in out

    second = dry_run(git_repo, capsys, "x")
    assert "seeded" not in second  # an existing library is left alone


def test_seed_roles_fills_a_gap_without_touching_an_edited_file(
    git_repo: Path, capsys, isolated_roles: Path
):
    (git_repo / "pyproject.toml").write_text("[project]\nname='x'\n")
    dry_run(git_repo, capsys, "x")
    edited = isolated_roles / "review" / "system.md"
    edited.write_text("MY REVIEWER\n", encoding="utf-8")
    (isolated_roles / "review" / "role.json").unlink()

    assert cli.main(["--repo", str(git_repo), "--seed-roles"]) == 0
    out = capsys.readouterr().out
    assert "seeded 1 default role file(s)" in out
    assert edited.read_text() == "MY REVIEWER\n"


def test_the_roles_dir_flag_moves_the_library(git_repo: Path, tmp_path: Path, capsys):
    (git_repo / "pyproject.toml").write_text("[project]\nname='x'\n")
    library = tmp_path / "shared-roles"
    out = dry_run(git_repo, capsys, "--roles-dir", str(library), "x")
    assert str(library) in out
    assert (library / "build" / "user.md").is_file()


def test_a_broken_role_template_is_refused_before_any_agent_runs(
    git_repo: Path, capsys, fake_cli, isolated_roles: Path
):
    broken = isolated_roles / "plan" / "user.md"
    broken.parent.mkdir(parents=True)
    broken.write_text("Do {{whatever}}.\n", encoding="utf-8")

    assert cli.main(["--repo", str(git_repo), "--dry-run", "x"]) == 2
    err = capsys.readouterr().err
    assert str(broken) in err and "{{whatever}}" in err
    assert fake_cli.calls == []


def test_a_user_template_that_lost_the_request_is_refused_before_any_agent_runs(
    git_repo: Path, capsys, fake_cli, isolated_roles: Path
):
    """A rewritten template that renders fine but sends no request: exit 2, named."""
    broken = isolated_roles / "build" / "user.md"
    broken.parent.mkdir(parents=True)
    broken.write_text("Implement the plan, tests included.\n", encoding="utf-8")

    assert cli.main(["--repo", str(git_repo), "--dry-run", "x"]) == 2
    err = capsys.readouterr().err
    assert str(broken) in err and "{{request}}" in err and "build" in err
    assert fake_cli.calls == []


def test_the_dry_run_names_the_layer_that_answered_for_each_file(
    git_repo: Path, capsys, isolated_roles: Path
):
    (git_repo / "pyproject.toml").write_text("[project]\nname='x'\n")
    dry_run(git_repo, capsys, "x")  # the first run seeds the library
    override = git_repo / ".masterwork" / "agents" / "review" / "system.md"
    override.parent.mkdir(parents=True)
    override.write_text("HOUSE REVIEWER\n", encoding="utf-8")
    (isolated_roles / "document" / "user.md").unlink()

    out = dry_run(git_repo, capsys, "x")
    rows = [" ".join(line.split()) for line in out.splitlines()]
    assert f"system.md repo {override}" in rows  # the repo is overriding this one
    assert f"user.md library {isolated_roles / 'plan' / 'user.md'}" in rows
    assert "user.md builtin (built-in)" in rows  # the deleted one, back to hardcoded text
    assert "review [repo+library]" in out
    assert "plan [library]" in out
    assert f"warning: role library: {isolated_roles / 'document' / 'user.md'} is missing" in out


def test_a_non_git_directory_is_refused(tmp_path: Path, capsys):
    assert cli.main(["--repo", str(tmp_path), "--dry-run", "x"]) == 2
    assert "not a git repository" in capsys.readouterr().err


def test_a_missing_directory_is_refused(tmp_path: Path, capsys):
    assert cli.main(["--repo", str(tmp_path / "nope"), "--dry-run", "x"]) == 2
    assert "no such directory" in capsys.readouterr().err


def test_a_run_without_a_request_is_refused(git_repo: Path, capsys):
    assert cli.main(["--repo", str(git_repo)]) == 2
    assert "a request is required" in capsys.readouterr().err


def test_malformed_config_is_refused(git_repo: Path, capsys):
    (git_repo / "factory.config.json").write_text("{not json", encoding="utf-8")
    assert cli.main(["--repo", str(git_repo), "--dry-run", "x"]) == 2
    assert "error:" in capsys.readouterr().err


def test_run_py_is_executable_as_a_script():
    path = Path(cli.__file__)
    assert path.name == "run.py"
    assert path.read_text().startswith("#!/usr/bin/env python3")
    assert str(path.parent) in sys.path
