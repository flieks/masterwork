"""Project-scoped chat: session filtering, project_id, and dual-block parsing."""

from __future__ import annotations

import json
from pathlib import Path

from httpx import AsyncClient

from app.api.deps import get_claude_runner, get_providers
from app.main import app
from tests.helpers import FakeRunner, providers_for


def _use(runner: FakeRunner, tree: tuple[Path, Path]) -> None:
    app.dependency_overrides[get_claude_runner] = lambda: runner
    app.dependency_overrides[get_providers] = lambda: providers_for(tree)


async def _new_project(client: AsyncClient, name: str = "Deploy") -> str:
    r = await client.post("/api/v1/projects", json={"name": name})
    return r.json()["id"]


def _reply_both(skills_root: Path) -> str:
    proposal = {
        "summary": "tidy",
        "changes": [
            {
                "path": str(skills_root / "frontend-dev" / "SKILL.md"),
                "action": "update",
                "new_content": "x",
                "description": "d",
            }
        ],
    }
    project = {
        "name": "Renamed project",
        "goal": None,
        "flow_mermaid": "flowchart TD\n  A-->B",
        "asset_ids": ["claude:skill:frontend-dev"],
        "description": "link the frontend skill",
    }
    return (
        f"Here is my plan.\n\n```proposal\n{json.dumps(proposal)}\n```"
        f"\n\n```project\n{json.dumps(project)}\n```"
    )


def _reply_project_only() -> str:
    project = {
        "name": None,
        "goal": "Auto-deploy to Azure",
        "flow_mermaid": None,
        "asset_ids": None,
        "description": "rewrite the goal",
    }
    return f"Updated the goal.\n\n```project\n{json.dumps(project)}\n```"


# --- session creation + filtering ------------------------------------------


async def test_create_session_with_project(client: AsyncClient) -> None:
    pid = await _new_project(client)
    r = await client.post("/api/v1/chat/sessions", json={"project_id": pid})
    assert r.status_code == 201
    assert r.json()["project_id"] == pid


async def test_create_session_unknown_project_404(client: AsyncClient) -> None:
    r = await client.post(
        "/api/v1/chat/sessions",
        json={"project_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert r.status_code == 404


async def test_global_session_has_null_project(client: AsyncClient) -> None:
    r = await client.post("/api/v1/chat/sessions", json={})
    assert r.json()["project_id"] is None


async def test_list_sessions_filter(client: AsyncClient) -> None:
    pid = await _new_project(client)
    proj_session = (await client.post("/api/v1/chat/sessions", json={"project_id": pid})).json()[
        "id"
    ]
    global_session = (await client.post("/api/v1/chat/sessions", json={})).json()["id"]

    # omitted → all
    all_ids = {s["id"] for s in (await client.get("/api/v1/chat/sessions")).json()}
    assert all_ids == {proj_session, global_session}

    # "none" → global only
    none_ids = {
        s["id"]
        for s in (await client.get("/api/v1/chat/sessions", params={"project_id": "none"})).json()
    }
    assert none_ids == {global_session}

    # uuid → that project only
    proj_ids = {
        s["id"]
        for s in (await client.get("/api/v1/chat/sessions", params={"project_id": pid})).json()
    }
    assert proj_ids == {proj_session}


async def test_list_sessions_unknown_project_404(client: AsyncClient) -> None:
    r = await client.get(
        "/api/v1/chat/sessions",
        params={"project_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert r.status_code == 404


# --- project-scoped message exchange ---------------------------------------


async def test_project_message_merges_proposal_and_project_update(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    runner = FakeRunner(reply=_reply_both(claude_tree[0]), session_id="cli-p1")
    _use(runner, claude_tree)
    pid = await _new_project(client)
    sid = (await client.post("/api/v1/chat/sessions", json={"project_id": pid})).json()["id"]

    r = await client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "analyze"})
    assert r.status_code == 200
    assistant = r.json()["assistant_message"]

    assert "```proposal" not in assistant["content"]
    assert "```project" not in assistant["content"]
    assert assistant["content"] == "Here is my plan."

    proposal = assistant["proposal"]
    assert proposal is not None
    assert proposal["changes"][0]["asset_id"] == "claude:skill:frontend-dev"
    pu = proposal["project_update"]
    assert pu is not None
    assert pu["project_id"] == pid  # filled server-side
    assert pu["name"] == "Renamed project"
    assert pu["asset_ids"] == ["claude:skill:frontend-dev"]

    # First project-scoped message carries the extended system prompt + state line.
    assert runner.calls[0]["system_prompt"] is not None
    assert "This chat is scoped to a PROJECT" in runner.calls[0]["system_prompt"]
    assert runner.calls[0]["prompt"].startswith("[current project state:")


async def test_project_only_reply_creates_project_update_proposal(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    _use(FakeRunner(reply=_reply_project_only(), session_id="cli-p2"), claude_tree)
    pid = await _new_project(client)
    sid = (await client.post("/api/v1/chat/sessions", json={"project_id": pid})).json()["id"]

    r = await client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "set goal"})
    proposal = r.json()["assistant_message"]["proposal"]
    assert proposal is not None
    assert proposal["changes"] == []
    assert proposal["project_update"]["goal"] == "Auto-deploy to Azure"


async def test_global_session_ignores_project_block(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    _use(FakeRunner(reply=_reply_project_only(), session_id="cli-p3"), claude_tree)
    sid = (await client.post("/api/v1/chat/sessions", json={})).json()["id"]

    r = await client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "hi"})
    assistant = r.json()["assistant_message"]
    assert assistant["proposal"] is None  # project block ignored, no file changes
    assert "```project" not in assistant["content"]
    assert assistant["content"] == "Updated the goal."


async def test_project_second_message_resumes_with_state_line(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    runner = FakeRunner(reply="ok", session_id="cli-p4")
    _use(runner, claude_tree)
    pid = await _new_project(client)
    sid = (await client.post("/api/v1/chat/sessions", json={"project_id": pid})).json()["id"]

    await client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "first"})
    await client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "second"})

    # Later message resumes (no system prompt) but still prepends the state line.
    assert runner.calls[1]["resume"] == "cli-p4"
    assert runner.calls[1]["system_prompt"] is None
    assert runner.calls[1]["prompt"].startswith("[current project state:")
