"""Defaults, the factory.config.json overlay, and checks auto-detection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from adw.config import (
    CONFIGURED,
    DEFAULT_RUNS_ROOT,
    DETECTED,
    OPTED_OUT,
    ConfigError,
    load_config,
)


def write_config(repo: Path, data: dict) -> None:
    (repo / "factory.config.json").write_text(json.dumps(data), encoding="utf-8")


def test_defaults(tmp_path: Path):
    cfg = load_config(tmp_path)
    assert cfg.stages["plan"].model == "opus"
    assert cfg.stages["build"].model == "sonnet"
    assert cfg.stages["review"].model == "opus"
    assert cfg.stages["document"].model == "sonnet"
    assert cfg.stages["checks"].model is None
    assert cfg.stages["plan"].boundary == ["plan.md", "docs/specs/**"]
    assert cfg.stages["build"].boundary is None
    assert cfg.stages["review"].boundary == []
    assert cfg.stages["document"].boundary == ["docs/**", "README.md", "CHANGELOG.md"]
    assert cfg.max_corrections == 2
    assert cfg.max_review_rounds == 2
    assert cfg.telemetry_url == "http://localhost:8008/api/v1/hooks/events"
    assert len(cfg.run_id) == 8


def test_writers_keep_file_tools_but_never_shell_out(tmp_path: Path):
    cfg = load_config(tmp_path)
    for name in ("plan", "build", "document"):
        tools = cfg.stages[name].disallowed_tools
        assert set(tools) == {"Bash", "Task", "WebFetch", "WebSearch"}
    review = cfg.stages["review"].disallowed_tools
    assert {"Edit", "Write", "NotebookEdit", "MultiEdit"} <= set(review)


def test_overlay_wins_over_defaults(tmp_path: Path):
    write_config(
        tmp_path,
        {
            "models": {"build": "opus"},
            "boundaries": {"document": ["CHANGELOG.md"], "build": ["app/**"]},
            "checks": ["make test"],
            "max_corrections": 5,
            "max_review_rounds": 1,
            "telemetry_url": None,
            "claude_bin": "/opt/claude",
        },
    )
    cfg = load_config(tmp_path)
    assert cfg.stages["build"].model == "opus"
    assert cfg.stages["plan"].model == "opus"  # untouched default
    assert cfg.stages["build"].boundary == ["app/**"]
    assert cfg.stages["document"].boundary == ["CHANGELOG.md"]
    assert cfg.checks == ["make test"]
    assert cfg.max_corrections == 5
    assert cfg.max_review_rounds == 1
    assert cfg.telemetry_url is None
    assert cfg.claude_bin == "/opt/claude"


def test_cli_overrides_win_over_the_config_file(tmp_path: Path):
    write_config(tmp_path, {"models": {"build": "opus"}, "max_corrections": 5})
    cfg = load_config(tmp_path, model_override="haiku", max_corrections=0)
    assert {s.model for s in cfg.stages.values() if s.model} == {"haiku"}
    assert cfg.max_corrections == 0


def test_checks_are_autodetected_for_a_python_repo(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    cfg = load_config(tmp_path)
    assert cfg.checks == ["uv run pytest -q", "uv run ruff check ."]
    assert cfg.warnings == ()


def test_checks_are_autodetected_for_a_node_repo(tmp_path: Path):
    (tmp_path / "package.json").write_text("{}")
    cfg = load_config(tmp_path)
    assert cfg.checks == ["npm run typecheck --if-present", "npm test --if-present"]


def test_a_fullstack_repo_gets_both_sets(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "package.json").write_text("{}")
    cfg = load_config(tmp_path)
    assert len(cfg.checks) == 4


def test_an_undetectable_repo_is_flagged_not_silently_greenlit(tmp_path: Path):
    cfg = load_config(tmp_path)
    assert cfg.checks == []
    assert cfg.checks_source == DETECTED
    assert cfg.undetectable_checks  # the CLI refuses to start on this
    assert not cfg.verified


def test_explicitly_empty_checks_are_allowed_but_unverified(tmp_path: Path):
    write_config(tmp_path, {"checks": []})
    cfg = load_config(tmp_path)
    assert cfg.checks_source == CONFIGURED
    assert not cfg.undetectable_checks  # explicit opt-out: allowed to run
    assert not cfg.verified
    assert any("explicitly empty" in w for w in cfg.warnings)


def test_the_no_checks_flag_is_an_opt_out_too(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    cfg = load_config(tmp_path, no_checks=True)
    assert cfg.checks == []  # the flag beats auto-detection
    assert cfg.checks_source == OPTED_OUT
    assert not cfg.undetectable_checks
    assert not cfg.verified


def test_detected_checks_mean_a_verified_run(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    cfg = load_config(tmp_path)
    assert cfg.checks_source == DETECTED
    assert cfg.verified and not cfg.undetectable_checks


def test_checks_may_be_given_as_argv_lists(tmp_path: Path):
    write_config(tmp_path, {"checks": [["uv", "run", "pytest", "-q"]]})
    assert load_config(tmp_path).checks == ["uv run pytest -q"]


def test_unknown_stage_names_warn_instead_of_crashing(tmp_path: Path):
    write_config(tmp_path, {"models": {"research": "opus"}})
    cfg = load_config(tmp_path)
    assert any("research" in w for w in cfg.warnings)


def test_malformed_config_raises(tmp_path: Path):
    (tmp_path / "factory.config.json").write_text("{not json")
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_bad_boundary_type_raises(tmp_path: Path):
    write_config(tmp_path, {"boundaries": {"plan": "plan.md"}})
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_explicit_config_path_must_exist(tmp_path: Path):
    with pytest.raises(ConfigError):
        load_config(tmp_path, config_path=tmp_path / "nope.json")


def test_run_logs_default_outside_the_target_repo(tmp_path: Path):
    repo = tmp_path / "someones-repo"
    repo.mkdir()
    cfg = load_config(repo, run_id="deadbeef")
    assert cfg.run_dir == DEFAULT_RUNS_ROOT / "someones-repo" / "deadbeef"
    assert repo.resolve() not in cfg.run_dir.parents
    assert cfg.run_dir_exclusions == ()  # nothing of ours is in their diff


def test_runs_dir_can_be_overridden_absolutely(tmp_path: Path):
    repo, elsewhere = tmp_path / "repo", tmp_path / "logs"
    repo.mkdir()
    cfg = load_config(repo, run_id="deadbeef", runs_dir=elsewhere)
    assert cfg.run_dir == elsewhere / "deadbeef"
    assert cfg.run_dir_exclusions == ()


def test_the_config_file_can_override_runs_dir(tmp_path: Path):
    write_config(tmp_path, {"runs_dir": "~/somewhere-else"})
    cfg = load_config(tmp_path, run_id="deadbeef")
    assert cfg.run_dir == Path.home() / "somewhere-else" / "deadbeef"


def test_a_relative_override_lands_in_the_repo_and_is_excluded(tmp_path: Path):
    cfg = load_config(tmp_path, run_id="deadbeef", runs_dir="factory/runs")
    assert cfg.run_dir == tmp_path.resolve() / "factory" / "runs" / "deadbeef"
    # Only then does the gate diff need to know about the run logs.
    assert cfg.run_dir_exclusions == ("factory/runs/deadbeef/",)


def test_the_cli_runs_dir_beats_the_config_file(tmp_path: Path):
    write_config(tmp_path, {"runs_dir": "in-repo"})
    cfg = load_config(tmp_path, run_id="deadbeef", runs_dir=tmp_path / "flag")
    assert cfg.run_dir == tmp_path / "flag" / "deadbeef"


def test_an_empty_runs_dir_is_rejected(tmp_path: Path):
    write_config(tmp_path, {"runs_dir": "  "})
    with pytest.raises(ConfigError):
        load_config(tmp_path)
