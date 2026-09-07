"""POST /assets/{id}/migrate against temp Claude, Codex and generic trees."""

from __future__ import annotations

import shutil
from pathlib import Path
from urllib.parse import quote

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.api.deps import get_providers
from app.main import app
from tests.helpers import providers_for


@pytest.fixture
def trees(tmp_path: Path, claude_tree: tuple[Path, Path]) -> dict[str, Path]:
    generic = tmp_path / "agents" / "skills"
    codex = tmp_path / "codex" / "skills"
    (codex / "theirs").mkdir(parents=True)
    (codex / "theirs" / "SKILL.md").write_text("---\nname: theirs\n---\ncodex skill\n")
    return {"claude": claude_tree[0], "generic": generic, "codex": codex}


@pytest_asyncio.fixture
async def migrate_client(
    client: AsyncClient, claude_tree: tuple[Path, Path], trees: dict[str, Path]
) -> AsyncClient:
    app.dependency_overrides[get_providers] = lambda: providers_for(
        claude_tree, generic_root=trees["generic"], codex_root=trees["codex"]
    )
    return client


def _migrate_url(asset_id: str) -> str:
    return f"/api/v1/assets/{quote(asset_id, safe='')}/migrate"


async def test_list_reports_agents_per_asset(migrate_client: AsyncClient) -> None:
    r = await migrate_client.get("/api/v1/assets")
    assert r.status_code == 200
    by_id = {a["id"]: a for a in r.json()}
    assert by_id["claude:skill:frontend-dev"]["agents"] == ["claude"]
    assert by_id["codex:skill:theirs"]["agents"] == ["codex"]
    assert by_id["codex:skill:theirs"]["provider"] == "codex"


async def test_migrate_claude_skill_to_generic(
    migrate_client: AsyncClient, trees: dict[str, Path]
) -> None:
    # A project linking the old id follows the skill to its new id.
    created = await migrate_client.post("/api/v1/projects", json={"name": "P", "goal": "ship"})
    assert created.status_code == 201, created.text
    project_id = created.json()["id"]
    linked = await migrate_client.patch(
        f"/api/v1/projects/{project_id}",
        json={"asset_ids": ["claude:skill:frontend-dev"]},
    )
    assert linked.status_code == 200, linked.text

    r = await migrate_client.post(_migrate_url("claude:skill:frontend-dev"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["previous_id"] == "claude:skill:frontend-dev"
    assert body["asset"]["id"] == "generic:skill:frontend-dev"
    assert body["asset"]["provider"] == "generic"
    assert sorted(body["asset"]["agents"]) == ["claude", "codex"]
    assert body["linked_agents"] == ["claude", "codex"]
    assert body["skipped_agents"] == []
    assert body["claude_only_keys"] == []
    assert body["relinked_projects"] == 1
    assert body["asset"]["path"] == str(trees["generic"] / "frontend-dev" / "SKILL.md")

    # On disk once, linked from both agents' dirs.
    assert (trees["claude"] / "frontend-dev").is_symlink()
    assert (trees["codex"] / "frontend-dev").is_symlink()

    # The old id is gone from the list; the new one is there once.
    listed = await migrate_client.get("/api/v1/assets", params={"kind": "skill"})
    ids = [a["id"] for a in listed.json()]
    assert "claude:skill:frontend-dev" not in ids
    assert ids.count("generic:skill:frontend-dev") == 1

    project = await migrate_client.get(f"/api/v1/projects/{project_id}")
    assert project.json()["asset_ids"] == ["generic:skill:frontend-dev"]


async def test_migrate_codex_skill_to_generic(migrate_client: AsyncClient) -> None:
    r = await migrate_client.post(_migrate_url("codex:skill:theirs"))
    assert r.status_code == 200, r.text
    assert r.json()["asset"]["id"] == "generic:skill:theirs"
    assert r.json()["linked_agents"] == ["claude", "codex"]


async def test_migrate_rejects_agents_and_generic_skills(migrate_client: AsyncClient) -> None:
    r = await migrate_client.post(_migrate_url("claude:agent:architect"))
    assert r.status_code == 409

    ok = await migrate_client.post(_migrate_url("claude:skill:backend-dev"))
    assert ok.status_code == 200
    again = await migrate_client.post(_migrate_url("generic:skill:backend-dev"))
    assert again.status_code == 409


async def test_migrate_conflicts_when_generic_holds_a_different_copy(
    migrate_client: AsyncClient, trees: dict[str, Path]
) -> None:
    (trees["generic"] / "backend-dev").mkdir(parents=True)
    (trees["generic"] / "backend-dev" / "SKILL.md").write_text("---\nname: backend-dev\n---\nold\n")

    r = await migrate_client.post(_migrate_url("claude:skill:backend-dev"))
    assert r.status_code == 409
    assert "differs" in r.json()["detail"]
    assert not (trees["claude"] / "backend-dev").is_symlink()

    # Asked for explicitly, the generic copy gives way to this one.
    r = await migrate_client.post(
        _migrate_url("claude:skill:backend-dev"), json={"replace_generic": True}
    )
    assert r.status_code == 200, r.text
    assert r.json()["replaced_generic"] is True
    assert r.json()["adopted"] is False
    assert "FastAPI" in (trees["generic"] / "backend-dev" / "SKILL.md").read_text()
    assert (trees["claude"] / "backend-dev").is_symlink()


async def test_migrate_adopts_an_identical_generic_copy(
    migrate_client: AsyncClient, trees: dict[str, Path]
) -> None:
    shutil.copytree(trees["claude"] / "backend-dev", trees["generic"] / "backend-dev")

    r = await migrate_client.post(_migrate_url("claude:skill:backend-dev"))
    assert r.status_code == 200, r.text
    assert r.json()["adopted"] is True
    assert r.json()["asset"]["id"] == "generic:skill:backend-dev"
    assert (trees["claude"] / "backend-dev").is_symlink()


async def test_migrate_unknown_asset_is_404(migrate_client: AsyncClient) -> None:
    r = await migrate_client.post(_migrate_url("claude:skill:nope"))
    assert r.status_code == 404
