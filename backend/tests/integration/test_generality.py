"""Generality-audit endpoint with a fake runner."""

from __future__ import annotations

from pathlib import Path

from httpx import AsyncClient

from app.api.deps import get_providers, get_simulation_runner
from app.main import app
from tests.helpers import FakeRunner, providers_for


def _audit_reply(markdown: str) -> str:
    return f"Read the files.\n\n```generality\n{markdown}\n```"


async def _create_project(client: AsyncClient) -> str:
    r = await client.post("/api/v1/projects", json={"name": "Gen project", "goal": "Ship"})
    assert r.status_code == 201
    return r.json()["id"]


async def test_audit_without_assets_short_circuits(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    runner = FakeRunner(reply=_audit_reply("should not be called"))
    app.dependency_overrides[get_simulation_runner] = lambda: runner
    app.dependency_overrides[get_providers] = lambda: providers_for(claude_tree)
    project_id = await _create_project(client)

    r = await client.post(f"/api/v1/projects/{project_id}/generality-audit")
    assert r.status_code == 200
    assert "No assets linked yet" in r.json()["generality_report"]
    assert runner.calls == []  # no claude round trip when nothing is linked

    r = await client.get(f"/api/v1/projects/{project_id}")
    assert "No assets linked yet" in r.json()["generality_report"]
    assert r.json()["generality_report_at"] is not None


async def test_audit_reads_linked_assets_and_persists(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    runner = FakeRunner(reply=_audit_reply("## Verdict\nStayed general."))
    app.dependency_overrides[get_simulation_runner] = lambda: runner
    app.dependency_overrides[get_providers] = lambda: providers_for(claude_tree)
    project_id = await _create_project(client)

    # Link the assets the audit should read.
    r = await client.patch(
        f"/api/v1/projects/{project_id}",
        json={"asset_ids": ["claude:skill:backend-dev", "claude:agent:architect"]},
    )
    assert r.status_code == 200

    r = await client.post(f"/api/v1/projects/{project_id}/generality-audit")
    assert r.status_code == 200
    assert r.json()["generality_report"] == "## Verdict\nStayed general."

    # The prompt named the linked assets so the model reads each file.
    prompt = runner.calls[0]["prompt"]
    assert "claude:skill:backend-dev" in prompt
    assert "claude:agent:architect" in prompt

    r = await client.get(f"/api/v1/projects/{project_id}")
    assert r.json()["generality_report"] == "## Verdict\nStayed general."
    assert r.json()["generality_report_at"] is not None
