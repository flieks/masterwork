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
        "System.Parent": 102,
        "System.Title": "Fix login bug",
        "System.Description": "<p>Users cannot <b>log in</b> on Safari.</p>",
        "Microsoft.VSTS.Common.AcceptanceCriteria": "<ul><li>Login works on Safari</li></ul>",
        "System.State": "Active",
        "System.IterationPath": "widgets\\Sprint 1",
        "System.WorkItemType": "Bug",
        "Microsoft.VSTS.Common.Priority": 2,
        "System.Tags": "auth; urgent",
        "System.ChangedDate": "2026-08-01T10:00:00Z",
        "System.AssignedTo": {"displayName": "Felix De Lille", "uniqueName": "felix@example.com"},
    },
    # Outside the current sprint — only reachable as 101's parent.
    102: {
        "System.Title": "Add dark mode",
        "System.Description": "<p>Add a dark theme toggle.</p>",
        "Microsoft.VSTS.Common.AcceptanceCriteria": None,
        "System.State": "New",
        "System.IterationPath": "widgets\\Sprint 2",
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
        "System.IterationPath": "widgets\\Sprint 1",
        "System.WorkItemType": "Task",
        "Microsoft.VSTS.Common.Priority": 1,
        "System.Tags": "auth",
        "System.ChangedDate": "2026-08-01T09:00:00Z",
        "System.Parent": 104,
        "System.AssignedTo": {"displayName": "Felix De Lille", "uniqueName": "felix@example.com"},
    },
    # Story in the current sprint: the widened WIQL fetches it directly now,
    # so it no longer needs the parent-pull pass to show up.
    104: {
        "System.Title": "Auth overhaul",
        "System.Description": "<p>Epic-level auth cleanup.</p>",
        "Microsoft.VSTS.Common.AcceptanceCriteria": None,
        "System.State": "Active",
        "System.IterationPath": "widgets\\Sprint 1",
        "System.WorkItemType": "User Story",
        "Microsoft.VSTS.Common.Priority": 2,
        "System.Tags": None,
        "System.ChangedDate": "2026-08-01T08:00:00Z",
    },
    # In the sprint but assigned to someone else — only the sprint-wide query
    # reaches this; the assigned-to-me default never would.
    105: {
        "System.Title": "Improve ingest reliability",
        "System.Description": "<p>Stabilize the ingest pipeline.</p>",
        "Microsoft.VSTS.Common.AcceptanceCriteria": None,
        "System.State": "Active",
        "System.IterationPath": "widgets\\Sprint 1",
        "System.WorkItemType": "Task",
        "Microsoft.VSTS.Common.Priority": 2,
        "System.Tags": None,
        "System.ChangedDate": "2026-08-03T10:00:00Z",
        "System.AssignedTo": {"displayName": "Sam Owner", "uniqueName": "sam@example.com"},
    },
}

# What the fallback (assigned-to-me) WIQL returns — only Felix's items.
_ASSIGNED_TO_ME_IDS = (101, 103)

_CURRENT_ITERATION = "widgets\\Sprint 1"
_OWNER_NAME = "Felix De Lille"


