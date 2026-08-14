"""Launcher endpoints against the real test database. The subprocess spawn is
a fake dependency override — no test here ever forks a real process."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.deps import get_launch_spawner
from app.db.models.launcher import SessionLaunch
from app.main import app


class _FakeSpawner:
    """Records every call instead of forking; hands back an incrementing pid."""

    def __init__(self) -> None:
        self.calls: list[tuple[Path, str, Path]] = []

    def __call__(self, project_path: Path, request_text: str, log_path: Path) -> int:
        self.calls.append((project_path, request_text, log_path))
        return 4242 + len(self.calls) - 1


@pytest.fixture
def fake_spawner() -> Iterator[_FakeSpawner]:
    spawner = _FakeSpawner()
    app.dependency_overrides[get_launch_spawner] = lambda: spawner
    try:
        yield spawner
    finally:
        app.dependency_overrides.pop(get_launch_spawner, None)


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

    assert len(fake_spawner.calls) == 1
    called_path, called_request, log_path = fake_spawner.calls[0]
    assert called_path == alpha
    assert called_request == "add a widget"
    assert log_path.name == f"{body['id']}.log"

    async with session_factory() as db:
        row = await db.get(SessionLaunch, body["id"])
        assert row is not None
        assert row.project_path == str(alpha)
        assert row.pid == 4242


async def test_launch_interview_mode_stores_and_returns_it(
    client: AsyncClient, seeded_projects: Path, fake_spawner: _FakeSpawner
) -> None:
    alpha = seeded_projects / "alpha"
    r = await client.post(
        "/api/v1/launcher/launch",
        json={"project_path": str(alpha), "request_text": "x", "mode": "interview"},
    )
    assert r.status_code == 200
    assert r.json()["mode"] == "interview"
    # No mode flag reaches the spawner — argv is identical regardless of mode.
    called_path, called_request, _ = fake_spawner.calls[0]
    assert called_path == alpha
    assert called_request == "x"


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
