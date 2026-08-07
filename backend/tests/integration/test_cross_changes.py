"""GET /projects/{id}/cross-changes: shared-asset edits by other projects."""

from __future__ import annotations

import json
from pathlib import Path

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.deps import get_providers, get_session_factory, get_simulation_runner
from app.main import app
from tests.helpers import FakeRunner, providers_for

_SHARED = "claude:skill:frontend-dev"


def _sim_reply(changes: list[dict], *, title: str = "Improve the skill") -> str:
    payload = {
        "score": 70,
        "verdict": "ok",
        "summary": "ran",
        "analysis": "none",
        "trace_mermaid": None,
        "suggestions": [{"title": title, "impact": "high", "rationale": "why", "changes": changes}],
    }
    return f"```simulation\n{json.dumps(payload)}\n```"


def _use(tree: tuple[Path, Path], session_factory: async_sessionmaker, reply: str) -> FakeRunner:
    runner = FakeRunner(reply=reply)
    app.dependency_overrides[get_providers] = lambda: providers_for(tree)
    app.dependency_overrides[get_simulation_runner] = lambda: runner
    app.dependency_overrides[get_session_factory] = lambda: session_factory
    return runner


async def _project(client: AsyncClient, name: str, assets: list[str]) -> str:
    r = await client.post("/api/v1/projects", json={"name": name, "goal": f"Goal of {name}"})
    project_id = r.json()["id"]
    await client.patch(f"/api/v1/projects/{project_id}", json={"asset_ids": assets})
    return project_id


async def _run_simulation(client: AsyncClient, project_id: str) -> str:
    r = await client.post(f"/api/v1/projects/{project_id}/simulations", json={"scenario": "s"})
    assert r.status_code == 202
    return r.json()["id"]


async def test_flags_shared_asset_edits_since_last_run(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    skill_path = str(claude_tree[0] / "frontend-dev" / "SKILL.md")
    change = {
        "path": skill_path,
        "action": "update",
        "new_content": "rewritten",
        "description": "d",
    }
    _use(claude_tree, session_factory, _sim_reply([change], title="Mobile rewrite"))

    web = await _project(client, "Web tool", [_SHARED])
    mobile = await _project(client, "Mobile tool", [_SHARED])

    # Web scores first — this run is its baseline.
    await _run_simulation(client, web)
    r = await client.get(f"/api/v1/projects/{web}/cross-changes")
    assert r.status_code == 200
    assert r.json()["changes"] == []  # nothing external happened yet

    # Mobile runs and APPLIES an edit to the shared skill.
    sim_id = await _run_simulation(client, mobile)
    r = await client.post(f"/api/v1/simulations/{sim_id}/suggestions/0/apply")
    assert r.json()["suggestions"][0]["status"] == "applied"

    # Web now sees the cross-project change; mobile does not (it made it).
    r = await client.get(f"/api/v1/projects/{web}/cross-changes")
    body = r.json()
    assert body["since"] is not None
    assert len(body["changes"]) == 1
    item = body["changes"][0]
    assert item["asset_id"] == _SHARED
    assert item["action"] == "update"
    assert item["source"] == "simulation"
    assert item["project_name"] == "Mobile tool"
    assert item["title"] == "Mobile rewrite"

    r = await client.get(f"/api/v1/projects/{mobile}/cross-changes")
    assert r.json()["changes"] == []

    # Web re-runs — the alert clears (baseline moves past the edit).
    await _run_simulation(client, web)
    r = await client.get(f"/api/v1/projects/{web}/cross-changes")
    assert r.json()["changes"] == []


async def test_no_completed_run_means_no_alerts(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    _use(claude_tree, session_factory, _sim_reply([]))
    project_id = await _project(client, "Fresh", [_SHARED])
    r = await client.get(f"/api/v1/projects/{project_id}/cross-changes")
    assert r.status_code == 200
    assert r.json() == {"since": None, "changes": []}


async def test_unlinked_assets_and_link_actions_are_ignored(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    backend_path = str(claude_tree[0] / "backend-dev" / "SKILL.md")
    changes = [
        # Touches an asset the watching project does NOT link.
        {"path": backend_path, "action": "update", "new_content": "x", "description": "d"},
        # Link action on the shared asset — no file modification.
        {
            "path": str(claude_tree[0] / "frontend-dev" / "SKILL.md"),
            "action": "link",
            "new_content": None,
            "description": "d",
        },
    ]
    _use(claude_tree, session_factory, _sim_reply(changes))

    web = await _project(client, "Web tool", [_SHARED])
    other = await _project(client, "Other", ["claude:skill:backend-dev"])

    await _run_simulation(client, web)
    sim_id = await _run_simulation(client, other)
    r = await client.post(f"/api/v1/simulations/{sim_id}/suggestions/0/apply")
    assert r.json()["suggestions"][0]["status"] == "applied"

    r = await client.get(f"/api/v1/projects/{web}/cross-changes")
    assert r.json()["changes"] == []
