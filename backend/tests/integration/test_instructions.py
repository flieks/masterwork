"""Global instructions endpoints (CLAUDE.md, AGENTS.md) against temp files."""

from __future__ import annotations

from pathlib import Path

import pytest_asyncio
from httpx import AsyncClient

from app.api.deps import get_instructions_paths
from app.api.v1.instructions.service import InstructionsPaths
from app.main import app

URL = "/api/v1/instructions"


@pytest_asyncio.fixture
async def paths(client: AsyncClient, tmp_path: Path) -> InstructionsPaths:
    resolved = InstructionsPaths(
        claude=tmp_path / "claude-home" / "CLAUDE.md",
        codex=tmp_path / "codex-home" / "AGENTS.md",
        codex_override=tmp_path / "codex-home" / "AGENTS.override.md",
    )
    app.dependency_overrides[get_instructions_paths] = lambda: resolved
    return resolved


async def test_get_missing_file(client: AsyncClient, paths: InstructionsPaths) -> None:
    r = await client.get(URL)
    assert r.status_code == 200
    assert r.json() == {
        "agent": "claude",
        "file_name": "CLAUDE.md",
        "path": str(paths.claude),
        "content": "",
        "exists": False,
        "updated_at": None,
        "shadowed_by": None,
        "same_file_as": None,
    }


async def test_get_existing_file(client: AsyncClient, paths: InstructionsPaths) -> None:
    paths.claude.parent.mkdir(parents=True)
    paths.claude.write_text("# Global preferences\n", encoding="utf-8")

    r = await client.get(URL)
    assert r.status_code == 200
    body = r.json()
    assert body["exists"] is True
    assert body["content"] == "# Global preferences\n"
    assert body["updated_at"] is not None


async def test_put_creates_file(client: AsyncClient, paths: InstructionsPaths) -> None:
    r = await client.put(URL, json={"content": "# Rules\n\nBe brief.\n"})
    assert r.status_code == 200
    assert r.json()["exists"] is True
    assert paths.claude.read_text(encoding="utf-8") == "# Rules\n\nBe brief.\n"
    assert not paths.codex.exists()


async def test_put_overwrites_file(client: AsyncClient, paths: InstructionsPaths) -> None:
    paths.claude.parent.mkdir(parents=True)
    paths.claude.write_text("old\n", encoding="utf-8")

    r = await client.put(URL, json={"content": "new\n"})
    assert r.status_code == 200
    assert r.json()["content"] == "new\n"
    assert paths.claude.read_text(encoding="utf-8") == "new\n"


async def test_put_unwritable_path_500(client: AsyncClient, tmp_path: Path) -> None:
    # A file where a parent directory is expected: mkdir fails with NotADirectoryError.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a dir", encoding="utf-8")
    app.dependency_overrides[get_instructions_paths] = lambda: InstructionsPaths(
        claude=blocker / "CLAUDE.md",
        codex=tmp_path / "AGENTS.md",
        codex_override=tmp_path / "AGENTS.override.md",
    )

    r = await client.put(URL, json={"content": "x"})
    assert r.status_code == 500
    assert "could not write" in r.json()["detail"]


async def test_codex_reads_and_writes_agents_md(
    client: AsyncClient, paths: InstructionsPaths
) -> None:
    r = await client.put(f"{URL}?agent=codex", json={"content": "# Codex rules\n"})
    assert r.status_code == 200
    body = r.json()
    assert body["agent"] == "codex"
    assert body["file_name"] == "AGENTS.md"
    assert body["path"] == str(paths.codex)
    assert body["shadowed_by"] is None
    assert paths.codex.read_text(encoding="utf-8") == "# Codex rules\n"
    assert not paths.claude.exists()

    r = await client.get(f"{URL}?agent=codex")
    assert r.json()["content"] == "# Codex rules\n"


async def test_a_non_empty_override_shadows_agents_md(
    client: AsyncClient, paths: InstructionsPaths
) -> None:
    paths.codex.parent.mkdir(parents=True)
    paths.codex.write_text("# base\n", encoding="utf-8")
    paths.codex_override.write_text("  \n", encoding="utf-8")

    # Blank does not count: Codex uses the first NON-empty file.
    assert (await client.get(f"{URL}?agent=codex")).json()["shadowed_by"] is None

    paths.codex_override.write_text("# temporary override\n", encoding="utf-8")
    body = (await client.get(f"{URL}?agent=codex")).json()
    assert body["shadowed_by"] == str(paths.codex_override)
    # Only Codex reads the override.
    assert (await client.get(URL)).json()["shadowed_by"] is None


async def test_same_file_as_names_the_other_agents_path_when_linked(
    client: AsyncClient, paths: InstructionsPaths
) -> None:
    paths.claude.parent.mkdir(parents=True)
    paths.claude.write_text("# shared\n", encoding="utf-8")
    paths.codex.parent.mkdir(parents=True)
    paths.codex.symlink_to(paths.claude)

    assert (await client.get(URL)).json()["same_file_as"] == str(paths.codex)
    assert (await client.get(f"{URL}?agent=codex")).json()["same_file_as"] == str(paths.claude)


async def test_separate_files_are_not_the_same(
    client: AsyncClient, paths: InstructionsPaths
) -> None:
    for path in (paths.claude, paths.codex):
        path.parent.mkdir(parents=True)
        path.write_text("# same text, different file\n", encoding="utf-8")

    assert (await client.get(f"{URL}?agent=codex")).json()["same_file_as"] is None


async def test_unknown_agent_is_422(client: AsyncClient, paths: InstructionsPaths) -> None:
    assert (await client.get(f"{URL}?agent=cursor")).status_code == 422
