"""Trigger-guide endpoint with a fake runner."""

from __future__ import annotations

from pathlib import Path

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.deps import get_providers, get_simulation_runner
from app.main import app
from tests.helpers import FakeRunner, providers_for


def _trigger_reply(markdown: str) -> str:
    return f"Read the files.\n\n```trigger\n{markdown}\n```"


async def _create_project(client: AsyncClient, *, asset_ids: list[str] | None = None) -> str:
    r = await client.post("/api/v1/projects", json={"name": "Trig project", "goal": "Ship"})
    assert r.status_code == 201
    project_id = r.json()["id"]
    if asset_ids:
        r = await client.patch(f"/api/v1/projects/{project_id}", json={"asset_ids": asset_ids})
        assert r.status_code == 200
    return project_id


async def test_trigger_without_assets_short_circuits(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    runner = FakeRunner(reply=_trigger_reply("should not be called"))
    app.dependency_overrides[get_providers] = lambda: providers_for(claude_tree)
    app.dependency_overrides[get_simulation_runner] = lambda: runner
    project_id = await _create_project(client)

    r = await client.post(f"/api/v1/projects/{project_id}/trigger")
    assert r.status_code == 200
    assert "No assets linked yet" in r.json()["trigger_guide"]
    assert runner.calls == []


async def test_trigger_generates_and_persists(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    guide = "## Entry point\nUse `frontend-dev` first."
    runner = FakeRunner(reply=_trigger_reply(guide))
    app.dependency_overrides[get_providers] = lambda: providers_for(claude_tree)
    app.dependency_overrides[get_simulation_runner] = lambda: runner
    project_id = await _create_project(
        client, asset_ids=["claude:skill:frontend-dev", "claude:agent:architect"]
    )

    r = await client.post(f"/api/v1/projects/{project_id}/trigger")
    assert r.status_code == 200
    assert r.json()["trigger_guide"] == guide

    # The prompt named both assets with their file paths.
    prompt = runner.calls[0]["prompt"]
    assert "claude:skill:frontend-dev" in prompt
    assert "claude:agent:architect" in prompt
    assert str(claude_tree[0] / "frontend-dev" / "SKILL.md") in prompt

    r = await client.get(f"/api/v1/projects/{project_id}")
    assert r.json()["trigger_guide"] == guide
    assert r.json()["trigger_guide_at"] is not None


async def test_trigger_cli_failure_is_502(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    runner = FakeRunner(error="boom")
    app.dependency_overrides[get_providers] = lambda: providers_for(claude_tree)
    app.dependency_overrides[get_simulation_runner] = lambda: runner
    project_id = await _create_project(client, asset_ids=["claude:skill:frontend-dev"])

    r = await client.post(f"/api/v1/projects/{project_id}/trigger")
    assert r.status_code == 502
