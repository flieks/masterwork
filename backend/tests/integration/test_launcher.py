"""Launcher endpoints against the real test database. The subprocess spawn is
a fake dependency override — no test here ever forks a real process."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.deps import get_launch_spawner, get_resume_spawner
from app.api.v1.launcher import service as launcher_service
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
        workflow: str | None = None,
    ) -> int:
        self.calls.append(
            {
                "project_path": project_path,
                "request_text": request_text,
                "log_path": log_path,
                "run_id": run_id,
                "interview": interview,
                "workflow": workflow,
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


@pytest.fixture(autouse=True)
def _no_resume_settle(monkeypatch: pytest.MonkeyPatch) -> None:
    """The settle wait exists for a real spawn; the fake one has nothing to say."""
    monkeypatch.setattr(launcher_service, "RESUME_SETTLE_SECONDS", 0)


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


# --- factory runs: list + resume ------------------------------------------


def _write_run_record(
    runs_root: Path,
    project_path: Path,
    run_id: str,
    *,
    state: str = "stopped",
    pid: int | None = None,
    accepted: bool = False,
    branch: str | None = "factory/x",
    started: str = "2026-08-15T09:00:00+00:00",
    reason: str | None = None,
) -> Path:
    run_dir = _run_dir(runs_root, project_path, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "run_id": run_id,
        "repo": str(project_path),
        "request": f"request for {run_id}",
        "state": state,
        "pid": pid,
        "accepted": accepted,
        "branch": branch,
        "started": started,
        "ended": None,
        "reason": reason,
        "interview": False,
    }
    (run_dir / "run.json").write_text(json.dumps(record), encoding="utf-8")
    return run_dir


async def test_list_factory_runs_reads_every_projects_run_dirs(
    client: AsyncClient, seeded_projects: Path, runs_root: Path
) -> None:
    alpha = seeded_projects / "alpha"
    beta = seeded_projects / "beta"
    _write_run_record(
        runs_root, alpha, "aaaa1111",
        state="stopped", reason="cost cap reached: $32 of $25 budget",
        started="2026-08-15T09:40:00+00:00",
    )
    _write_run_record(
        runs_root, beta, "bbbb2222",
        state="finished", accepted=True, started="2026-08-14T08:00:00+00:00",
    )

    r = await client.get("/api/v1/launcher/runs")
    assert r.status_code == 200
    runs = r.json()
    assert [run["run_id"] for run in runs] == ["aaaa1111", "bbbb2222"]  # newest first

    stopped = runs[0]
    assert stopped["project_name"] == "alpha"
    assert stopped["state"] == "stopped"
    assert stopped["reason"].startswith("cost cap reached")
    assert stopped["resumable"] is True

    accepted = runs[1]
    assert accepted["resumable"] is False  # completed and accepted — nothing to resume


async def test_run_with_live_pid_is_not_resumable_but_dead_pid_is(
    client: AsyncClient, seeded_projects: Path, runs_root: Path
) -> None:
    alpha = seeded_projects / "alpha"
    _write_run_record(runs_root, alpha, "live1111", state="running", pid=os.getpid())
    _write_run_record(runs_root, alpha, "dead2222", state="running", pid=99999999)

    runs = {run["run_id"]: run for run in (await client.get("/api/v1/launcher/runs")).json()}
    assert runs["live1111"]["resumable"] is False
    assert runs["dead2222"]["resumable"] is True  # crashed mid-run: worth offering


async def test_resume_run_spawns_the_detached_resume(
    client: AsyncClient,
    seeded_projects: Path,
    runs_root: Path,
    fake_resume_spawner: _FakeResumeSpawner,
) -> None:
    alpha = seeded_projects / "alpha"
    _write_run_record(runs_root, alpha, "aaaa1111", state="stopped")

    r = await client.post(
        "/api/v1/launcher/runs/resume",
        json={"project_path": str(alpha), "run_id": "aaaa1111"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body == {"run_id": "aaaa1111", "resumed": True, "pid": 5000}

    (call,) = fake_resume_spawner.calls
    assert call["project_path"] == alpha
    assert call["run_id"] == "aaaa1111"


async def test_resume_unknown_run_404(
    client: AsyncClient,
    seeded_projects: Path,
    runs_root: Path,
    fake_resume_spawner: _FakeResumeSpawner,
) -> None:
    alpha = seeded_projects / "alpha"
    r = await client.post(
        "/api/v1/launcher/runs/resume",
        json={"project_path": str(alpha), "run_id": "nope0000"},
    )
    assert r.status_code == 404
    assert fake_resume_spawner.calls == []


async def test_resume_refuses_live_and_accepted_runs_409(
    client: AsyncClient,
    seeded_projects: Path,
    runs_root: Path,
    fake_resume_spawner: _FakeResumeSpawner,
) -> None:
    alpha = seeded_projects / "alpha"
    _write_run_record(runs_root, alpha, "live1111", state="running", pid=os.getpid())
    _write_run_record(runs_root, alpha, "done2222", state="finished", accepted=True)

    for run_id in ("live1111", "done2222"):
        r = await client.post(
            "/api/v1/launcher/runs/resume",
            json={"project_path": str(alpha), "run_id": run_id},
        )
        assert r.status_code == 409
    assert fake_resume_spawner.calls == []


async def test_resume_rejects_a_project_outside_projects_root(
    client: AsyncClient,
    seeded_projects: Path,
    runs_root: Path,
    tmp_path: Path,
    fake_resume_spawner: _FakeResumeSpawner,
) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    r = await client.post(
        "/api/v1/launcher/runs/resume",
        json={"project_path": str(outside), "run_id": "aaaa1111"},
    )
    assert r.status_code == 400
    assert fake_resume_spawner.calls == []


async def test_list_factory_runs_finds_a_project_nested_below_projects_root(
    client: AsyncClient, projects_root: Path, runs_root: Path
) -> None:
    # projects_root/group/masterwork — deeper than the first level, like a
    # project picked through the folder browser. run.json's "repo" names it.
    nested = projects_root / "group" / "masterwork"
    nested.mkdir(parents=True)
    _write_run_record(runs_root, nested, "cccc3333", state="stopped")

    runs = (await client.get("/api/v1/launcher/runs")).json()
    assert [r["run_id"] for r in runs] == ["cccc3333"]
    assert runs[0]["project_name"] == "masterwork"
    assert runs[0]["project_path"] == str(nested)


async def test_list_factory_runs_skips_repos_outside_projects_root(
    client: AsyncClient, projects_root: Path, runs_root: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    _write_run_record(runs_root, outside, "dddd4444")

    runs = (await client.get("/api/v1/launcher/runs")).json()
    assert runs == []


# --- outcome, resume hints, and the session -> run link --------------------


async def test_outcome_separates_approved_from_rejected_finished_runs(
    client: AsyncClient, seeded_projects: Path, runs_root: Path
) -> None:
    # Both end state="finished"; only `accepted` says which one actually worked.
    alpha = seeded_projects / "alpha"
    _write_run_record(runs_root, alpha, "good1111", state="finished", accepted=True)
    _write_run_record(runs_root, alpha, "bad22222", state="finished", accepted=False)
    _write_run_record(runs_root, alpha, "cap33333", state="stopped")
    _write_run_record(runs_root, alpha, "crash444", state="running", pid=99999999)

    runs = {r["run_id"]: r for r in (await client.get("/api/v1/launcher/runs")).json()}
    assert runs["good1111"]["outcome"] == "done"
    assert runs["bad22222"]["outcome"] == "failed"
    assert runs["cap33333"]["outcome"] == "stopped"
    # A record still claiming "running" whose pid is gone died; it did not finish.
    assert runs["crash444"]["outcome"] == "failed"


async def test_every_unresumable_run_says_why(
    client: AsyncClient, seeded_projects: Path, runs_root: Path
) -> None:
    alpha = seeded_projects / "alpha"
    _write_run_record(runs_root, alpha, "live1111", state="running", pid=os.getpid())
    _write_run_record(runs_root, alpha, "done2222", state="finished", accepted=True)
    _write_run_record(runs_root, alpha, "nobr3333", state="finished", branch=None)
    _write_run_record(runs_root, alpha, "open4444", state="stopped")

    runs = {r["run_id"]: r for r in (await client.get("/api/v1/launcher/runs")).json()}
    assert runs["live1111"]["resume_hint"] == "still running"
    assert "nothing to resume" in runs["done2222"]["resume_hint"]
    assert "no branch to resume onto" in runs["nobr3333"]["resume_hint"]
    # The resumable one carries no hint — the button speaks for it.
    assert runs["open4444"]["resumable"] is True
    assert runs["open4444"]["resume_hint"] is None
    for run_id in ("live1111", "done2222", "nobr3333"):
        assert runs[run_id]["resumable"] is False


async def test_run_reports_the_session_ids_its_stages_recorded(
    client: AsyncClient, seeded_projects: Path, runs_root: Path
) -> None:
    alpha = seeded_projects / "alpha"
    run_dir = _write_run_record(runs_root, alpha, "aaaa1111")
    (run_dir / "telemetry.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"stage": "plan", "session_id": "sess-plan"}),
                json.dumps({"stage": "plan", "session_id": "sess-plan"}),  # repeats collapse
                "not json at all",  # a torn last line must not lose the rest
                json.dumps({"stage": "build", "session_id": "sess-build"}),
                json.dumps({"stage": "build"}),  # no session id yet
            ]
        ),
        encoding="utf-8",
    )

    runs = (await client.get("/api/v1/launcher/runs")).json()
    assert runs[0]["session_ids"] == ["sess-plan", "sess-build"]


async def test_by_session_finds_the_run_that_spawned_a_session(
    client: AsyncClient, seeded_projects: Path, runs_root: Path
) -> None:
    alpha = seeded_projects / "alpha"
    run_dir = _write_run_record(runs_root, alpha, "aaaa1111", state="stopped")
    (run_dir / "telemetry.jsonl").write_text(
        json.dumps({"stage": "build", "session_id": "sess-build"}), encoding="utf-8"
    )

    r = await client.get("/api/v1/launcher/runs/by-session/sess-build")
    assert r.status_code == 200
    assert r.json()["run_id"] == "aaaa1111"
    assert r.json()["resumable"] is True


async def test_by_session_is_null_for_a_session_no_run_owns(
    client: AsyncClient, seeded_projects: Path, runs_root: Path
) -> None:
    # A plain chat session belongs to no run; that is not an error.
    _write_run_record(runs_root, seeded_projects / "alpha", "aaaa1111")
    r = await client.get("/api/v1/launcher/runs/by-session/sess-unknown")
    assert r.status_code == 200
    assert r.json() is None


# --- resume gate: --no-branch, detached HEAD, deleted branch ---------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def real_repo(projects_root: Path) -> Path:
    """A genuine git repo, so the branch-existence half of the gate is real."""
    repo = projects_root / "realrepo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    (repo / "README.md").write_text("x", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "first")
    _git(repo, "branch", "factory/kept")
    return repo


async def test_a_no_branch_run_resumes_onto_the_branch_it_started_from(
    client: AsyncClient, real_repo: Path, runs_root: Path
) -> None:
    # `--no-branch` records no branch of its own; the factory falls back to
    # branch_origin, so this run IS resumable and must not claim otherwise.
    run_dir = _run_dir(runs_root, real_repo, "nobr1111")
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "run_id": "nobr1111",
                "repo": str(real_repo),
                "request": "no-branch run",
                "state": "finished",
                "accepted": False,
                "branch": None,
                "branch_origin": "factory/kept",
                "started": "2026-08-15T09:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    runs = (await client.get("/api/v1/launcher/runs")).json()
    assert runs[0]["resumable"] is True
    assert runs[0]["resume_hint"] is None


async def test_a_detached_head_run_says_so_and_stays_unresumable(
    client: AsyncClient, real_repo: Path, runs_root: Path
) -> None:
    run_dir = _run_dir(runs_root, real_repo, "det22222")
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "run_id": "det22222",
                "repo": str(real_repo),
                "request": "detached run",
                "state": "stopped",
                "accepted": False,
                "branch": None,
                # A bare sha names no ref that still means "where this run was".
                "branch_origin": "430182e7b090bef3643ccc0e7f7d79ba15dc3dfb",
                "started": "2026-08-15T09:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    runs = (await client.get("/api/v1/launcher/runs")).json()
    assert runs[0]["resumable"] is False
    assert "detached HEAD" in runs[0]["resume_hint"]


async def test_a_run_whose_branch_was_deleted_reports_it_and_refuses_409(
    client: AsyncClient,
    real_repo: Path,
    runs_root: Path,
    fake_resume_spawner: _FakeResumeSpawner,
) -> None:
    _write_run_record(runs_root, real_repo, "gone3333", state="stopped", branch="factory/deleted")

    runs = (await client.get("/api/v1/launcher/runs")).json()
    assert runs[0]["resumable"] is False
    assert "is gone" in runs[0]["resume_hint"]

    # The spawn is detached, so this has to be refused here, not discovered later.
    r = await client.post(
        "/api/v1/launcher/runs/resume",
        json={"project_path": str(real_repo), "run_id": "gone3333"},
    )
    assert r.status_code == 409
    assert "is gone" in r.json()["detail"]
    assert fake_resume_spawner.calls == []


async def test_by_session_also_answers_for_the_runs_own_session(
    client: AsyncClient, seeded_projects: Path, runs_root: Path
) -> None:
    # The pipeline run itself is a session too, id `factory-<run_id>` — that is
    # the page a user lands on from the runs grid.
    alpha = seeded_projects / "alpha"
    _write_run_record(runs_root, alpha, "aaaa1111", state="stopped")

    r = await client.get("/api/v1/launcher/runs/by-session/factory-aaaa1111")
    assert r.status_code == 200
    assert r.json()["run_id"] == "aaaa1111"


async def test_a_branch_that_moved_past_the_run_is_refused_not_offered(
    client: AsyncClient,
    real_repo: Path,
    runs_root: Path,
    fake_resume_spawner: _FakeResumeSpawner,
) -> None:
    # Commits landing on the branch after the run stopped are work the run
    # never did; the factory refuses to build on them, so neither do we.
    left_at = subprocess.run(
        ["git", "-C", str(real_repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    run_dir = _write_run_record(
        runs_root, real_repo, "moved111", state="stopped", branch="factory/kept"
    )
    (run_dir / "telemetry.jsonl").write_text(
        json.dumps({"event": "commit", "phase": "plan", "payload": {"sha": left_at}}),
        encoding="utf-8",
    )
    _git(real_repo, "checkout", "-q", "factory/kept")
    (real_repo / "later.txt").write_text("added after the run", encoding="utf-8")
    _git(real_repo, "add", "-A")
    _git(real_repo, "commit", "-qm", "work the run never did")

    runs = (await client.get("/api/v1/launcher/runs")).json()
    assert runs[0]["resumable"] is False
    assert "has moved on since this run left it" in runs[0]["resume_hint"]

    r = await client.post(
        "/api/v1/launcher/runs/resume",
        json={"project_path": str(real_repo), "run_id": "moved111"},
    )
    assert r.status_code == 409
    assert fake_resume_spawner.calls == []


async def test_a_run_still_on_its_recorded_tip_stays_resumable(
    client: AsyncClient, real_repo: Path, runs_root: Path
) -> None:
    tip = subprocess.run(
        ["git", "-C", str(real_repo), "rev-parse", "factory/kept"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    run_dir = _write_run_record(
        runs_root, real_repo, "still222", state="stopped", branch="factory/kept"
    )
    (run_dir / "telemetry.jsonl").write_text(
        json.dumps({"event": "commit", "payload": {"sha": tip}}), encoding="utf-8"
    )

    runs = (await client.get("/api/v1/launcher/runs")).json()
    assert runs[0]["resumable"] is True
    assert runs[0]["resume_hint"] is None


async def test_a_resume_that_refuses_itself_is_reported_not_celebrated(
    client: AsyncClient,
    seeded_projects: Path,
    runs_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Every pre-check can pass and the factory can still say no; the caller
    # must hear that instead of a green "resumed".
    alpha = seeded_projects / "alpha"
    _write_run_record(runs_root, alpha, "argue333", state="stopped")

    def refusing_spawner(*, project_path: Path, run_id: str, log_path: Path) -> int:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("ab") as log:
            log.write(b"resuming run argue333\nerror: something the backend cannot foresee\n")
        return 6001

    app.dependency_overrides[get_resume_spawner] = lambda: refusing_spawner
    try:
        r = await client.post(
            "/api/v1/launcher/runs/resume",
            json={"project_path": str(alpha), "run_id": "argue333"},
        )
    finally:
        app.dependency_overrides.pop(get_resume_spawner, None)

    assert r.status_code == 502
    assert r.json()["detail"] == "something the backend cannot foresee"


# --- a doomed spawn, and a request someone already re-ran ------------------


async def test_launch_refuses_when_the_agent_cli_is_missing(
    client: AsyncClient,
    seeded_projects: Path,
    fake_spawner: _FakeSpawner,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Without the CLI the factory dies in under a second, long after a naive
    # endpoint has already reported success.
    monkeypatch.setattr(launcher_service.factory_launcher, "find_agent_cli", lambda: None)

    r = await client.post(
        "/api/v1/launcher/launch",
        json={"project_path": str(seeded_projects / "alpha"), "request_text": "build it"},
    )
    assert r.status_code == 502
    assert "not on the backend's PATH" in r.json()["detail"]
    assert fake_spawner.calls == []


async def test_launch_reports_a_run_that_died_on_arrival(
    client: AsyncClient, seeded_projects: Path
) -> None:
    def dying_spawner(
        *,
        project_path: Path,
        request_text: str,
        log_path: Path,
        run_id: str | None = None,
        interview: bool = False,
        workflow: str | None = None,
    ) -> int:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("ab") as log:
            log.write(b"NOT ACCEPTED - could not run the agent CLI (0 turns, $0.0000)\n")
        return 7001

    app.dependency_overrides[get_launch_spawner] = lambda: dying_spawner
    try:
        r = await client.post(
            "/api/v1/launcher/launch",
            json={"project_path": str(seeded_projects / "alpha"), "request_text": "build it"},
        )
    finally:
        app.dependency_overrides.pop(get_launch_spawner, None)

    assert r.status_code == 502
    assert "could not run the agent CLI" in r.json()["detail"]


async def test_a_rerun_supersedes_the_run_it_repeats(
    client: AsyncClient, seeded_projects: Path, runs_root: Path
) -> None:
    alpha = seeded_projects / "alpha"
    request = "request for old11111"  # what _write_run_record writes for that id
    _write_run_record(runs_root, alpha, "old11111", state="stopped", started="2026-08-15T09:00:00Z")
    new_dir = _write_run_record(runs_root, alpha, "new22222", started="2026-08-16T09:00:00Z")
    record = json.loads((new_dir / "run.json").read_text())
    record["request"] = request  # the rerun sends the old run's text verbatim
    (new_dir / "run.json").write_text(json.dumps(record), encoding="utf-8")

    runs = {r["run_id"]: r for r in (await client.get("/api/v1/launcher/runs")).json()}
    assert runs["old11111"]["superseded_by"] == "new22222"
    # The newer one is nobody's repeat.
    assert runs["new22222"]["superseded_by"] is None


async def test_runs_of_different_requests_do_not_supersede_each_other(
    client: AsyncClient, seeded_projects: Path, runs_root: Path
) -> None:
    alpha = seeded_projects / "alpha"
    _write_run_record(runs_root, alpha, "aaa11111", started="2026-08-15T09:00:00Z")
    _write_run_record(runs_root, alpha, "bbb22222", started="2026-08-16T09:00:00Z")

    runs = (await client.get("/api/v1/launcher/runs")).json()
    assert all(r["superseded_by"] is None for r in runs)


# --- dismissing a run out of the list --------------------------------------


async def test_dismiss_marks_a_run_and_restore_brings_it_back(
    client: AsyncClient, seeded_projects: Path, runs_root: Path
) -> None:
    alpha = seeded_projects / "alpha"
    _write_run_record(runs_root, alpha, "aaaa1111", state="stopped")
    body = {"project_path": str(alpha), "run_id": "aaaa1111"}

    r = await client.post("/api/v1/launcher/runs/dismiss", json=body)
    assert r.status_code == 200
    assert r.json()["dismissed"] is True
    assert (await client.get("/api/v1/launcher/runs")).json()[0]["dismissed"] is True

    # Dismissing twice is not an error — the row is already there.
    assert (await client.post("/api/v1/launcher/runs/dismiss", json=body)).status_code == 200

    r = await client.post("/api/v1/launcher/runs/restore", json=body)
    assert r.status_code == 200
    assert r.json()["dismissed"] is False
    assert (await client.get("/api/v1/launcher/runs")).json()[0]["dismissed"] is False


async def test_dismissing_leaves_the_run_dir_untouched(
    client: AsyncClient, seeded_projects: Path, runs_root: Path
) -> None:
    # The factory owns those files; a dismissal is masterwork's own note.
    alpha = seeded_projects / "alpha"
    run_dir = _write_run_record(runs_root, alpha, "aaaa1111", state="stopped")
    before = sorted(p.name for p in run_dir.iterdir())
    record_before = (run_dir / "run.json").read_text()

    await client.post(
        "/api/v1/launcher/runs/dismiss",
        json={"project_path": str(alpha), "run_id": "aaaa1111"},
    )

    assert sorted(p.name for p in run_dir.iterdir()) == before
    assert (run_dir / "run.json").read_text() == record_before


async def test_dismiss_rejects_an_unknown_run_and_an_outside_project(
    client: AsyncClient, seeded_projects: Path, runs_root: Path, tmp_path: Path
) -> None:
    alpha = seeded_projects / "alpha"
    r = await client.post(
        "/api/v1/launcher/runs/dismiss",
        json={"project_path": str(alpha), "run_id": "nope0000"},
    )
    assert r.status_code == 404

    outside = tmp_path / "elsewhere"
    outside.mkdir()
    r = await client.post(
        "/api/v1/launcher/runs/dismiss",
        json={"project_path": str(outside), "run_id": "aaaa1111"},
    )
    assert r.status_code == 400


async def test_launch_passes_a_workflow_through_and_omits_it_by_default(
    client: AsyncClient, seeded_projects: Path, fake_spawner: _FakeSpawner
) -> None:
    alpha = seeded_projects / "alpha"
    await client.post(
        "/api/v1/launcher/launch",
        json={"project_path": str(alpha), "request_text": "is this done?", "workflow": "scout"},
    )
    assert fake_spawner.calls[0]["workflow"] == "scout"

    await client.post(
        "/api/v1/launcher/launch",
        json={"project_path": str(alpha), "request_text": "build it"},
    )
    assert fake_spawner.calls[1]["workflow"] is None


async def test_launch_rejects_a_workflow_the_factory_does_not_have(
    client: AsyncClient, seeded_projects: Path, fake_spawner: _FakeSpawner
) -> None:
    r = await client.post(
        "/api/v1/launcher/launch",
        json={
            "project_path": str(seeded_projects / "alpha"),
            "request_text": "x",
            "workflow": "sabotage",
        },
    )
    assert r.status_code == 422
    assert fake_spawner.calls == []


async def test_runs_report_the_workflow_they_ran(
    client: AsyncClient, seeded_projects: Path, runs_root: Path
) -> None:
    alpha = seeded_projects / "alpha"
    run_dir = _write_run_record(runs_root, alpha, "scout111", state="finished", accepted=True)
    record = json.loads((run_dir / "run.json").read_text())
    record["workflow_name"] = "scout"
    (run_dir / "run.json").write_text(json.dumps(record), encoding="utf-8")

    assert (await client.get("/api/v1/launcher/runs")).json()[0]["workflow"] == "scout"
