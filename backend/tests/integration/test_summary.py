"""Change-summary endpoint with a fake runner."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.deps import (
    get_light_runner,
    get_providers,
    get_session_factory,
    get_simulation_runner,
)
from app.db.models.chat import ChatMessage, ChatSession, Proposal
from app.main import app
from tests.helpers import FakeRunner, providers_for


def _summary_reply(markdown: str) -> str:
    return f"Here you go.\n\n```summary\n{markdown}\n```"


def _sim_reply(path: str) -> str:
    payload = {
        "score": 80,
        "verdict": "v",
        "summary": "s",
        "analysis": "a",
        "trace_mermaid": 'flowchart TD\n  A["x"]',
        "suggestions": [
            {
                "title": "Tighten the trigger",
                "impact": "high",
                "rationale": "r",
                "changes": [
                    {
                        "path": path,
                        "action": "update",
                        "new_content": "new body",
                        "description": "rewrote the trigger section",
                    }
                ],
            }
        ],
    }
    return f"```simulation\n{json.dumps(payload)}\n```"


async def _create_project(client: AsyncClient) -> str:
    r = await client.post("/api/v1/projects", json={"name": "Sum project", "goal": "Ship"})
    assert r.status_code == 201
    project_id = r.json()["id"]
    # Simulations refuse projects with no linked assets.
    r = await client.patch(
        f"/api/v1/projects/{project_id}", json={"asset_ids": ["claude:skill:frontend-dev"]}
    )
    assert r.status_code == 200
    return project_id


async def test_summary_without_changes_short_circuits(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    runner = FakeRunner(reply=_summary_reply("should not be called"))
    app.dependency_overrides[get_light_runner] = lambda: runner
    project_id = await _create_project(client)

    r = await client.post(f"/api/v1/projects/{project_id}/summary")
    assert r.status_code == 200
    assert "No applied changes yet" in r.json()["summary"]
    assert runner.calls == []  # no claude round trip for an empty change log

    r = await client.get(f"/api/v1/projects/{project_id}")
    assert "No applied changes yet" in r.json()["change_summary"]
    assert r.json()["change_summary_at"] is not None


async def test_summary_covers_simulation_suggestions_and_chat_proposals(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    skill_path = str(claude_tree[0] / "frontend-dev" / "SKILL.md")
    sim_runner = FakeRunner(reply=_sim_reply(skill_path))
    summary_runner = FakeRunner(reply=_summary_reply("## Overview\nToolkit evolved."))
    app.dependency_overrides[get_providers] = lambda: providers_for(claude_tree)
    app.dependency_overrides[get_simulation_runner] = lambda: sim_runner
    app.dependency_overrides[get_session_factory] = lambda: session_factory
    app.dependency_overrides[get_light_runner] = lambda: summary_runner
    project_id = await _create_project(client)

    # An applied simulation suggestion...
    r = await client.post(f"/api/v1/projects/{project_id}/simulations", json={"scenario": "x"})
    assert r.status_code == 202
    simulation_id = r.json()["id"]
    r = await client.post(f"/api/v1/simulations/{simulation_id}/suggestions/0/apply")
    assert r.status_code == 200
    assert r.json()["suggestions"][0]["status"] == "applied"

    # ...and an applied chat proposal, inserted directly (the chat flow is
    # covered by its own tests; here only the summary join matters).
    async with session_factory() as db:
        session = ChatSession(project_id=uuid.UUID(project_id), title="t")
        db.add(session)
        await db.flush()
        message = ChatMessage(session_id=session.id, role="assistant", content="m")
        db.add(message)
        await db.flush()
        db.add(
            Proposal(
                message_id=message.id,
                status="applied",
                summary="Add deploy checklist",
                changes=[
                    {
                        "path": str(claude_tree[1] / "architect.md"),
                        "action": "update",
                        "new_content": "x",
                        "description": "added a deploy checklist",
                        "asset_id": "claude:agent:architect",
                    }
                ],
                applied_at=datetime.now(tz=UTC),
            )
        )
        await db.commit()

    r = await client.post(f"/api/v1/projects/{project_id}/summary")
    assert r.status_code == 200
    assert r.json()["summary"] == "## Overview\nToolkit evolved."

    # The prompt carried both sources, grouped per asset.
    prompt = summary_runner.calls[0]["prompt"]
    assert "claude:skill:frontend-dev" in prompt
    assert "rewrote the trigger section" in prompt
    assert "claude:agent:architect" in prompt
    assert "Add deploy checklist" in prompt

    r = await client.get(f"/api/v1/projects/{project_id}")
    assert r.json()["change_summary"] == "## Overview\nToolkit evolved."
