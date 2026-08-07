"""Accepting proposals that carry a project update (v1.1)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from httpx import AsyncClient

from app.api.deps import get_claude_runner, get_providers
from app.main import app
from tests.helpers import FakeRunner, providers_for


def _reply(project: dict[str, Any], changes: list[dict[str, Any]] | None = None) -> str:
    parts = ["ok"]
    if changes is not None:
        parts.append(f"```proposal\n{json.dumps({'summary': 's', 'changes': changes})}\n```")
    parts.append(f"```project\n{json.dumps(project)}\n```")
    return "\n\n".join(parts)


async def _seed(client: AsyncClient, tree: tuple[Path, Path], reply: str) -> tuple[str, str]:
    """Create a project + scoped session, run one message, return (project_id, proposal_id)."""
    app.dependency_overrides[get_claude_runner] = lambda: FakeRunner(reply=reply, session_id="cli")
    app.dependency_overrides[get_providers] = lambda: providers_for(tree)
    pid = (await client.post("/api/v1/projects", json={"name": "P"})).json()["id"]
    sid = (await client.post("/api/v1/chat/sessions", json={"project_id": pid})).json()["id"]
    r = await client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "go"})
    return pid, r.json()["assistant_message"]["proposal"]["id"]


async def test_accept_applies_project_fields(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    project = {
        "name": "Renamed",
        "goal": "New goal",
        "flow_mermaid": "flowchart TD\n  A-->B",
        "asset_ids": ["claude:skill:frontend-dev", "claude:agent:architect"],
        "description": "wire it up",
    }
    pid, proposal_id = await _seed(client, claude_tree, _reply(project))

    r = await client.post(f"/api/v1/proposals/{proposal_id}/accept")
    assert r.status_code == 200
    assert r.json()["status"] == "applied"

    body = (await client.get(f"/api/v1/projects/{pid}")).json()
    assert body["name"] == "Renamed"
    assert body["goal"] == "New goal"
    assert body["flow_mermaid"] == "flowchart TD\n  A-->B"
    assert body["asset_ids"] == ["claude:skill:frontend-dev", "claude:agent:architect"]


async def test_accept_asset_ids_is_full_replace(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    project = {"asset_ids": ["claude:skill:frontend-dev"], "description": "replace"}
    pid, proposal_id = await _seed(client, claude_tree, _reply(project))
    # Give the project a different existing link first; the proposal must REPLACE it.
    await client.patch(f"/api/v1/projects/{pid}", json={"asset_ids": ["claude:skill:backend-dev"]})

    r = await client.post(f"/api/v1/proposals/{proposal_id}/accept")
    assert r.json()["status"] == "applied"
    body = (await client.get(f"/api/v1/projects/{pid}")).json()
    assert body["asset_ids"] == ["claude:skill:frontend-dev"]  # backend-dev dropped


async def test_accept_create_file_then_link_it(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    skills_root = claude_tree[0]
    new_file = skills_root / "brand-new" / "SKILL.md"
    changes = [
        {
            "path": str(new_file),
            "action": "create",
            "new_content": "---\nname: brand-new\n---\nbody",
            "description": "new skill",
        }
    ]
    project = {"asset_ids": ["claude:skill:brand-new"], "description": "link the new skill"}
    pid, proposal_id = await _seed(client, claude_tree, _reply(project, changes))

    r = await client.post(f"/api/v1/proposals/{proposal_id}/accept")
    assert r.json()["status"] == "applied"
    assert new_file.exists()  # created before asset validation ran
    body = (await client.get(f"/api/v1/projects/{pid}")).json()
    assert body["asset_ids"] == ["claude:skill:brand-new"]


async def test_accept_unknown_asset_id_fails_with_files_applied_note(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    skills_root = claude_tree[0]
    new_file = skills_root / "made-here" / "SKILL.md"
    changes = [
        {
            "path": str(new_file),
            "action": "create",
            "new_content": "---\nname: made-here\n---\nbody",
            "description": "new skill",
        }
    ]
    project = {"asset_ids": ["claude:skill:does-not-exist"], "description": "bad link"}
    pid, proposal_id = await _seed(client, claude_tree, _reply(project, changes))

    r = await client.post(f"/api/v1/proposals/{proposal_id}/accept")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "failed"
    assert "file changes were applied" in body["error"]
    assert "claude:skill:does-not-exist" in body["error"]

    # File change stays applied; the project update did NOT persist.
    assert new_file.exists()
    project_body = (await client.get(f"/api/v1/projects/{pid}")).json()
    assert project_body["asset_ids"] == []
