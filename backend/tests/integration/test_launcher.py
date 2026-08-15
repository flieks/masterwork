"""Launcher endpoints against the real test database. The subprocess spawn is
a fake dependency override — no test here ever forks a real process."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.deps import get_launch_spawner, get_resume_spawner
from app.config import settings
from app.db.models.launcher import SessionLaunch
from app.main import app


class _FakeSpawner:
    """Records every call instead of forking; hands back an incrementing pid."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(
        self,
        *,
        project_path: Path,
        request_text: str,
        log_path: Path,
        run_id: str | None = None,
        interview: bool = False,
    ) -> int:
        self.calls.append(
            {
                "project_path": project_path,
                "request_text": request_text,
                "log_path": log_path,
                "run_id": run_id,
                "interview": interview,
            }
        )
        return 4242 + len(self.calls) - 1


@pytest.fixture
def fake_spawner() -> Iterator[_FakeSpawner]:
    spawner = _FakeSpawner()
    app.dependency_overrides[get_launch_spawner] = lambda: spawner
    try:
        yield spawner
    finally:
        app.dependency_overrides.pop(get_launch_spawner, None)


class _FakeResumeSpawner:
    """Stands in for the `--resume` spawn submitting answers triggers."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, *, project_path: Path, run_id: str, log_path: Path) -> int:
        self.calls.append({"project_path": project_path, "run_id": run_id, "log_path": log_path})
        return 5000 + len(self.calls) - 1


@pytest.fixture
def fake_resume_spawner() -> Iterator[_FakeResumeSpawner]:
    spawner = _FakeResumeSpawner()
    app.dependency_overrides[get_resume_spawner] = lambda: spawner
    try:
        yield spawner
    finally:
        app.dependency_overrides.pop(get_resume_spawner, None)


@pytest.fixture
def runs_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Where an interview run's questions.json/answers.json/run.json live —
    mirrors the factory's own runs-root rule (backend/app/services/factory_runs.py)."""
    root = tmp_path / "runs"
    monkeypatch.setattr(settings, "factory_runs_root", root)
    return root


def _run_dir(runs_root: Path, project_path: Path, run_id: str) -> Path:
    return runs_root / project_path.name / run_id


def _write_questions(run_dir: Path, run_id: str, questions: list[dict[str, str]]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "questions.json").write_text(
        json.dumps({"run_id": run_id, "stage": "plan", "asked_at": "x", "questions": questions}),
        encoding="utf-8",
    )


def _write_run_state(run_dir: Path, state: str) -> None:
    (run_dir / "run.json").write_text(json.dumps({"state": state}), encoding="utf-8")


@pytest_asyncio.fixture
async def projects_root(client: AsyncClient, tmp_path: Path) -> Path:
    """Points the real settings endpoint at a fresh temp dir — no dependency
    override needed for this half of the surface."""
    root = tmp_path / "projects"
    root.mkdir()
    r = await client.patch("/api/v1/settings", json={"projects_root": str(root)})
    assert r.status_code == 200
    return root


@pytest_asyncio.fixture
async def seeded_projects(projects_root: Path) -> Path:
    alpha = projects_root / "alpha"
    alpha.mkdir()
    (alpha / ".git").mkdir()
    (projects_root / "beta").mkdir()  # no .git
    (projects_root / ".hidden").mkdir()
    (projects_root / "not-a-dir.txt").write_text("x", encoding="utf-8")
    return projects_root


# --- projects: list ----------------------------------------------------


async def test_list_projects_returns_dirs_only_sorted_with_git_flag(
    client: AsyncClient, seeded_projects: Path
) -> None:
    r = await client.get("/api/v1/launcher/projects")
    assert r.status_code == 200
    body = r.json()
    assert [p["name"] for p in body] == ["alpha", "beta"]
    assert next(p for p in body if p["name"] == "alpha")["is_git_repo"] is True
    assert next(p for p in body if p["name"] == "beta")["is_git_repo"] is False


# --- projects: create ----------------------------------------------------