@pytest.fixture(autouse=True)
def _pat(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_DEVOPS_PAT", "fake-pat")


def _devops_handler(
    items: dict[int, dict[str, object]],
    *,
    queries: list[str],
    iteration_path: str | None = _CURRENT_ITERATION,
    iteration_status: int | None = None,
    owner_name: str | None = _OWNER_NAME,
    connection_status: int | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/_apis/wit/wiql"):
            query = json.loads(request.content)["query"]
            queries.append(query)
            if "IterationPath" in query:
                prefix = iteration_path or ""
                ids = [
                    i
                    for i, fields in items.items()
                    if str(fields.get("System.IterationPath", "")).startswith(prefix)
                ]
            else:
                ids = [i for i in _ASSIGNED_TO_ME_IDS if i in items]
            return httpx.Response(200, json={"workItems": [{"id": i} for i in ids]})
        if request.url.path.endswith("/_apis/wit/workitemsbatch"):
            body = json.loads(request.content)
            value = [
                {"id": i, "url": f"https://dev.azure.com/_apis/wit/workItems/{i}", "fields": fields}
                for i, fields in items.items()
                if i in body["ids"]
            ]
            return httpx.Response(200, json={"value": value})
        if request.url.path.endswith("/_apis/work/teamsettings/iterations"):
            if iteration_status is not None:
                return httpx.Response(iteration_status, text="teamsettings unavailable")
            values = [{"path": iteration_path}] if iteration_path else []
            return httpx.Response(200, json={"value": values})
        if request.url.path.endswith("/_apis/connectionData"):
            if connection_status is not None:
                return httpx.Response(connection_status, text="connectionData unavailable")
            user = {"providerDisplayName": owner_name} if owner_name else {}
            return httpx.Response(200, json={"authenticatedUser": user})
        raise AssertionError(f"unexpected request: {request.url}")

    return handler


def _use_devops(
    items: dict[int, dict[str, object]],
    *,
    iteration_path: str | None = _CURRENT_ITERATION,
    iteration_status: int | None = None,
    owner_name: str | None = _OWNER_NAME,
    connection_status: int | None = None,
) -> list[str]:
    """Wires the fake DevOps client and returns the list every WIQL sent will
    be appended to, so a test can assert on what was actually queried."""
    queries: list[str] = []
    handler = _devops_handler(
        items,
        queries=queries,
        iteration_path=iteration_path,
        iteration_status=iteration_status,
        owner_name=owner_name,
        connection_status=connection_status,
    )

    def factory(source: object) -> AzureDevOpsClient:
        return AzureDevOpsClient(
            org_url=source.org_url,  # type: ignore[attr-defined]
            project=source.project,  # type: ignore[attr-defined]
            secret_ref=source.secret_ref,  # type: ignore[attr-defined]
            transport=httpx.MockTransport(handler),
        )

    app.dependency_overrides[get_devops_client_factory] = lambda: factory
    return queries


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
    assert body["owner_display_name"] is None


async def test_list_sources(client: AsyncClient) -> None:
    await _new_source(client, "widgets")
    r = await client.get("/api/v1/work/sources")
    assert r.status_code == 200
    assert len(r.json()) == 1


async def test_sync_unknown_source_404(client: AsyncClient) -> None:
    r = await client.post("/api/v1/work/sources/00000000-0000-0000-0000-000000000000/sync")
    assert r.status_code == 404


# --- sync: sprint-wide WIQL ---------------------------------------------------


async def test_sync_inserts_and_converts_html_to_markdown(client: AsyncClient) -> None:
    _use_devops(dict(_ITEMS))
    source = await _new_source(client)

    r = await client.post(f"/api/v1/work/sources/{source['id']}/sync")
    assert r.status_code == 200
    # 101, 103, 104, 105 come straight off the sprint WIQL; 102 is 101's
    # parent, outside the sprint, so it is pulled in the second pass.
    assert r.json() == {"fetched": 5, "inserted": 5, "updated": 0}

    items = (await client.get("/api/v1/work/items")).json()
    assert len(items) == 5
    fixed_login = next(i for i in items if i["external_id"] == 101)
    assert "**log in**" in fixed_login["description_md"]
    assert "Login works on Safari" in fixed_login["acceptance_md"]
    assert fixed_login["tags"] == ["auth", "urgent"]
    assert fixed_login["assigned_to"] == "Felix De Lille"
    assert fixed_login["parent_external_id"] == 102
    assert fixed_login["pulled_as_parent"] is False

    dark_mode = next(i for i in items if i["external_id"] == 102)
    assert dark_mode["acceptance_md"] is None
    assert dark_mode["parent_external_id"] is None
    assert dark_mode["assigned_to"] is None
    assert dark_mode["pulled_as_parent"] is True  # only reachable as 101's parent

    # 104 is in-sprint now, so the WIQL itself returns it — no longer a
    # parent-pull row.
    auth_story = next(i for i in items if i["external_id"] == 104)
    assert auth_story["pulled_as_parent"] is False
    assert auth_story["item_type"] == "User Story"

    other_persons_item = next(i for i in items if i["external_id"] == 105)
    assert other_persons_item["assigned_to"] == "Sam Owner"
    assert other_persons_item["pulled_as_parent"] is False


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
    assert body == {"fetched": 5, "inserted": 0, "updated": 5}

    all_items = (await client.get("/api/v1/work/items")).json()
    assert len(all_items) == 5  # row count stays 5, nothing duplicated
    updated = next(i for i in all_items if i["external_id"] == 102)
    assert updated["title"] == "Add dark mode toggle"
    assert updated["state"] == "Active"

    sources = (await client.get("/api/v1/work/sources")).json()
    assert sources[0]["last_sync_at"] is not None
    assert sources[0]["current_iteration"] == _CURRENT_ITERATION


async def test_sync_sends_the_sprint_scoped_wiql_not_assigned_to_me(client: AsyncClient) -> None:
    queries = _use_devops(dict(_ITEMS))
    source = await _new_source(client)
    await client.post(f"/api/v1/work/sources/{source['id']}/sync")

    assert len(queries) == 1
    assert f"[System.IterationPath] UNDER '{_CURRENT_ITERATION}'" in queries[0]
    assert "NOT IN ('Closed','Removed','Done')" in queries[0]
    assert "@Me" not in queries[0]


async def test_sync_falls_back_to_assigned_to_me_when_iteration_lookup_fails(
    client: AsyncClient,
) -> None:
    queries = _use_devops(dict(_ITEMS), iteration_status=500)
    source = await _new_source(client)
    r = await client.post(f"/api/v1/work/sources/{source['id']}/sync")
    assert r.status_code == 200

    assert len(queries) == 1
    assert queries[0] == (
        "SELECT [System.Id] FROM WorkItems WHERE [System.AssignedTo] = @Me "
        "AND [System.State] NOT IN ('Closed','Removed','Done') "
        "ORDER BY [System.ChangedDate] DESC"
    )
    # 101, 103 come off the WIQL; their parents 102, 104 are pulled as context.
    items = (await client.get("/api/v1/work/items")).json()
    assert {i["external_id"] for i in items} == {101, 102, 103, 104}


async def test_sync_falls_back_to_assigned_to_me_when_no_sprint_covers_today(
    client: AsyncClient,
) -> None:
    queries = _use_devops(dict(_ITEMS), iteration_path=None)
    source = await _new_source(client)
    await client.post(f"/api/v1/work/sources/{source['id']}/sync")

    assert queries[0] == (
        "SELECT [System.Id] FROM WorkItems WHERE [System.AssignedTo] = @Me "
        "AND [System.State] NOT IN ('Closed','Removed','Done') "
        "ORDER BY [System.ChangedDate] DESC"
    )
    sources = (await client.get("/api/v1/work/sources")).json()
    assert sources[0]["current_iteration"] is None


async def test_sync_escapes_a_single_quote_in_the_iteration_path(client: AsyncClient) -> None:
    tricky_path = "widgets\\O'Brien Sprint"
    queries = _use_devops(dict(_ITEMS), iteration_path=tricky_path)
    source = await _new_source(client)
    r = await client.post(f"/api/v1/work/sources/{source['id']}/sync")
    assert r.status_code == 200

    assert "UNDER 'widgets\\O''Brien Sprint'" in queries[0]
    assert "AND [System.State] NOT IN ('Closed','Removed','Done')" in queries[0]


async def test_sync_operator_wiql_still_wins_over_the_sprint_query(client: AsyncClient) -> None:
    queries = _use_devops(dict(_ITEMS))
    r = await client.post(
        "/api/v1/work/sources",
        json={
            "org_url": "https://dev.azure.com/acme",
            "project": "widgets",
            "query_wiql": "SELECT [System.Id] FROM WorkItems WHERE [System.State] = 'Active'",
        },
    )
    assert r.status_code == 201
    source = r.json()

    await client.post(f"/api/v1/work/sources/{source['id']}/sync")
    assert queries == ["SELECT [System.Id] FROM WorkItems WHERE [System.State] = 'Active'"]


# --- sync: owner display name -------------------------------------------------


async def test_owner_display_name_is_persisted_from_connection_data(client: AsyncClient) -> None:
    _use_devops(dict(_ITEMS), owner_name="Felix De Lille")
    source = await _new_source(client)
    await client.post(f"/api/v1/work/sources/{source['id']}/sync")

    sources = (await client.get("/api/v1/work/sources")).json()
    assert sources[0]["owner_display_name"] == "Felix De Lille"


async def test_owner_display_name_survives_a_failing_connection_data_call(
    client: AsyncClient,
) -> None:
    _use_devops(dict(_ITEMS), owner_name="Felix De Lille")
    source = await _new_source(client)
    await client.post(f"/api/v1/work/sources/{source['id']}/sync")

    _use_devops(dict(_ITEMS), connection_status=500)
    r = await client.post(f"/api/v1/work/sources/{source['id']}/sync")
    assert r.status_code == 200  # the sync itself still succeeds

    sources = (await client.get("/api/v1/work/sources")).json()
    assert sources[0]["owner_display_name"] == "Felix De Lille"


# --- filters -------------------------------------------------------------


async def test_list_items_filters_by_state(client: AsyncClient) -> None:
    _use_devops(dict(_ITEMS))
    source = await _new_source(client)
    await client.post(f"/api/v1/work/sources/{source['id']}/sync")

    r = await client.get("/api/v1/work/items", params={"state": "Active"})
    assert r.status_code == 200
    ids = {i["external_id"] for i in r.json()}
    assert ids == {101, 103, 104, 105}


async def test_list_items_filters_by_source_id(client: AsyncClient) -> None:
    _use_devops(dict(_ITEMS))
    source_a = await _new_source(client, "widgets")
    await client.post(f"/api/v1/work/sources/{source_a['id']}/sync")
    source_b = await _new_source(client, "gizmos")  # never synced

    r = await client.get("/api/v1/work/items", params={"source_id": source_a["id"]})
    assert len(r.json()) == 5
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
