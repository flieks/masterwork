"""spawn_factory_run's subprocess contract — the injection guard plan.md names:
argv is a list (never a shell string), no mode flag, detached."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.services import factory_launcher


class _FakePopen:
    """Records the exact call instead of forking; captured on the class so the
    test can inspect it after spawn_factory_run returns."""

    calls: list[dict[str, Any]] = []

    def __init__(self, argv: list[str], **kwargs: Any) -> None:
        self.pid = 4242
        type(self).calls.append({"argv": argv, **kwargs})

    def poll(self) -> int | None:
        return None


@pytest.fixture(autouse=True)
def _reset() -> None:
    _FakePopen.calls = []
    factory_launcher._children.clear()


def test_argv_has_no_mode_flag_and_is_never_shell_interpreted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(factory_launcher.subprocess, "Popen", _FakePopen)
    repo_root = tmp_path / "masterwork"
    project_path = tmp_path / "projects" / "alpha"
    project_path.mkdir(parents=True)
    log_path = tmp_path / "launches" / "1.log"

    # Untrusted request_text carrying shell metacharacters must land as one
    # argv element, never be interpreted.
    request_text = "add a widget; rm -rf / #"
    pid = factory_launcher.spawn_factory_run(
        repo_root=repo_root,
        python_bin="python3",
        project_path=project_path,
        request_text=request_text,
        log_path=log_path,
    )

    assert pid == 4242
    assert len(_FakePopen.calls) == 1
    call = _FakePopen.calls[0]
    assert call["argv"] == [
        "python3",
        str(repo_root / "factory" / "run.py"),
        "--repo",
        str(project_path),
        request_text,
    ]
    assert call["cwd"] == str(project_path)
    assert call["start_new_session"] is True
    assert "shell" not in call  # never shell=True
    assert log_path.exists()


def test_interview_argv_inserts_run_id_and_interview_before_the_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(factory_launcher.subprocess, "Popen", _FakePopen)
    repo_root = tmp_path / "masterwork"
    project_path = tmp_path / "projects" / "alpha"
    project_path.mkdir(parents=True)

    factory_launcher.spawn_factory_run(
        repo_root=repo_root,
        python_bin="python3",
        project_path=project_path,
        request_text="add a widget",
        log_path=tmp_path / "launches" / "2.log",
        run_id="a1b2c3d4",
        interview=True,
    )

    assert _FakePopen.calls[0]["argv"] == [
        "python3",
        str(repo_root / "factory" / "run.py"),
        "--repo",
        str(project_path),
        "--run-id",
        "a1b2c3d4",
        "--interview",
        "add a widget",
    ]


def test_resume_argv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(factory_launcher.subprocess, "Popen", _FakePopen)
    repo_root = tmp_path / "masterwork"
    project_path = tmp_path / "projects" / "alpha"
    project_path.mkdir(parents=True)

    pid = factory_launcher.spawn_factory_resume(
        repo_root=repo_root,
        python_bin="python3",
        project_path=project_path,
        run_id="a1b2c3d4",
        log_path=tmp_path / "launches" / "3.log",
    )

    assert pid == 4242
    call = _FakePopen.calls[0]
    assert call["argv"] == [
        "python3",
        str(repo_root / "factory" / "run.py"),
        "--repo",
        str(project_path),
        "--resume",
        "a1b2c3d4",
    ]
    assert call["cwd"] == str(project_path)
    assert call["start_new_session"] is True


def test_reaps_finished_children_before_spawning_the_next(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(factory_launcher.subprocess, "Popen", _FakePopen)
    project_path = tmp_path / "projects" / "alpha"
    project_path.mkdir(parents=True)

    class _FinishedPopen(_FakePopen):
        def poll(self) -> int | None:
            return 0  # already exited

    finished = _FinishedPopen(["noop"])
    factory_launcher._children.append(finished)  # type: ignore[arg-type]

    factory_launcher.spawn_factory_run(
        repo_root=tmp_path,
        python_bin="python3",
        project_path=project_path,
        request_text="x",
        log_path=tmp_path / "launches" / "2.log",
    )

    assert finished not in factory_launcher._children


def test_workflow_is_appended_only_when_asked_for(monkeypatch: pytest.MonkeyPatch) -> None:
    """A plain launch's argv must stay byte-for-byte what it always was."""
    seen: list[list[str]] = []

    def fake_spawn(argv: list[str], project_path: Path, log_path: Path) -> int:
        seen.append(argv)
        return 1

    monkeypatch.setattr(factory_launcher, "_spawn", fake_spawn)
    common = {
        "repo_root": Path("/repo"),
        "python_bin": "python3",
        "project_path": Path("/proj"),
        "request_text": "do it",
        "log_path": Path("/tmp/x.log"),
    }

    factory_launcher.spawn_factory_run(**common)
    assert "--workflow" not in seen[0]
    assert seen[0][-1] == "do it"

    factory_launcher.spawn_factory_run(**common, workflow="scout")
    assert seen[1][-3:] == ["--workflow", "scout", "do it"]


def test_the_injected_spawner_takes_every_argument_the_service_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tests override this dependency with a fake, so the real wrapper is the
    one thing no other test touches — and a kwarg added to the service without
    it lands as an unhandled TypeError (a 500 with no CORS headers, which a
    browser reports as 'cannot reach the backend')."""
    from app.api.deps import get_launch_spawner

    seen: dict[str, object] = {}

    def fake_run(**kwargs: object) -> int:
        seen.update(kwargs)
        return 7

    monkeypatch.setattr(factory_launcher, "spawn_factory_run", fake_run)

    pid = get_launch_spawner()(
        project_path=Path("/proj"),
        request_text="do it",
        log_path=Path("/tmp/x.log"),
        run_id="abc123",
        interview=True,
        workflow="scout",
    )

    assert pid == 7
    assert seen["workflow"] == "scout"
    assert seen["run_id"] == "abc123"
    assert seen["interview"] is True
