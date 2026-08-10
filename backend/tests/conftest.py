"""Shared test fixtures.

Integration tests run against a real database, never a mock — SQLite in a
throwaway file by default, or a dedicated `masterwork_test` Postgres database
when DATABASE_URL points at Postgres. Never the dev database either way.
Asset providers and the claude runner are overridden per test.
"""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.pool import NullPool

from app.api.deps import get_db
from app.config import settings
from app.db.base import Base
from app.db.session import make_engine
from app.main import app

TEST_DB_NAME = "masterwork_test"
_IS_POSTGRES = settings.database_url.startswith("postgresql")
_BASE_URL = settings.database_url.rsplit("/", 1)[0]
_TMPDIR = tempfile.TemporaryDirectory(prefix="masterwork-test-")

TEST_DB_URL = (
    f"{_BASE_URL}/{TEST_DB_NAME}"
    if _IS_POSTGRES
    else f"sqlite+aiosqlite:///{Path(_TMPDIR.name) / 'test.db'}"
)


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True, text=True)


@pytest.fixture(scope="session", autouse=True)
def _test_database() -> Iterator[None]:
    if not _IS_POSTGRES:
        try:
            yield
        finally:
            _TMPDIR.cleanup()
        return

    # Guard: only ever touch a local database whose name ends with _test.
    assert "localhost" in TEST_DB_URL, "refusing to run against a non-local DB"
    assert TEST_DB_NAME.endswith("_test")

    _run(["dropdb", "--if-exists", TEST_DB_NAME])
    _run(["createdb", TEST_DB_NAME])
    try:
        yield
    finally:
        _run(["dropdb", "--if-exists", TEST_DB_NAME])


@pytest_asyncio.fixture
async def engine() -> AsyncIterator[object]:
    eng = make_engine(TEST_DB_URL, poolclass=NullPool)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield eng
    finally:
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine: object) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)  # type: ignore[arg-type]


@pytest_asyncio.fixture
async def client(session_factory: async_sessionmaker) -> AsyncIterator[AsyncClient]:
    async def _override_get_db() -> AsyncIterator[object]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def claude_tree(tmp_path: Path) -> tuple[Path, Path]:
    """A minimal Claude asset tree: two skills and one agent."""
    skills_root = tmp_path / "skills"
    agents_root = tmp_path / "agents"
    skills_root.mkdir()
    agents_root.mkdir()

    (skills_root / "frontend-dev").mkdir()
    (skills_root / "frontend-dev" / "SKILL.md").write_text(
        "---\nname: frontend-dev\ndescription: React frontend guidelines.\n---\n\n"
        "# Frontend\nUse React and Vite.\n",
        encoding="utf-8",
    )
    (skills_root / "backend-dev").mkdir()
    (skills_root / "backend-dev" / "SKILL.md").write_text(
        "---\nname: backend-dev\ndescription: Python FastAPI guidelines.\n---\n\n"
        "# Backend\nUse FastAPI and Postgres.\n",
        encoding="utf-8",
    )
    (agents_root / "architect.md").write_text(
        '---\nname: "architect"\ndescription: "Plans features before building."\n---\n\n'
        "# Architect\nDesign first.\n",
        encoding="utf-8",
    )
    return skills_root, agents_root


@pytest.fixture
def role_tree(tmp_path: Path) -> Path:
    """A factory role store with two roles — never the real ~/.masterwork/agents."""
    import json

    store = tmp_path / "masterwork-agents"
    plan = store / "plan"
    plan.mkdir(parents=True)
    (plan / "system.md").write_text(
        "You are the PLAN stage of a deterministic pipeline.\nWrite plan.md.\n",
        encoding="utf-8",
    )
    (plan / "user.md").write_text(
        "Request: {{request}}\nRepo: {{repo}}\nPlan it.\n", encoding="utf-8"
    )
    (plan / "role.json").write_text(
        json.dumps(
            {
                "model": "opus",
                "purpose": "Turns a request into an implementation plan.",
                "writes": ["plan.md", "docs/specs/**"],
                "disallowed_tools": ["Bash", "Write"],
            }
        ),
        encoding="utf-8",
    )
    build = store / "build"
    build.mkdir()
    (build / "system.md").write_text(
        "You are the BUILD stage. Implement the plan.\n", encoding="utf-8"
    )
    (build / "user.md").write_text("Plan: {{plan}}\nBuild it.\n", encoding="utf-8")
    # No role.json: the description and model must still degrade gracefully.
    return store


@pytest.fixture
def plugin_tree(tmp_path: Path) -> Path:
    """A plugins root with one installed plugin shipping a skill and an agent."""
    import json

    plugins_root = tmp_path / "plugins"
    install = plugins_root / "cache" / "official" / "vercel" / "1.0.0"
    (install / "skills" / "bootstrap").mkdir(parents=True)
    (install / "skills" / "bootstrap" / "SKILL.md").write_text(
        "---\nname: bootstrap\ndescription: Provision Vercel resources.\n---\n\n"
        "# Bootstrap\nLink and provision.\n",
        encoding="utf-8",
    )
    (install / "agents").mkdir()
    (install / "agents" / "deployment-expert.md").write_text(
        "---\nname: deployment-expert\ndescription: Vercel deploy specialist.\n---\n\n# Deploys\n",
        encoding="utf-8",
    )
    (install / "agents" / "deployment-expert.md.tmpl").write_text("template", encoding="utf-8")
    (plugins_root / "installed_plugins.json").write_text(
        json.dumps(
            {
                "version": 2,
                "plugins": {"vercel@official": [{"scope": "user", "installPath": str(install)}]},
            }
        ),
        encoding="utf-8",
    )
    return plugins_root
