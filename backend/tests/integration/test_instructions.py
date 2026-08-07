"""Global CLAUDE.md endpoints against a temp file."""

from __future__ import annotations

from pathlib import Path

import pytest_asyncio
from httpx import AsyncClient

from app.api.deps import get_instructions_path
from app.main import app

URL = "/api/v1/instructions"


@pytest_asyncio.fixture
async def instructions_path(client: AsyncClient, tmp_path: Path) -> Path:
    path = tmp_path / "claude-home" / "CLAUDE.md"
    app.dependency_overrides[get_instructions_path] = lambda: path
    return path


async def test_get_missing_file(client: AsyncClient, instructions_path: Path) -> None:
    r = await client.get(URL)
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "path": str(instructions_path),
        "content": "",
        "exists": False,
        "updated_at": None,
    }


async def test_get_existing_file(client: AsyncClient, instructions_path: Path) -> None:
    instructions_path.parent.mkdir(parents=True)
    instructions_path.write_text("# Global preferences\n", encoding="utf-8")

    r = await client.get(URL)
    assert r.status_code == 200
    body = r.json()
    assert body["exists"] is True
    assert body["content"] == "# Global preferences\n"
    assert body["updated_at"] is not None


async def test_put_creates_file(client: AsyncClient, instructions_path: Path) -> None:
    r = await client.put(URL, json={"content": "# Rules\n\nBe brief.\n"})
    assert r.status_code == 200
    assert r.json()["exists"] is True
    assert instructions_path.read_text(encoding="utf-8") == "# Rules\n\nBe brief.\n"


async def test_put_overwrites_file(client: AsyncClient, instructions_path: Path) -> None:
    instructions_path.parent.mkdir(parents=True)
    instructions_path.write_text("old\n", encoding="utf-8")

    r = await client.put(URL, json={"content": "new\n"})
    assert r.status_code == 200
    assert r.json()["content"] == "new\n"
    assert instructions_path.read_text(encoding="utf-8") == "new\n"


async def test_put_unwritable_path_500(client: AsyncClient, tmp_path: Path) -> None:
    # A file where a parent directory is expected: mkdir fails with NotADirectoryError.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir", encoding="utf-8")
    app.dependency_overrides[get_instructions_path] = lambda: blocker / "CLAUDE.md"

    r = await client.put(URL, json={"content": "x"})
    assert r.status_code == 500
    assert "could not write" in r.json()["detail"]
