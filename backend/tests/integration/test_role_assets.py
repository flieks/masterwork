"""Factory role prompts over HTTP: listed, searchable, readable — and writable."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import quote

import pytest_asyncio
from httpx import AsyncClient

from app.api.deps import get_claude_runner, get_providers
from app.main import app
from tests.helpers import FakeRunner, providers_for

PLAN_SYSTEM = "masterwork:agent:plan:system"
PLAN_USER = "masterwork:agent:plan:user"


@pytest_asyncio.fixture
async def roles_client(
    client: AsyncClient, claude_tree: tuple[Path, Path], role_tree: Path
) -> AsyncClient:
    app.dependency_overrides[get_providers] = lambda: providers_for(
        claude_tree, roles_root=role_tree
    )
    return client


def _url(asset_id: str) -> str:
    return f"/api/v1/assets/{quote(asset_id, safe='')}"


async def test_list_includes_roles_beside_claude_assets(roles_client: AsyncClient) -> None:
    body = (await roles_client.get("/api/v1/assets")).json()
    by_id = {a["id"]: a for a in body}
    assert PLAN_SYSTEM in by_id
    assert "masterwork:agent:build:user" in by_id
    assert "claude:skill:frontend-dev" in by_id  # other providers still there
    assert by_id[PLAN_SYSTEM]["provider"] == "masterwork"
    assert by_id[PLAN_SYSTEM]["read_only"] is False
    assert by_id[PLAN_SYSTEM]["model"] == "opus"
    assert by_id[PLAN_SYSTEM]["title"] == "plan · system prompt"


async def test_roles_filter_as_agents(roles_client: AsyncClient) -> None:
    body = (await roles_client.get("/api/v1/assets", params={"kind": "agent"})).json()
    ids = {a["id"] for a in body}
    assert PLAN_SYSTEM in ids and "claude:agent:architect" in ids
    skills = (await roles_client.get("/api/v1/assets", params={"kind": "skill"})).json()
    assert not any(a["provider"] == "masterwork" for a in skills)


async def test_search_matches_role_purpose_and_content(roles_client: AsyncClient) -> None:
    # `purpose` from role.json reaches search through the description.
    by_purpose = (await roles_client.get("/api/v1/assets", params={"q": "implementation"})).json()
    assert {a["id"] for a in by_purpose} == {PLAN_SYSTEM, PLAN_USER}
    # ...and the prompt body itself is searchable like any other asset.
    by_body = (await roles_client.get("/api/v1/assets", params={"q": "{{request}}"})).json()
    assert [a["id"] for a in by_body] == [PLAN_USER]


async def test_get_role_asset(roles_client: AsyncClient) -> None:
    r = await roles_client.get(_url(PLAN_SYSTEM))
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "agent"
    assert body["name"] == "plan:system"
    assert "PLAN stage" in body["content"]


async def test_role_json_is_not_an_asset(roles_client: AsyncClient) -> None:
    assert (await roles_client.get(_url("masterwork:agent:plan:config"))).status_code == 404
    assert (await roles_client.get(_url("masterwork:agent:plan:role.json"))).status_code == 404


async def test_update_role_prompt_writes_the_file(
    roles_client: AsyncClient, role_tree: Path
) -> None:
    new = "You are the PLAN stage. Be concrete about paths.\n"
    r = await roles_client.put(_url(PLAN_SYSTEM), json={"content": new})
    assert r.status_code == 200
    assert r.json()["content"] == new
    assert (role_tree / "plan" / "system.md").read_text(encoding="utf-8") == new
    # role.json untouched, so the derived model/description survive an edit.
    assert r.json()["model"] == "opus"


async def test_update_unknown_role_404(roles_client: AsyncClient) -> None:
    r = await roles_client.put(_url("masterwork:agent:nope:system"), json={"content": "x"})
    assert r.status_code == 404


async def test_proposal_can_write_a_role_prompt(
    client: AsyncClient, claude_tree: tuple[Path, Path], role_tree: Path
) -> None:
    """The point of the feature: chat proposals improve the factory's own prompts."""
    target = role_tree / "plan" / "user.md"
    reply = (
        "ok\n\n```proposal\n"
        + json.dumps(
            {
                "summary": "sharpen the plan turn",
                "changes": [
                    {
                        "path": str(target),
                        "action": "update",
                        "new_content": "Request: {{request}}\nList the risks too.\n",
                        "description": "d",
                    }
                ],
            }
        )
        + "\n```"
    )
    app.dependency_overrides[get_claude_runner] = lambda: FakeRunner(reply=reply, session_id="cli")
    app.dependency_overrides[get_providers] = lambda: providers_for(
        claude_tree, roles_root=role_tree
    )
    sid = (await client.post("/api/v1/chat/sessions", json={})).json()["id"]
    r = await client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "go"})
    proposal = r.json()["assistant_message"]["proposal"]
    assert proposal["changes"][0]["asset_id"] == PLAN_USER  # path mapped back to the asset

    r = await client.post(f"/api/v1/proposals/{proposal['id']}/accept")
    assert r.json()["status"] == "applied"
    assert "List the risks too." in target.read_text(encoding="utf-8")


async def test_proposal_outside_the_store_still_fails(
    client: AsyncClient, claude_tree: tuple[Path, Path], role_tree: Path
) -> None:
    outside = role_tree.parent / "escaped.md"
    reply = (
        "ok\n\n```proposal\n"
        + json.dumps(
            {
                "summary": "s",
                "changes": [
                    {
                        "path": str(outside),
                        "action": "create",
                        "new_content": "nope",
                        "description": "d",
                    }
                ],
            }
        )
        + "\n```"
    )
    app.dependency_overrides[get_claude_runner] = lambda: FakeRunner(reply=reply, session_id="cli")
    app.dependency_overrides[get_providers] = lambda: providers_for(
        claude_tree, roles_root=role_tree
    )
    sid = (await client.post("/api/v1/chat/sessions", json={})).json()["id"]
    r = await client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "go"})
    proposal_id = r.json()["assistant_message"]["proposal"]["id"]

    r = await client.post(f"/api/v1/proposals/{proposal_id}/accept")
    assert r.json()["status"] == "failed"
    assert "outside allowed roots" in r.json()["error"]
    assert not outside.exists()


async def test_absent_store_is_invisible_not_an_error(
    client: AsyncClient, claude_tree: tuple[Path, Path], tmp_path: Path
) -> None:
    app.dependency_overrides[get_providers] = lambda: providers_for(
        claude_tree, roles_root=tmp_path / "never-seeded"
    )
    r = await client.get("/api/v1/assets")
    assert r.status_code == 200
    assert not any(a["provider"] == "masterwork" for a in r.json())
