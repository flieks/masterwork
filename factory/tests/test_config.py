"""Defaults, the factory.config.json overlay, and checks auto-detection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from adw.config import ConfigError, load_config


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


def test_a_repo_with_no_checks_warns_loudly(tmp_path: Path):
    cfg = load_config(tmp_path)
    assert cfg.checks == []
    assert any("NO executed checks" in w for w in cfg.warnings)


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


def test_run_dir_is_under_the_target_repo(tmp_path: Path):
    cfg = load_config(tmp_path, run_id="deadbeef")
    assert cfg.run_dir == tmp_path.resolve() / "factory" / "runs" / "deadbeef"