async def test_create_project_mkdirs_and_git_inits(
    client: AsyncClient, projects_root: Path
) -> None:
    r = await client.post("/api/v1/launcher/projects", json={"name": "gamma"})
    assert r.status_code == 201
    body = r.json()
    assert body["is_git_repo"] is True
    created = projects_root / "gamma"
    assert created.is_dir()
    assert (created / ".git").exists()
    assert body["path"] == str(created)


@pytest.mark.parametrize("name", ["../escape", "a/b"])
async def test_create_project_rejects_traversal_and_separators(
    client: AsyncClient, projects_root: Path, name: str
) -> None:
    r = await client.post("/api/v1/launcher/projects", json={"name": name})
    assert r.status_code == 400
    assert list(projects_root.parent.iterdir()) == [projects_root]


async def test_create_project_conflict_409(client: AsyncClient, seeded_projects: Path) -> None:
    r = await client.post("/api/v1/launcher/projects", json={"name": "alpha"})
    assert r.status_code == 409


# --- browse --------------------------------------------------------------


async def test_browse_explicit_path_lists_dirs_only_sorted(
    client: AsyncClient, seeded_projects: Path
) -> None:
    r = await client.get("/api/v1/launcher/browse", params={"path": str(seeded_projects)})
    assert r.status_code == 200
    body = r.json()
    assert body["path"] == str(seeded_projects)
    assert body["parent"] == str(seeded_projects.parent)
    assert [e["name"] for e in body["entries"]] == ["alpha", "beta"]
    assert body["entries"][0]["path"] == str(seeded_projects / "alpha")


async def test_browse_defaults_to_projects_root(
    client: AsyncClient, seeded_projects: Path
) -> None:
    explicit = await client.get("/api/v1/launcher/browse", params={"path": str(seeded_projects)})
    default = await client.get("/api/v1/launcher/browse")
    assert default.status_code == 200
    assert default.json() == explicit.json()


async def test_browse_descends_into_a_subdirectory(
    client: AsyncClient, seeded_projects: Path
) -> None:
    (seeded_projects / "alpha" / "nested").mkdir()
    r = await client.get(
        "/api/v1/launcher/browse", params={"path": str(seeded_projects / "alpha")}
    )
    assert r.status_code == 200
    body = r.json()
    assert [e["name"] for e in body["entries"]] == ["nested"]
    assert body["parent"] == str(seeded_projects)


async def test_browse_reports_the_backend_home_directory(
    client: AsyncClient, seeded_projects: Path
) -> None:
    """The picker's Home shortcut points at the backend's home, not the browser's."""
    r = await client.get("/api/v1/launcher/browse", params={"path": str(seeded_projects)})
    assert r.status_code == 200
    assert r.json()["home"] == str(Path.home())


async def test_browse_filesystem_root_has_no_parent(client: AsyncClient) -> None:
    r = await client.get("/api/v1/launcher/browse", params={"path": "/"})
    assert r.status_code == 200
    assert r.json()["parent"] is None


@pytest.mark.parametrize(
    "bad_path",
    ["relative/path", "/definitely/does/not/exist/anywhere"],
)
async def test_browse_rejects_relative_and_nonexistent_paths(
    client: AsyncClient, bad_path: str
) -> None:
    r = await client.get("/api/v1/launcher/browse", params={"path": bad_path})
    assert r.status_code == 400


async def test_browse_rejects_a_file_path(client: AsyncClient, seeded_projects: Path) -> None:
    r = await client.get(
        "/api/v1/launcher/browse", params={"path": str(seeded_projects / "not-a-dir.txt")}
    )
    assert r.status_code == 400


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permission bits")
async def test_browse_unreadable_target_directory_is_400(
    client: AsyncClient, seeded_projects: Path
) -> None:
    """A target this process cannot even list — distinct from a per-entry skip,
    covered at the unit level in test_launcher_browse.py."""
    locked = seeded_projects / "locked"
    locked.mkdir()
    locked.chmod(0o000)
    try:
        r = await client.get("/api/v1/launcher/browse", params={"path": str(locked)})
        assert r.status_code == 400
    finally:
        locked.chmod(0o755)


# --- launch ----------------------------------------------------------------


