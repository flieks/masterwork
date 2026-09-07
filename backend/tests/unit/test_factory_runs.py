"""The backend half of the interview file contract: runs-root resolution,
questions/run-state reads, and the answers.json write."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config import settings
from app.services import factory_runs


def test_runs_root_defaults_to_factory_runs_root_over_project_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "factory_runs_root", tmp_path / "runs")
    project = tmp_path / "projects" / "alpha"
    project.mkdir(parents=True)

    assert factory_runs.runs_root_for(project) == tmp_path / "runs" / "alpha"


def test_an_absolute_configured_runs_dir_wins(tmp_path: Path) -> None:
    project = tmp_path / "alpha"
    project.mkdir()
    elsewhere = tmp_path / "elsewhere"
    (project / "factory.config.json").write_text(
        json.dumps({"runs_dir": str(elsewhere)}), encoding="utf-8"
    )

    assert factory_runs.runs_root_for(project) == elsewhere


def test_a_relative_configured_runs_dir_resolves_against_the_project(tmp_path: Path) -> None:
    project = tmp_path / "alpha"
    project.mkdir()
    (project / "factory.config.json").write_text(
        json.dumps({"runs_dir": "factory/runs"}), encoding="utf-8"
    )

    assert factory_runs.runs_root_for(project) == project / "factory" / "runs"


def test_a_malformed_config_file_falls_back_to_the_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "factory_runs_root", tmp_path / "runs")
    project = tmp_path / "alpha"
    project.mkdir()
    (project / "factory.config.json").write_text("not json", encoding="utf-8")

    assert factory_runs.runs_root_for(project) == tmp_path / "runs" / "alpha"


def test_run_dir_for_is_the_runs_root_plus_the_run_id(tmp_path: Path) -> None:
    project = tmp_path / "alpha"
    project.mkdir()
    assert factory_runs.run_dir_for(project, "r1") == factory_runs.runs_root_for(project) / "r1"


# --- reading questions / run state ------------------------------------------


def test_read_questions_returns_them_in_order(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "questions.json").write_text(
        json.dumps(
            {
                "run_id": "r1",
                "stage": "plan",
                "asked_at": "x",
                "questions": [{"id": "q1", "question": "A?"}, {"id": "q2", "question": "B?"}],
            }
        ),
        encoding="utf-8",
    )

    assert factory_runs.read_questions(run_dir) == [
        {"id": "q1", "question": "A?"},
        {"id": "q2", "question": "B?"},
    ]


def test_read_questions_is_empty_when_the_file_is_missing_or_malformed(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    assert factory_runs.read_questions(missing) == []

    malformed = tmp_path / "malformed"
    malformed.mkdir()
    (malformed / "questions.json").write_text("not json", encoding="utf-8")
    assert factory_runs.read_questions(malformed) == []


def test_read_run_state_reads_the_state_field(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(json.dumps({"state": "waiting_input"}), encoding="utf-8")

    assert factory_runs.read_run_state(run_dir) == "waiting_input"


def test_read_run_state_is_none_when_the_file_is_missing_or_malformed(tmp_path: Path) -> None:
    assert factory_runs.read_run_state(tmp_path / "missing") is None

    malformed = tmp_path / "malformed"
    malformed.mkdir()
    (malformed / "run.json").write_text("not json", encoding="utf-8")
    assert factory_runs.read_run_state(malformed) is None


def test_answers_exist_reflects_the_file(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    assert factory_runs.answers_exist(run_dir) is False
    (run_dir / "answers.json").write_text("{}", encoding="utf-8")
    assert factory_runs.answers_exist(run_dir) is True


# --- writing answers ---------------------------------------------------------


def test_write_answers_is_atomic_and_readable_back(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    pairs = [{"id": "q1", "question": "A?", "answer": "yes"}]

    factory_runs.write_answers(run_dir, pairs)

    assert not (run_dir / "answers.json.tmp").exists()  # no leftover temp file
    saved = json.loads((run_dir / "answers.json").read_text())
    assert saved["answers"] == pairs
    assert "answered_at" in saved


def test_write_answers_refuses_a_missing_run_dir(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        factory_runs.write_answers(tmp_path / "never-started", [{"id": "q1", "answer": "x"}])
