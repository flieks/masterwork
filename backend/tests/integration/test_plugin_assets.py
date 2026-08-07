"""Plugin assets over HTTP: listed, readable, linkable — but never writable."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest_asyncio
from httpx import AsyncClient

from app.api.deps import get_claude_runner, get_providers
from app.main import app
from tests.helpers import FakeRunner, providers_for

PLUGIN_SKILL_ID = "claude-plugin:skill:vercel:bootstrap"


@pytest_asyncio.fixture
async def plugin_client(
    client: AsyncClient, claude_tree: tuple[Path, Path], plugin_tree: Path
) -> AsyncClient:
    app.dependency_overrides[get_providers] = lambda: providers_for(claude_tree, plugin_tree)
    return client


def _url(asset_id: str) -> str:
    return f"/api/v1/assets/{quote(asset_id, safe='')}"


async def test_list_includes_plugin_assets(plugin_client: AsyncClient) -> None:
    body = (await plugin_client.get("/api/v1/assets")).json()
    by_id = {a["id"]: a for a in body}
    assert PLUGIN_SKILL_ID in by_id
    assert "claude-plugin:agent:vercel:deployment-expert" in by_id
    assert by_id[PLUGIN_SKILL_ID]["read_only"] is True
    assert by_id[PLUGIN_SKILL_ID]["provider"] == "claude-plugin"
    assert by_id["claude:skill:frontend-dev"]["read_only"] is False


async def test_search_matches_plugin_content(plugin_client: AsyncClient) -> None:
    body = (await plugin_client.get("/api/v1/assets", params={"q": "provision"})).json()
    assert [a["id"] for a in body] == [PLUGIN_SKILL_ID]


async def test_get_plugin_asset_with_colon_name(plugin_client: AsyncClient) -> None:
    r = await plugin_client.get(_url(PLUGIN_SKILL_ID))
    assert r.status_code == 200
    assert r.json()["name"] == "vercel:bootstrap"
    assert "# Bootstrap" in r.json()["content"]


async def test_put_plugin_asset_rejected_403(plugin_client: AsyncClient, plugin_tree: Path) -> None:
    r = await plugin_client.put(_url(PLUGIN_SKILL_ID), json={"content": "# hacked"})
    assert r.status_code == 403
    assert "read-only" in r.json()["detail"]
    skill_file = (
        plugin_tree / "cache" / "official" / "vercel" / "1.0.0" / "skills" / "bootstrap"
    ) / "SKILL.md"
    assert "# hacked" not in skill_file.read_text(encoding="utf-8")


async def _seed_proposal(
    client: AsyncClient, tree: tuple[Path, Path], plugins: Path, reply_payload: dict[str, Any]
) -> str:
    reply = f"ok\n\n```project\n{json.dumps(reply_payload)}\n```"
    app.dependency_overrides[get_claude_runner] = lambda: FakeRunner(reply=reply, session_id="cli")
    app.dependency_overrides[get_providers] = lambda: providers_for(tree, plugins)
    pid = (await client.post("/api/v1/projects", json={"name": "P"})).json()["id"]
    sid = (await client.post("/api/v1/chat/sessions", json={"project_id": pid})).json()["id"]
    r = await client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "go"})
    return str(r.json()["assistant_message"]["proposal"]["id"])


async def test_linking_plugin_asset_to_project_succeeds(
    client: AsyncClient, claude_tree: tuple[Path, Path], plugin_tree: Path
) -> None:
    payload = {"asset_ids": [PLUGIN_SKILL_ID, "claude:agent:architect"], "description": "link"}
    proposal_id = await _seed_proposal(client, claude_tree, plugin_tree, payload)
    r = await client.post(f"/api/v1/proposals/{proposal_id}/accept")
    assert r.status_code == 200
    assert r.json()["status"] == "applied"


async def test_proposal_file_change_into_plugin_dir_fails(
    client: AsyncClient, claude_tree: tuple[Path, Path], plugin_tree: Path
) -> None:
    target = (
        plugin_tree / "cache" / "official" / "vercel" / "1.0.0" / "skills" / "bootstrap"
    ) / "SKILL.md"
    reply = (
        "ok\n\n```proposal\n"
        + json.dumps(
            {
                "summary": "s",
                "changes": [
                    {
                        "path": str(target),
                        "action": "update",
                        "new_content": "# hacked",
                        "description": "d",
                    }
                ],
            }
        )
        + "\n```"
    )
    app.dependency_overrides[get_claude_runner] = lambda: FakeRunner(reply=reply, session_id="cli")
    app.dependency_overrides[get_providers] = lambda: providers_for(claude_tree, plugin_tree)
    sid = (await client.post("/api/v1/chat/sessions", json={})).json()["id"]
    r = await client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "go"})
    proposal_id = r.json()["assistant_message"]["proposal"]["id"]

    r = await client.post(f"/api/v1/proposals/{proposal_id}/accept")
    assert r.json()["status"] == "failed"
    assert "outside allowed roots" in r.json()["error"]
    assert "# hacked" not in target.read_text(encoding="utf-8")
