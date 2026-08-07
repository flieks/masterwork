"""POST /projects/{id}/suggest-links with a fake runner (never the real CLI)."""

from __future__ import annotations

import json
from pathlib import Path

from httpx import AsyncClient

from app.api.deps import get_providers, get_simulation_runner
from app.main import app
from tests.helpers import FakeRunner, providers_for


def _links_reply(links: list[dict]) -> str:
    return f"I read the catalog.\n\n```links\n{json.dumps({'links': links})}\n```"


def _use(
    tree: tuple[Path, Path], *, reply: str | None = None, error: str | None = None
) -> FakeRunner:
    runner = FakeRunner(reply=reply, error=error)
    app.dependency_overrides[get_providers] = lambda: providers_for(tree)
    app.dependency_overrides[get_simulation_runner] = lambda: runner
    return runner


async def _create_project(client: AsyncClient) -> str:
    r = await client.post("/api/v1/projects", json={"name": "P", "goal": "Ship an API"})
    assert r.status_code == 201
    return r.json()["id"]


async def test_suggest_links_returns_known_assets_with_reasons(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    runner = _use(
        claude_tree,
        reply=_links_reply(
            [
                {"asset_id": "claude:skill:backend-dev", "reason": "the API half"},
                {"asset_id": "claude:agent:architect", "reason": "design first"},
                {"asset_id": "claude:skill:nope", "reason": "hallucinated"},  # dropped
            ]
        ),
    )
    project_id = await _create_project(client)

    r = await client.post(f"/api/v1/projects/{project_id}/suggest-links")
    assert r.status_code == 200
    suggestions = r.json()["suggestions"]
    assert [s["asset_id"] for s in suggestions] == [
        "claude:skill:backend-dev",
        "claude:agent:architect",
    ]
    assert suggestions[0]["reason"] == "the API half"

    # The prompt carried the goal and the full catalog, nothing was persisted.
    prompt = runner.calls[0]["prompt"]
    assert "Ship an API" in prompt
    assert "claude:skill:frontend-dev" in prompt
    r = await client.get(f"/api/v1/projects/{project_id}")
    assert r.json()["asset_ids"] == []


async def test_suggest_links_sorted_by_confidence_desc(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    _use(
        claude_tree,
        reply=_links_reply(
            [
                {"asset_id": "claude:skill:backend-dev", "confidence": 45, "reason": "borderline"},
                {"asset_id": "claude:agent:architect", "confidence": 95, "reason": "load-bearing"},
                {"asset_id": "claude:skill:frontend-dev", "confidence": 70, "reason": "main path"},
            ]
        ),
    )
    project_id = await _create_project(client)

    r = await client.post(f"/api/v1/projects/{project_id}/suggest-links")
    assert r.status_code == 200
    suggestions = r.json()["suggestions"]
    assert [s["confidence"] for s in suggestions] == [95, 70, 45]
    assert suggestions[0]["asset_id"] == "claude:agent:architect"


async def test_suggest_links_all_unknown_502(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    _use(claude_tree, reply=_links_reply([{"asset_id": "claude:skill:nope"}]))
    project_id = await _create_project(client)
    r = await client.post(f"/api/v1/projects/{project_id}/suggest-links")
    assert r.status_code == 502


async def test_suggest_links_runner_error_502(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    _use(claude_tree, error="boom")
    project_id = await _create_project(client)
    r = await client.post(f"/api/v1/projects/{project_id}/suggest-links")
    assert r.status_code == 502


async def test_suggest_links_unknown_project_404(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    _use(claude_tree, reply=_links_reply([]))
    r = await client.post("/api/v1/projects/00000000-0000-0000-0000-000000000000/suggest-links")
    assert r.status_code == 404
