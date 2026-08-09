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


def test_dry_run_warns_when_a_repo_has_no_checks(git_repo: Path, capsys):
    out = dry_run(git_repo, capsys, "x")
    assert "(none)" in out
    assert "warning:" in out and "NO executed checks" in out


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
