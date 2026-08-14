"""Work-item HTTP endpoints against the real test database + a MockTransport
DevOps client — zero live DevOps calls."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.deps import get_devops_client_factory
from app.db.models.work import WorkItemSession
from app.main import app
from app.providers.azuredevops import AzureDevOpsClient

_ITEMS: dict[int, dict[str, object]] = {
    101: {
        "System.Title": "Fix login bug",
        "System.Description": "<p>Users cannot <b>log in</b> on Safari.</p>",
        "Microsoft.VSTS.Common.AcceptanceCriteria": "<ul><li>Login works on Safari</li></ul>",
        "System.State": "Active",
        "System.IterationPath": "Sprint 1",
        "System.WorkItemType": "Bug",
        "Microsoft.VSTS.Common.Priority": 2,
        "System.Tags": "auth; urgent",
        "System.ChangedDate": "2026-08-01T10:00:00Z",
    },
    102: {
        "System.Title": "Add dark mode",
        "System.Description": "<p>Add a dark theme toggle.</p>",
        "Microsoft.VSTS.Common.AcceptanceCriteria": None,
        "System.State": "New",
        "System.IterationPath": "Sprint 2",
        "System.WorkItemType": "Feature",
        "Microsoft.VSTS.Common.Priority": 3,
        "System.Tags": None,
        "System.ChangedDate": "2026-08-02T10:00:00Z",
    },
    103: {
        "System.Title": "Refactor auth service",
        "System.Description": "<p>Split the auth module.</p>",
        "Microsoft.VSTS.Common.AcceptanceCriteria": "<p>Tests still pass.</p>",
        "System.State": "Active",
        "System.IterationPath": "Sprint 1",
        "System.WorkItemType": "Task",
        "Microsoft.VSTS.Common.Priority": 1,
        "System.Tags": "auth",
        "System.ChangedDate": "2026-08-01T09:00:00Z",
    },
}


@pytest.fixture(autouse=True)
def _pat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_DEVOPS_PAT", "fake-pat")


def _devops_handler(
    items: dict[int, dict[str, object]],
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/_apis/wit/wiql"):
            return httpx.Response(200, json={"workItems": [{"id": i} for i in items]})
        if request.url.path.endswith("/_apis/wit/workitemsbatch"):
            body = json.loads(request.content)
            value = [
                {"id": i, "url": f"https://dev.azure.com/_apis/wit/workItems/{i}", "fields": fields}
                for i, fields in items.items()
                if i in body["ids"]
            ]
            return httpx.Response(200, json={"value": value})
        raise AssertionError(f"unexpected request: {request.url}")

    return handler


def _use_devops(items: dict[int, dict[str, object]]) -> None:
    handler = _devops_handler(items)

    def factory(source: object) -> AzureDevOpsClient:
        return AzureDevOpsClient(
            org_url=source.org_url,  # type: ignore[attr-defined]
            project=source.project,  # type: ignore[attr-defined]
            secret_ref=source.secret_ref,  # type: ignore[attr-defined]
            transport=httpx.MockTransport(handler),
        )

    app.dependency_overrides[get_devops_client_factory] = lambda: factory


async def _new_source(client: AsyncClient, project: str = "widgets") -> dict:
    r = await client.post(
        "/api/v1/work/sources",
        json={"org_url": "https://dev.azure.com/acme", "project": project},
    )
    assert r.status_code == 201
    return r.json()


# --- source CRUD + validation ------------------------------------------------


async def test_create_source_validates_org_url(client: AsyncClient) -> None:
    r = await client.post(
        "/api/v1/work/sources", json={"org_url": "https://example.com/foo", "project": "widgets"}
    )
    assert r.status_code == 400


async def test_create_source_accepts_a_devops_org_url(client: AsyncClient) -> None:
    body = await _new_source(client)
    assert body["org_url"] == "https://dev.azure.com/acme"
    assert body["project"] == "widgets"
    assert body["provider"] == "azuredevops"
    assert body["secret_ref"] == "AZURE_DEVOPS_PAT"
    assert body["last_sync_at"] is None


async def test_list_sources(client: AsyncClient) -> None:
    await _new_source(client, "widgets")
    r = await client.get("/api/v1/work/sources")
    assert r.status_code == 200
    assert len(r.json()) == 1


async def test_sync_unknown_source_404(client: AsyncClient) -> None:
    r = await client.post("/api/v1/work/sources/00000000-0000-0000-0000-000000000000/sync")
    assert r.status_code == 404


# --- sync: insert then update -------------------------------------------------


async def test_sync_inserts_and_converts_html_to_markdown(client: AsyncClient) -> None:
    _use_devops(dict(_ITEMS))
    source = await _new_source(client)

    r = await client.post(f"/api/v1/work/sources/{source['id']}/sync")
    assert r.status_code == 200
    assert r.json() == {"fetched": 3, "inserted": 3, "updated": 0}

    items = (await client.get("/api/v1/work/items")).json()
    assert len(items) == 3
    fixed_login = next(i for i in items if i["external_id"] == 101)
    assert "**log in**" in fixed_login["description_md"]
    assert "Login works on Safari" in fixed_login["acceptance_md"]
    assert fixed_login["tags"] == ["auth", "urgent"]

    dark_mode = next(i for i in items if i["external_id"] == 102)
    assert dark_mode["acceptance_md"] is None
    assert dark_mode["tags"] is None


async def test_second_sync_updates_changed_items_without_duplicating_rows(
    client: AsyncClient,
) -> None:
    items = dict(_ITEMS)
    _use_devops(items)
    source = await _new_source(client)
    await client.post(f"/api/v1/work/sources/{source['id']}/sync")

    # Mutate the upstream payload — the second sync must pick this up.
    items[102] = {
        **items[102],
        "System.Title": "Add dark mode toggle",
        "System.State": "Active",
        "System.ChangedDate": "2026-08-05T10:00:00Z",
    }

    r = await client.post(f"/api/v1/work/sources/{source['id']}/sync")
    assert r.status_code == 200
    body = r.json()
    assert body == {"fetched": 3, "inserted": 0, "updated": 3}

    all_items = (await client.get("/api/v1/work/items")).json()
    assert len(all_items) == 3  # row count stays 3, nothing duplicated
    updated = next(i for i in all_items if i["external_id"] == 102)
    assert updated["title"] == "Add dark mode toggle"
    assert updated["state"] == "Active"

    sources = (await client.get("/api/v1/work/sources")).json()
    assert sources[0]["last_sync_at"] is not None


# --- filters -------------------------------------------------------------


async def test_list_items_filters_by_state(client: AsyncClient) -> None:
    _use_devops(dict(_ITEMS))
    source = await _new_source(client)
    await client.post(f"/api/v1/work/sources/{source['id']}/sync")

    r = await client.get("/api/v1/work/items", params={"state": "Active"})
    assert r.status_code == 200
    ids = {i["external_id"] for i in r.json()}
    assert ids == {101, 103}


async def test_list_items_filters_by_source_id(client: AsyncClient) -> None:
    _use_devops(dict(_ITEMS))
    source_a = await _new_source(client, "widgets")
    await client.post(f"/api/v1/work/sources/{source_a['id']}/sync")
    source_b = await _new_source(client, "gizmos")  # never synced

    r = await client.get("/api/v1/work/items", params={"source_id": source_a["id"]})
    assert len(r.json()) == 3
    r = await client.get("/api/v1/work/items", params={"source_id": source_b["id"]})
    assert r.json() == []


# --- start endpoint -----------------------------------------------------


async def test_start_work_item_assembles_prompt_and_writes_link_row(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    _use_devops(dict(_ITEMS))
    source = await _new_source(client)
    await client.post(f"/api/v1/work/sources/{source['id']}/sync")
    items = (await client.get("/api/v1/work/items")).json()
    login_item = next(i for i in items if i["external_id"] == 101)

    r = await client.post(f"/api/v1/work/items/{login_item['id']}/start")
    assert r.status_code == 200
    body = r.json()

    assert body["launched"] is False
    assert body["session_id"] is None
    assert "Bug #101: Fix login bug" in body["prompt"]
    assert login_item["external_url"] in body["prompt"]
    assert "## Story" in body["prompt"]
    assert "log in" in body["prompt"]
    assert "## Acceptance criteria" in body["prompt"]
    assert "Login works on Safari" in body["prompt"]

    async with session_factory() as db:
        link = await db.get(WorkItemSession, body["link_id"])
        assert link is not None
        assert link.kind == "spawned"
        assert link.session_id is None
        assert link.work_item_id == login_item["id"]


async def test_start_work_item_omits_acceptance_heading_when_absent(client: AsyncClient) -> None:
    _use_devops(dict(_ITEMS))
    source = await _new_source(client)
    await client.post(f"/api/v1/work/sources/{source['id']}/sync")
    items = (await client.get("/api/v1/work/items")).json()
    dark_mode_item = next(i for i in items if i["external_id"] == 102)

    r = await client.post(f"/api/v1/work/items/{dark_mode_item['id']}/start")
    assert r.status_code == 200
    assert "## Acceptance criteria" not in r.json()["prompt"]


async def test_start_unknown_item_404(client: AsyncClient) -> None:
    r = await client.post("/api/v1/work/items/999999/start")
    assert r.status_code == 404


async def test_work_item_sessions_count_matches_starts(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    _use_devops(dict(_ITEMS))
    source = await _new_source(client)
    await client.post(f"/api/v1/work/sources/{source['id']}/sync")
    items = (await client.get("/api/v1/work/items")).json()

    await client.post(f"/api/v1/work/items/{items[0]['id']}/start")
    await client.post(f"/api/v1/work/items/{items[1]['id']}/start")

    async with session_factory() as db:
        count = (
            await db.execute(select(func.count()).select_from(WorkItemSession))
        ).scalar()
        assert count == 2
