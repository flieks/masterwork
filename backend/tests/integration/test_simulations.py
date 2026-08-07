"""Simulation endpoints with a fake runner (never the real CLI).

The create endpoint schedules the run via BackgroundTasks; with httpx's
ASGITransport those run before the request call returns, so tests can assert
the completed state on the very next GET.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.deps import get_providers, get_session_factory, get_simulation_runner
from app.db.models.simulation import Simulation
from app.main import app
from tests.helpers import FakeRunner, providers_for


def _sim_reply(payload: object) -> str:
    return f"I read the assets and simulated the run.\n\n```simulation\n{json.dumps(payload)}\n```"


def _scenario_reply(scenario: str) -> str:
    return f"Here is a scenario:\n\n```scenario\n{scenario}\n```"


def _payload(
    *,
    score: int = 74,
    changes: list[dict] | None = None,
    suggestions: list[dict] | None = None,
    checklist: list[dict] | None = None,
) -> dict:
    if suggestions is None:
        suggestions = [
            {
                "title": "Sharpen the frontend-dev trigger",
                "impact": "high",
                "rationale": "The trigger misses mobile phrasing.",
                "changes": changes or [],
            }
        ]
    payload = {
        "score": score,
        "verdict": "Goal mostly achieved.",
        "summary": "1. frontend-dev handled the UI step.",
        "analysis": "## Gaps\nNo deploy coverage.",
        "trace_mermaid": 'flowchart TD\n  A["scenario"]-->B["frontend-dev"]',
        "suggestions": suggestions,
    }
    if checklist is not None:
        payload["checklist"] = checklist
    return payload


def _item(item_id: str, status: str, weight: int = 3) -> dict:
    return {
        "id": item_id,
        "title": f"capability {item_id}",
        "weight": weight,
        "status": status,
        "evidence": "frontend-dev covers it",
    }


def _use(
    tree: tuple[Path, Path],
    session_factory: async_sessionmaker,
    *,
    reply: str | None = None,
    error: str | None = None,
    stats: dict | None = None,
) -> FakeRunner:
    runner = FakeRunner(reply=reply, error=error, stats=stats)
    app.dependency_overrides[get_providers] = lambda: providers_for(tree)
    app.dependency_overrides[get_simulation_runner] = lambda: runner
    app.dependency_overrides[get_session_factory] = lambda: session_factory
    return runner


async def _create_project(client: AsyncClient, *, asset_ids: list[str] | None = None) -> str:
    r = await client.post("/api/v1/projects", json={"name": "Sim project", "goal": "Ship a UI"})
    assert r.status_code == 201
    project_id = r.json()["id"]
    # Runs refuse projects with no linked assets, so link one by default.
    links = asset_ids if asset_ids is not None else ["claude:skill:frontend-dev"]
    if links:
        r = await client.patch(f"/api/v1/projects/{project_id}", json={"asset_ids": links})
        assert r.status_code == 200
    return project_id


async def test_create_and_complete(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    skill_path = str(claude_tree[0] / "frontend-dev" / "SKILL.md")
    changes = [
        {
            "path": skill_path,
            "action": "update",
            "new_content": "updated skill body",
            "description": "rewrite trigger",
        }
    ]
    runner = _use(claude_tree, session_factory, reply=_sim_reply(_payload(changes=changes)))
    project_id = await _create_project(client, asset_ids=["claude:skill:frontend-dev"])

    r = await client.post(
        f"/api/v1/projects/{project_id}/simulations", json={"scenario": "Add a page"}
    )
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "running"
    assert body["scenario"] == "Add a page"

    # Background task already ran (ASGITransport awaits it) — poll shows completed.
    r = await client.get(f"/api/v1/simulations/{body['id']}")
    assert r.status_code == 200
    done = r.json()
    assert done["status"] == "completed"
    assert done["score"] == 74
    assert done["verdict"] == "Goal mostly achieved."
    assert done["trace_mermaid"].startswith("flowchart TD")
    assert done["completed_at"] is not None
    suggestion = done["suggestions"][0]
    assert suggestion["status"] == "pending"
    assert suggestion["changes"][0]["asset_id"] == "claude:skill:frontend-dev"

    # The prompt carried the goal, the scenario, and the linked asset's file path.
    prompt = runner.calls[0]["prompt"]
    assert "Ship a UI" in prompt and "Add a page" in prompt and skill_path in prompt


async def test_run_persists_cli_stats(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    stats = {
        "model": "claude-opus-4-6",
        "duration_ms": 123456,
        "num_turns": 7,
        "cost_usd": 1.23,
        "input_tokens": 10,
        "output_tokens": 2000,
        "cache_read_tokens": 50000,
        "cache_creation_tokens": 8000,
    }
    _use(claude_tree, session_factory, reply=_sim_reply(_payload()), stats=stats)
    project_id = await _create_project(client, asset_ids=["claude:skill:frontend-dev"])

    r = await client.post(f"/api/v1/projects/{project_id}/simulations", json={"scenario": "x"})
    assert r.status_code == 202

    r = await client.get(f"/api/v1/simulations/{r.json()['id']}")
    assert r.status_code == 200
    assert r.json()["stats"] == stats


async def test_generate_scenario_persists_to_project(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    scenario = "Add a /reports page that streams a CSV export; the export 500s on empty datasets."
    runner = _use(claude_tree, session_factory, reply=_scenario_reply(scenario))
    project_id = await _create_project(client, asset_ids=["claude:skill:frontend-dev"])

    r = await client.post(f"/api/v1/projects/{project_id}/simulations/scenario")
    assert r.status_code == 200
    assert r.json()["scenario"] == scenario

    # Persisted onto the project and visible via GET.
    r = await client.get(f"/api/v1/projects/{project_id}")
    assert r.json()["scenario"] == scenario

    # The prompt carried the goal and the linked asset's file path.
    prompt = runner.calls[0]["prompt"]
    skill_path = str(claude_tree[0] / "frontend-dev" / "SKILL.md")
    assert "Ship a UI" in prompt and skill_path in prompt


async def test_generate_scenario_falls_back_to_stripped_reply(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    # No fenced block — the service falls back to the whole stripped reply.
    _use(claude_tree, session_factory, reply="  Migrate the auth flow to Clerk and fix the 401s.  ")
    project_id = await _create_project(client)

    r = await client.post(f"/api/v1/projects/{project_id}/simulations/scenario")
    assert r.status_code == 200
    assert r.json()["scenario"] == "Migrate the auth flow to Clerk and fix the 401s."


async def test_generate_scenario_runner_error_502(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    _use(claude_tree, session_factory, error="claude timed out after 900s")
    project_id = await _create_project(client)

    r = await client.post(f"/api/v1/projects/{project_id}/simulations/scenario")
    assert r.status_code == 502
    assert "timed out" in r.json()["detail"]


async def test_generate_scenario_unknown_project_404(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    _use(claude_tree, session_factory, reply=_scenario_reply("anything"))
    r = await client.post(f"/api/v1/projects/{uuid.uuid4()}/simulations/scenario")
    assert r.status_code == 404


async def test_start_simulation_mirrors_scenario_onto_project(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    _use(claude_tree, session_factory, reply=_sim_reply(_payload()))
    project_id = await _create_project(client)

    r = await client.post(
        f"/api/v1/projects/{project_id}/simulations", json={"scenario": "Ship the export page"}
    )
    assert r.status_code == 202
    r = await client.get(f"/api/v1/projects/{project_id}")
    assert r.json()["scenario"] == "Ship the export page"

    # A later run with an empty scenario mirrors empty back onto the project.
    r = await client.post(f"/api/v1/projects/{project_id}/simulations", json={})
    assert r.status_code == 202
    r = await client.get(f"/api/v1/projects/{project_id}")
    assert r.json()["scenario"] == ""


async def test_create_unknown_project_404(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    _use(claude_tree, session_factory, reply=_sim_reply(_payload()))
    r = await client.post(f"/api/v1/projects/{uuid.uuid4()}/simulations", json={})
    assert r.status_code == 404


async def test_runner_error_marks_failed(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    _use(claude_tree, session_factory, error="claude timed out after 900s")
    project_id = await _create_project(client)
    r = await client.post(f"/api/v1/projects/{project_id}/simulations", json={})
    sim_id = r.json()["id"]

    r = await client.get(f"/api/v1/simulations/{sim_id}")
    assert r.json()["status"] == "failed"
    assert "timed out" in r.json()["error"]


async def test_missing_block_marks_failed(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    _use(claude_tree, session_factory, reply="I could not finish the evaluation.")
    project_id = await _create_project(client)
    r = await client.post(f"/api/v1/projects/{project_id}/simulations", json={})
    r = await client.get(f"/api/v1/simulations/{r.json()['id']}")
    assert r.json()["status"] == "failed"
    assert "simulation" in r.json()["error"]


async def test_conflict_while_running(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    _use(claude_tree, session_factory, reply=_sim_reply(_payload()))
    project_id = await _create_project(client)

    # Pin a running row directly — the fake runner completes too fast to race.
    async with session_factory() as db:
        db.add(Simulation(project_id=uuid.UUID(project_id), status="running", suggestions=[]))
        await db.commit()

    r = await client.post(f"/api/v1/projects/{project_id}/simulations", json={})
    assert r.status_code == 409


async def test_list_returns_newest_first(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    _use(claude_tree, session_factory, reply=_sim_reply(_payload()))
    project_id = await _create_project(client)
    for scenario in ("first", "second"):
        r = await client.post(
            f"/api/v1/projects/{project_id}/simulations", json={"scenario": scenario}
        )
        assert r.status_code == 202

    r = await client.get(f"/api/v1/projects/{project_id}/simulations")
    assert r.status_code == 200
    sims = r.json()
    assert len(sims) == 2
    assert all(s["status"] == "completed" for s in sims)


async def test_apply_suggestion_writes_files(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    new_skill = claude_tree[0] / "deployer" / "SKILL.md"
    changes = [
        {
            "path": str(new_skill),
            "action": "create",
            "new_content": "---\nname: deployer\n---\n\nDeploy things.\n",
            "description": "add missing deploy skill",
        }
    ]
    _use(claude_tree, session_factory, reply=_sim_reply(_payload(changes=changes)))
    project_id = await _create_project(client)
    r = await client.post(f"/api/v1/projects/{project_id}/simulations", json={})
    sim_id = r.json()["id"]

    r = await client.post(f"/api/v1/simulations/{sim_id}/suggestions/0/apply")
    assert r.status_code == 200
    suggestion = r.json()["suggestions"][0]
    assert suggestion["status"] == "applied"
    assert suggestion["applied_at"] is not None
    assert new_skill.read_text(encoding="utf-8").endswith("Deploy things.\n")

    # The created asset is auto-linked to the project.
    r = await client.get(f"/api/v1/projects/{project_id}")
    assert "claude:skill:deployer" in r.json()["asset_ids"]

    # Second apply of the same suggestion → 409.
    r = await client.post(f"/api/v1/simulations/{sim_id}/suggestions/0/apply")
    assert r.status_code == 409


async def test_apply_delete_change_unlinks_asset(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    doomed = claude_tree[0] / "frontend-dev" / "SKILL.md"
    changes = [
        {
            "path": str(doomed),
            "action": "delete",
            "new_content": None,
            "description": "remove the redundant skill",
        }
    ]
    _use(claude_tree, session_factory, reply=_sim_reply(_payload(changes=changes)))
    project_id = await _create_project(client, asset_ids=["claude:skill:frontend-dev"])
    r = await client.post(f"/api/v1/projects/{project_id}/simulations", json={})
    sim_id = r.json()["id"]

    r = await client.post(f"/api/v1/simulations/{sim_id}/suggestions/0/apply")
    assert r.status_code == 200
    assert r.json()["suggestions"][0]["status"] == "applied"
    assert not doomed.exists()

    r = await client.get(f"/api/v1/projects/{project_id}")
    assert "claude:skill:frontend-dev" not in r.json()["asset_ids"]


async def test_apply_rejects_path_outside_roots(
    client: AsyncClient,
    claude_tree: tuple[Path, Path],
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "elsewhere" / "evil.md"
    changes = [
        {
            "path": str(outside),
            "action": "create",
            "new_content": "nope",
            "description": "escape attempt",
        }
    ]
    _use(claude_tree, session_factory, reply=_sim_reply(_payload(changes=changes)))
    project_id = await _create_project(client)
    r = await client.post(f"/api/v1/projects/{project_id}/simulations", json={})
    sim_id = r.json()["id"]

    r = await client.post(f"/api/v1/simulations/{sim_id}/suggestions/0/apply")
    assert r.status_code == 200
    suggestion = r.json()["suggestions"][0]
    assert suggestion["status"] == "failed"
    assert "outside" in suggestion["error"]
    assert not outside.exists()


async def test_failed_suggestion_can_be_retried(
    client: AsyncClient,
    claude_tree: tuple[Path, Path],
    session_factory: async_sessionmaker,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "elsewhere" / "evil.md"
    changes = [
        {
            "path": str(outside),
            "action": "create",
            "new_content": "nope",
            "description": "escape attempt",
        }
    ]
    _use(claude_tree, session_factory, reply=_sim_reply(_payload(changes=changes)))
    project_id = await _create_project(client)
    r = await client.post(f"/api/v1/projects/{project_id}/simulations", json={})
    sim_id = r.json()["id"]

    r = await client.post(f"/api/v1/simulations/{sim_id}/suggestions/0/apply")
    assert r.json()["suggestions"][0]["status"] == "failed"

    # Retry is allowed (still fails here — the path is still outside the roots).
    r = await client.post(f"/api/v1/simulations/{sim_id}/suggestions/0/apply")
    assert r.status_code == 200
    assert r.json()["suggestions"][0]["status"] == "failed"


async def test_apply_unknown_index_404(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    _use(claude_tree, session_factory, reply=_sim_reply(_payload(suggestions=[])))
    project_id = await _create_project(client)
    r = await client.post(f"/api/v1/projects/{project_id}/simulations", json={})
    sim_id = r.json()["id"]

    r = await client.post(f"/api/v1/simulations/{sim_id}/suggestions/0/apply")
    assert r.status_code == 404


async def test_delete_simulation(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    _use(claude_tree, session_factory, reply=_sim_reply(_payload()))
    project_id = await _create_project(client)
    r = await client.post(f"/api/v1/projects/{project_id}/simulations", json={})
    sim_id = r.json()["id"]

    r = await client.delete(f"/api/v1/simulations/{sim_id}")
    assert r.status_code == 204
    r = await client.get(f"/api/v1/simulations/{sim_id}")
    assert r.status_code == 404


async def test_project_delete_cascades_simulations(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    _use(claude_tree, session_factory, reply=_sim_reply(_payload()))
    project_id = await _create_project(client)
    r = await client.post(f"/api/v1/projects/{project_id}/simulations", json={})
    sim_id = r.json()["id"]

    r = await client.delete(f"/api/v1/projects/{project_id}")
    assert r.status_code == 204
    r = await client.get(f"/api/v1/simulations/{sim_id}")
    assert r.status_code == 404


async def test_apply_link_suggestion_links_without_writing(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    existing = claude_tree[0] / "backend-dev" / "SKILL.md"
    before = existing.read_text(encoding="utf-8")
    changes = [
        {
            "path": str(existing),
            "action": "link",
            "new_content": None,
            "description": "link the existing backend-dev skill",
        }
    ]
    _use(claude_tree, session_factory, reply=_sim_reply(_payload(changes=changes)))
    project_id = await _create_project(client)
    r = await client.post(f"/api/v1/projects/{project_id}/simulations", json={})
    sim_id = r.json()["id"]

    r = await client.post(f"/api/v1/simulations/{sim_id}/suggestions/0/apply")
    assert r.status_code == 200
    assert r.json()["suggestions"][0]["status"] == "applied"
    assert existing.read_text(encoding="utf-8") == before  # no file write

    r = await client.get(f"/api/v1/projects/{project_id}")
    assert "claude:skill:backend-dev" in r.json()["asset_ids"]


async def test_apply_unlink_suggestion_unlinks_without_writing(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    existing = claude_tree[0] / "frontend-dev" / "SKILL.md"
    before = existing.read_text(encoding="utf-8")
    changes = [
        {
            "path": str(existing),
            "action": "unlink",
            "new_content": None,
            "description": "drop the unused frontend-dev skill",
        }
    ]
    _use(claude_tree, session_factory, reply=_sim_reply(_payload(changes=changes)))
    project_id = await _create_project(client)  # links claude:skill:frontend-dev
    r = await client.post(f"/api/v1/projects/{project_id}/simulations", json={})
    sim_id = r.json()["id"]

    r = await client.post(f"/api/v1/simulations/{sim_id}/suggestions/0/apply")
    assert r.status_code == 200
    assert r.json()["suggestions"][0]["status"] == "applied"
    assert existing.read_text(encoding="utf-8") == before  # no file write

    r = await client.get(f"/api/v1/projects/{project_id}")
    assert "claude:skill:frontend-dev" not in r.json()["asset_ids"]


async def test_apply_link_to_unknown_path_fails(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    changes = [
        {
            "path": "/nowhere/SKILL.md",
            "action": "link",
            "new_content": None,
            "description": "link something that is not an asset",
        }
    ]
    _use(claude_tree, session_factory, reply=_sim_reply(_payload(changes=changes)))
    project_id = await _create_project(client)
    r = await client.post(f"/api/v1/projects/{project_id}/simulations", json={})
    sim_id = r.json()["id"]

    r = await client.post(f"/api/v1/simulations/{sim_id}/suggestions/0/apply")
    assert r.status_code == 200
    suggestion = r.json()["suggestions"][0]
    assert suggestion["status"] == "failed"
    assert "not a known asset" in suggestion["error"]


async def test_prompt_lists_unlinked_assets_as_catalog(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    runner = _use(claude_tree, session_factory, reply=_sim_reply(_payload()))
    project_id = await _create_project(client, asset_ids=["claude:skill:frontend-dev"])
    await client.post(f"/api/v1/projects/{project_id}/simulations", json={})

    prompt = runner.calls[0]["prompt"]
    linked_section = prompt.split("AVAILABLE BUT UNLINKED")[0]
    catalog_section = prompt.split("AVAILABLE BUT UNLINKED")[1].split("SCENARIO TO SIMULATE")[0]
    assert "claude:skill:frontend-dev" in linked_section
    assert "claude:skill:backend-dev" in catalog_section
    assert "claude:agent:architect" in catalog_section
    assert "claude:skill:frontend-dev" not in catalog_section


async def test_run_requires_linked_assets_409(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    _use(claude_tree, session_factory, reply=_sim_reply(_payload()))
    project_id = await _create_project(client, asset_ids=[])

    r = await client.post(f"/api/v1/projects/{project_id}/simulations", json={})
    assert r.status_code == 409
    r = await client.post(f"/api/v1/projects/{project_id}/simulations/scenario")
    assert r.status_code == 409
    r = await client.post(f"/api/v1/projects/{project_id}/simulations/autopilot", json={})
    assert r.status_code == 409


async def test_prompt_annotates_assets_shared_with_other_projects(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    runner = _use(claude_tree, session_factory, reply=_sim_reply(_payload()))
    # Another project linking the same skill, plus one linking something else.
    r = await client.post("/api/v1/projects", json={"name": "Web tool", "goal": "Ship a web SaaS"})
    await client.patch(
        f"/api/v1/projects/{r.json()['id']}", json={"asset_ids": ["claude:skill:frontend-dev"]}
    )
    project_id = await _create_project(client, asset_ids=["claude:skill:frontend-dev"])
    await client.post(f"/api/v1/projects/{project_id}/simulations", json={})

    prompt = runner.calls[0]["prompt"]
    linked_section = prompt.split("AVAILABLE BUT UNLINKED")[0]
    assert "SHARED with: 'Web tool' (goal: Ship a web SaaS)" in linked_section
    assert "SHARED-ASSET RULE" in prompt


async def test_second_run_regrades_the_previous_checklist(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    checklist = [_item("deploys", "partial"), _item("tests", "pass")]
    runner = _use(claude_tree, session_factory, reply=_sim_reply(_payload(checklist=checklist)))
    project_id = await _create_project(client)

    for _ in range(2):
        r = await client.post(
            f"/api/v1/projects/{project_id}/simulations", json={"scenario": "Add a page"}
        )
        assert r.status_code == 202

    # Below the ceiling the rubric is inherited, so the run stays comparable.
    assert r.json()["control_run"] is False
    prompt = runner.calls[1]["prompt"]
    assert "MEMORY OF THE PREVIOUS RUN" in prompt
    assert "[deploys]" in prompt
    assert "CONTROL RUN" not in prompt


async def test_perfect_score_forces_a_control_run(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    # An all-pass checklist computes to 100 — the rubric has nothing left to find.
    perfect = _payload(score=100, suggestions=[], checklist=[_item("deploys", "pass")])
    runner = _use(claude_tree, session_factory, reply=_sim_reply(perfect))
    project_id = await _create_project(client)

    r = await client.post(
        f"/api/v1/projects/{project_id}/simulations", json={"scenario": "Add a page"}
    )
    assert r.json()["control_run"] is False
    r = await client.get(f"/api/v1/simulations/{r.json()['id']}")
    assert r.json()["score"] == 100

    r = await client.post(
        f"/api/v1/projects/{project_id}/simulations", json={"scenario": "Add a page"}
    )
    assert r.status_code == 202
    assert r.json()["control_run"] is True
    prompt = runner.calls[1]["prompt"]
    assert "CONTROL RUN" in prompt
    assert "MEMORY OF THE PREVIOUS RUN" not in prompt


async def test_control_run_can_be_requested_explicitly(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    checklist = [_item("deploys", "partial")]
    runner = _use(claude_tree, session_factory, reply=_sim_reply(_payload(checklist=checklist)))
    project_id = await _create_project(client)

    await client.post(f"/api/v1/projects/{project_id}/simulations", json={"scenario": "Add a page"})
    r = await client.post(
        f"/api/v1/projects/{project_id}/simulations",
        json={"scenario": "Add a page", "control_run": True},
    )
    assert r.json()["control_run"] is True
    prompt = runner.calls[1]["prompt"]
    assert "CONTROL RUN" in prompt
    assert "MEMORY OF THE PREVIOUS RUN" not in prompt