async def test_launch_writes_row_and_spawns_with_expected_argv(
    client: AsyncClient,
    session_factory: async_sessionmaker,
    seeded_projects: Path,
    fake_spawner: _FakeSpawner,
) -> None:
    alpha = seeded_projects / "alpha"
    r = await client.post(
        "/api/v1/launcher/launch",
        json={"project_path": str(alpha), "request_text": "add a widget"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["launched"] is True
    assert body["pid"] == 4242
    assert body["mode"] == "autonomous"
    assert body["run_id"] is None  # autonomous launches never get one

    assert len(fake_spawner.calls) == 1
    call = fake_spawner.calls[0]
    assert call["project_path"] == alpha
    assert call["request_text"] == "add a widget"
    assert call["log_path"].name == f"{body['id']}.log"
    assert call["run_id"] is None
    assert call["interview"] is False

    async with session_factory() as db:
        row = await db.get(SessionLaunch, body["id"])
        assert row is not None
        assert row.project_path == str(alpha)
        assert row.pid == 4242
        assert row.run_id is None


async def test_launch_interview_mode_stores_and_returns_it(
    client: AsyncClient, seeded_projects: Path, fake_spawner: _FakeSpawner
) -> None:
    alpha = seeded_projects / "alpha"
    r = await client.post(
        "/api/v1/launcher/launch",
        json={"project_path": str(alpha), "request_text": "x", "mode": "interview"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "interview"
    assert body["run_id"]  # server-generated, non-empty

    # Interview mode reaches the spawner as run_id + interview=True — the
    # autonomous argv stays byte-for-byte, asserted in test_factory_launcher.py.
    call = fake_spawner.calls[0]
    assert call["project_path"] == alpha
    assert call["request_text"] == "x"
    assert call["run_id"] == body["run_id"]
    assert call["interview"] is True


async def test_launch_rejects_path_outside_root(
    client: AsyncClient, seeded_projects: Path, tmp_path: Path, fake_spawner: _FakeSpawner
) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / ".git").mkdir()
    r = await client.post(
        "/api/v1/launcher/launch", json={"project_path": str(outside), "request_text": "x"}
    )
    assert r.status_code == 400
    assert fake_spawner.calls == []


async def test_launch_rejects_traversal(
    client: AsyncClient, seeded_projects: Path, fake_spawner: _FakeSpawner
) -> None:
    r = await client.post(
        "/api/v1/launcher/launch",
        json={"project_path": str(seeded_projects / ".." / "escape"), "request_text": "x"},
    )
    assert r.status_code == 400
    assert fake_spawner.calls == []


async def test_launch_rejects_a_file_path(
    client: AsyncClient, seeded_projects: Path, fake_spawner: _FakeSpawner
) -> None:
    r = await client.post(
        "/api/v1/launcher/launch",
        json={"project_path": str(seeded_projects / "not-a-dir.txt"), "request_text": "x"},
    )
    assert r.status_code == 400
    assert fake_spawner.calls == []


async def test_launch_rejects_a_non_git_directory(
    client: AsyncClient, seeded_projects: Path, fake_spawner: _FakeSpawner
) -> None:
    r = await client.post(
        "/api/v1/launcher/launch",
        json={"project_path": str(seeded_projects / "beta"), "request_text": "x"},
    )
    assert r.status_code == 400
    assert fake_spawner.calls == []


async def test_launch_rejects_an_invalid_mode(
    client: AsyncClient, seeded_projects: Path, fake_spawner: _FakeSpawner
) -> None:
    alpha = seeded_projects / "alpha"
    r = await client.post(
        "/api/v1/launcher/launch",
        json={"project_path": str(alpha), "request_text": "x", "mode": "sideways"},
    )
    assert r.status_code == 422
    assert fake_spawner.calls == []


# --- launches list -----------------------------------------------------


async def test_list_launches_embeds_interview_for_interview_rows_only(
    client: AsyncClient, seeded_projects: Path, fake_spawner: _FakeSpawner, runs_root: Path
) -> None:
    alpha = seeded_projects / "alpha"
    await client.post(
        "/api/v1/launcher/launch", json={"project_path": str(alpha), "request_text": "auto"}
    )
    await client.post(
        "/api/v1/launcher/launch",
        json={"project_path": str(alpha), "request_text": "asks", "mode": "interview"},
    )

    r = await client.get("/api/v1/launcher/launches")
    assert r.status_code == 200
    body = r.json()
    autonomous = next(row for row in body if row["mode"] == "autonomous")
    interview = next(row for row in body if row["mode"] == "interview")
    assert autonomous["interview"] is None
    assert interview["interview"]["state"] == "starting"


# --- interview state --------------------------------------------------------


async def _launch_interview(client: AsyncClient, project_path: Path) -> dict:
    r = await client.post(
        "/api/v1/launcher/launch",
        json={"project_path": str(project_path), "request_text": "x", "mode": "interview"},
    )
    assert r.status_code == 200
    return r.json()


async def test_interview_state_walks_starting_waiting_answered(
    client: AsyncClient,
    seeded_projects: Path,
    fake_spawner: _FakeSpawner,
    fake_resume_spawner: _FakeResumeSpawner,
    runs_root: Path,
) -> None:
    alpha = seeded_projects / "alpha"
    launched = await _launch_interview(client, alpha)
    launch_id, run_id = launched["id"], launched["run_id"]

    starting = await client.get(f"/api/v1/launcher/launches/{launch_id}/interview")
    assert starting.status_code == 200
    assert starting.json()["state"] == "starting"

    run_dir = _run_dir(runs_root, alpha, run_id)
    _write_questions(run_dir, run_id, [{"id": "q1", "question": "Use SQLite for now?"}])
    _write_run_state(run_dir, "waiting_input")

    waiting = await client.get(f"/api/v1/launcher/launches/{launch_id}/interview")
    body = waiting.json()
    assert body["state"] == "waiting"
    assert body["questions"] == [{"id": "q1", "question": "Use SQLite for now?"}]

    submit = await client.post(
        f"/api/v1/launcher/launches/{launch_id}/answers",
        json={"answers": [{"id": "q1", "answer": "Yes, SQLite is fine"}]},
    )
    assert submit.status_code == 200
    assert submit.json() == {"launch_id": launch_id, "run_id": run_id, "resumed": True, "pid": 5000}
    assert len(fake_resume_spawner.calls) == 1
    resume_call = fake_resume_spawner.calls[0]
    assert resume_call["project_path"] == alpha
    assert resume_call["run_id"] == run_id

    saved = json.loads((run_dir / "answers.json").read_text())
    assert saved["answers"] == [
        {"id": "q1", "question": "Use SQLite for now?", "answer": "Yes, SQLite is fine"}
    ]

    answered = await client.get(f"/api/v1/launcher/launches/{launch_id}/interview")
    assert answered.json()["state"] == "answered"


async def test_answers_missing_a_question_is_400_and_spawns_nothing(
    client: AsyncClient,
    seeded_projects: Path,
    fake_spawner: _FakeSpawner,
    fake_resume_spawner: _FakeResumeSpawner,
    runs_root: Path,
) -> None:
    alpha = seeded_projects / "alpha"
    launched = await _launch_interview(client, alpha)
    run_dir = _run_dir(runs_root, alpha, launched["run_id"])
    _write_questions(
        run_dir,
        launched["run_id"],
        [{"id": "q1", "question": "A?"}, {"id": "q2", "question": "B?"}],
    )
    _write_run_state(run_dir, "waiting_input")

    r = await client.post(
        f"/api/v1/launcher/launches/{launched['id']}/answers",
        json={"answers": [{"id": "q1", "answer": "only one"}]},
    )
    assert r.status_code == 400
    assert fake_resume_spawner.calls == []


async def test_an_unknown_question_id_is_400(
    client: AsyncClient,
    seeded_projects: Path,
    fake_spawner: _FakeSpawner,
    fake_resume_spawner: _FakeResumeSpawner,
    runs_root: Path,
) -> None:
    alpha = seeded_projects / "alpha"
    launched = await _launch_interview(client, alpha)
    run_dir = _run_dir(runs_root, alpha, launched["run_id"])
    _write_questions(run_dir, launched["run_id"], [{"id": "q1", "question": "A?"}])
    _write_run_state(run_dir, "waiting_input")

    r = await client.post(
        f"/api/v1/launcher/launches/{launched['id']}/answers",
        json={"answers": [{"id": "not-q1", "answer": "x"}]},
    )
    assert r.status_code == 400
    assert fake_resume_spawner.calls == []


async def test_a_blank_answer_after_strip_is_400(
    client: AsyncClient,
    seeded_projects: Path,
    fake_spawner: _FakeSpawner,
    fake_resume_spawner: _FakeResumeSpawner,
    runs_root: Path,
) -> None:
    alpha = seeded_projects / "alpha"
    launched = await _launch_interview(client, alpha)
    run_dir = _run_dir(runs_root, alpha, launched["run_id"])
    _write_questions(run_dir, launched["run_id"], [{"id": "q1", "question": "A?"}])
    _write_run_state(run_dir, "waiting_input")

    r = await client.post(
        f"/api/v1/launcher/launches/{launched['id']}/answers",
        json={"answers": [{"id": "q1", "answer": "   "}]},
    )
    assert r.status_code == 400
    assert fake_resume_spawner.calls == []


async def test_submitting_answers_when_not_waiting_is_409(
    client: AsyncClient,
    seeded_projects: Path,
    fake_spawner: _FakeSpawner,
    fake_resume_spawner: _FakeResumeSpawner,
    runs_root: Path,
) -> None:
    alpha = seeded_projects / "alpha"
    launched = await _launch_interview(client, alpha)  # still "starting" — no questions.json yet

    r = await client.post(
        f"/api/v1/launcher/launches/{launched['id']}/answers",
        json={"answers": [{"id": "q1", "answer": "x"}]},
    )
    assert r.status_code == 409
    assert fake_resume_spawner.calls == []


async def test_a_second_submit_is_refused_by_the_now_answered_state(
    client: AsyncClient,
    seeded_projects: Path,
    fake_spawner: _FakeSpawner,
    fake_resume_spawner: _FakeResumeSpawner,
    runs_root: Path,
) -> None:
    """The double-submit guard: once answers.json exists, state is "answered",
    not "waiting", so a second POST cannot spawn a second resume."""
    alpha = seeded_projects / "alpha"
    launched = await _launch_interview(client, alpha)
    run_dir = _run_dir(runs_root, alpha, launched["run_id"])
    _write_questions(run_dir, launched["run_id"], [{"id": "q1", "question": "A?"}])
    _write_run_state(run_dir, "waiting_input")

    first = await client.post(
        f"/api/v1/launcher/launches/{launched['id']}/answers",
        json={"answers": [{"id": "q1", "answer": "x"}]},
    )
    assert first.status_code == 200

    second = await client.post(
        f"/api/v1/launcher/launches/{launched['id']}/answers",
        json={"answers": [{"id": "q1", "answer": "y"}]},
    )
    assert second.status_code == 409
    assert len(fake_resume_spawner.calls) == 1


async def test_answers_for_an_unknown_launch_is_404(
    client: AsyncClient, fake_resume_spawner: _FakeResumeSpawner
) -> None:
    r = await client.post(
        "/api/v1/launcher/launches/999999/answers",
        json={"answers": [{"id": "q1", "answer": "x"}]},
    )
    assert r.status_code == 404
    assert fake_resume_spawner.calls == []


async def test_interview_state_for_an_unknown_launch_is_404(client: AsyncClient) -> None:
    r = await client.get("/api/v1/launcher/launches/999999/interview")
    assert r.status_code == 404


async def test_an_autonomous_launch_reports_not_interview(
    client: AsyncClient, seeded_projects: Path, fake_spawner: _FakeSpawner
) -> None:
    alpha = seeded_projects / "alpha"
    r = await client.post(
        "/api/v1/launcher/launch", json={"project_path": str(alpha), "request_text": "x"}
    )
    launch_id = r.json()["id"]

    state = await client.get(f"/api/v1/launcher/launches/{launch_id}/interview")
    assert state.status_code == 200
    body = state.json()
    assert body["state"] == "not_interview"
    assert body["run_id"] is None
    assert body["questions"] == []
