"""Project CRUD HTTP endpoints against the real test database."""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models.chat import ChatSession


async def _new_project(client: AsyncClient, name: str = "Deploy", goal: str = "") -> dict:
    r = await client.post("/api/v1/projects", json={"name": name, "goal": goal})
    assert r.status_code == 201
    return r.json()


async def test_create_project_defaults(client: AsyncClient) -> None:
    r = await client.post("/api/v1/projects", json={"name": "Azure repo"})
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Azure repo"
    assert body["goal"] == ""
    assert body["flow_mermaid"] is None
    assert body["asset_ids"] == []
    assert body["scenario"] == ""


async def test_create_project_with_goal(client: AsyncClient) -> None:
    body = await _new_project(client, "Repo", "Auto-deploy to Azure")
    assert body["goal"] == "Auto-deploy to Azure"


async def test_get_project(client: AsyncClient) -> None:
    pid = (await _new_project(client))["id"]
    r = await client.get(f"/api/v1/projects/{pid}")
    assert r.status_code == 200
    assert r.json()["id"] == pid


async def test_get_unknown_project_404(client: AsyncClient) -> None:
    r = await client.get("/api/v1/projects/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


async def test_list_projects_newest_first(client: AsyncClient) -> None:
    a = (await _new_project(client, "A"))["id"]
    b = (await _new_project(client, "B"))["id"]
    await client.patch(f"/api/v1/projects/{a}", json={"name": "A2"})  # bump A

    ids = [p["id"] for p in (await client.get("/api/v1/projects")).json()]
    assert ids[0] == a
    assert set(ids) == {a, b}


async def test_patch_partial_leaves_other_fields(client: AsyncClient) -> None:
    pid = (await _new_project(client, "Orig", "Orig goal"))["id"]
    # Set asset_ids only; name/goal must be untouched.
    r = await client.patch(f"/api/v1/projects/{pid}", json={"asset_ids": ["claude:skill:x"]})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Orig"
    assert body["goal"] == "Orig goal"
    assert body["asset_ids"] == ["claude:skill:x"]


async def test_patch_flow_mermaid_set_then_null(client: AsyncClient) -> None:
    pid = (await _new_project(client))["id"]
    r = await client.patch(f"/api/v1/projects/{pid}", json={"flow_mermaid": "flowchart TD\n A-->B"})
    assert r.json()["flow_mermaid"] == "flowchart TD\n A-->B"

    # Explicit null clears it (flow_mermaid is the one nullable field).
    r = await client.patch(f"/api/v1/projects/{pid}", json={"flow_mermaid": None})
    assert r.status_code == 200
    assert r.json()["flow_mermaid"] is None


async def test_patch_empty_body_is_noop(client: AsyncClient) -> None:
    pid = (await _new_project(client, "Keep", "Keep goal"))["id"]
    r = await client.patch(f"/api/v1/projects/{pid}", json={})
    assert r.status_code == 200
    assert r.json()["name"] == "Keep"
    assert r.json()["goal"] == "Keep goal"


async def test_patch_unknown_project_404(client: AsyncClient) -> None:
    r = await client.patch(
        "/api/v1/projects/00000000-0000-0000-0000-000000000000", json={"name": "x"}
    )
    assert r.status_code == 404


async def test_delete_project_204(client: AsyncClient) -> None:
    pid = (await _new_project(client))["id"]
    r = await client.delete(f"/api/v1/projects/{pid}")
    assert r.status_code == 204
    assert (await client.get(f"/api/v1/projects/{pid}")).status_code == 404


async def test_delete_unknown_project_404(client: AsyncClient) -> None:
    r = await client.delete("/api/v1/projects/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


async def test_delete_project_cascades_sessions(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    pid = (await _new_project(client))["id"]
    await client.post("/api/v1/chat/sessions", json={"project_id": pid})
    await client.post("/api/v1/chat/sessions", json={"project_id": pid})

    async with session_factory() as db:
        assert (await db.execute(select(func.count()).select_from(ChatSession))).scalar() == 2

    r = await client.delete(f"/api/v1/projects/{pid}")
    assert r.status_code == 204

    async with session_factory() as db:
        assert (await db.execute(select(func.count()).select_from(ChatSession))).scalar() == 0
