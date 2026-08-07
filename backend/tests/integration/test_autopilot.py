"""Autopilot endpoints with a fake runner.

BackgroundTasks run before the request returns under httpx's ASGITransport, so
the WHOLE autopilot chain has finished by the time the POST responds — tests
assert the final row set directly.
"""

from __future__ import annotations

import json
from pathlib import Path

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.deps import get_providers, get_session_factory, get_simulation_runner
from app.main import app
from tests.helpers import FakeRunner, providers_for


def _sim_reply(payload: object) -> str:
    return f"Simulated.\n\n```simulation\n{json.dumps(payload)}\n```"


def _payload(*, score: int, suggestions: list[dict]) -> dict:
    return {
        "score": score,
        "verdict": "v",
        "summary": "s",
        "analysis": "a",
        "trace_mermaid": 'flowchart TD\n  A["x"]',
        "suggestions": suggestions,
    }


def _suggestion(path: str, content: str) -> dict:
    return {
        "title": "Improve the skill",
        "impact": "high",
        "rationale": "r",
        "changes": [{"path": path, "action": "update", "new_content": content, "description": "d"}],
    }


def _use(
    tree: tuple[Path, Path], session_factory: async_sessionmaker, *, replies: list[str]
) -> FakeRunner:
    runner = FakeRunner(replies=replies)
    app.dependency_overrides[get_providers] = lambda: providers_for(tree)
    app.dependency_overrides[get_simulation_runner] = lambda: runner
    app.dependency_overrides[get_session_factory] = lambda: session_factory
    return runner


async def _create_project(client: AsyncClient) -> str:
    r = await client.post("/api/v1/projects", json={"name": "AP project", "goal": "Ship"})
    assert r.status_code == 201
    project_id = r.json()["id"]
    r = await client.patch(
        f"/api/v1/projects/{project_id}", json={"asset_ids": ["claude:skill:frontend-dev"]}
    )
    assert r.status_code == 200
    return project_id


async def _runs(client: AsyncClient, project_id: str) -> list[dict]:
    r = await client.get(f"/api/v1/projects/{project_id}/simulations")
    assert r.status_code == 200
    return sorted(r.json(), key=lambda s: s["autopilot_iteration"] or 0)


async def test_autopilot_chains_and_stops_when_no_suggestions(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    skill_path = claude_tree[0] / "frontend-dev" / "SKILL.md"
    with_suggestion = _sim_reply(
        _payload(score=80, suggestions=[_suggestion(str(skill_path), "improved body")])
    )
    without = _sim_reply(_payload(score=91, suggestions=[]))
    runner = _use(claude_tree, session_factory, replies=[with_suggestion, without])
    project_id = await _create_project(client)

    r = await client.post(
        f"/api/v1/projects/{project_id}/simulations/autopilot",
        json={"scenario": "Do the thing", "iterations": 5},
    )
    assert r.status_code == 202
    first = r.json()
    assert first["autopilot_iteration"] == 1
    assert first["autopilot_total"] == 5
    assert first["autopilot_run_id"] is not None

    # Stopped after run 2 (no suggestions) even though 5 were allowed.
    runs = await _runs(client, project_id)
    assert len(runs) == 2
    assert [run["status"] for run in runs] == ["completed", "completed"]
    assert runs[0]["suggestions"][0]["status"] == "applied"  # auto-applied between runs
    assert runs[1]["suggestions"] == []
    assert runs[1]["autopilot_run_id"] == first["autopilot_run_id"]
    assert skill_path.read_text(encoding="utf-8") == "improved body"
    assert len(runner.calls) == 2


async def test_autopilot_respects_iteration_cap_and_leaves_last_run_pending(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    skill_path = claude_tree[0] / "frontend-dev" / "SKILL.md"
    always = _sim_reply(
        _payload(score=85, suggestions=[_suggestion(str(skill_path), "another body")])
    )
    _use(claude_tree, session_factory, replies=[always])
    project_id = await _create_project(client)

    r = await client.post(
        f"/api/v1/projects/{project_id}/simulations/autopilot",
        json={"scenario": "", "iterations": 2},
    )
    assert r.status_code == 202

    runs = await _runs(client, project_id)
    assert len(runs) == 2
    assert runs[0]["suggestions"][0]["status"] == "applied"
    # The final iteration's suggestions stay pending for manual review.
    assert runs[1]["suggestions"][0]["status"] == "pending"


async def test_autopilot_stops_when_nothing_can_be_applied(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    outside = _sim_reply(_payload(score=70, suggestions=[_suggestion("/etc/evil.md", "nope")]))
    _use(claude_tree, session_factory, replies=[outside])
    project_id = await _create_project(client)

    r = await client.post(
        f"/api/v1/projects/{project_id}/simulations/autopilot",
        json={"scenario": "", "iterations": 3},
    )
    assert r.status_code == 202

    runs = await _runs(client, project_id)
    assert len(runs) == 2
    assert runs[0]["status"] == "completed"
    assert runs[0]["suggestions"][0]["status"] == "failed"  # path outside roots
    assert runs[1]["status"] == "failed"
    assert "no suggestion could be applied" in runs[1]["error"]


async def test_autopilot_rotates_the_scenario_after_a_perfect_score(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    perfect = _sim_reply(_payload(score=100, suggestions=[]))
    scenario_reply = "Here you go.\n\n```scenario\nA brand new scenario.\n```"
    imperfect = _sim_reply(_payload(score=88, suggestions=[]))
    runner = _use(claude_tree, session_factory, replies=[perfect, scenario_reply, imperfect])
    project_id = await _create_project(client)

    r = await client.post(
        f"/api/v1/projects/{project_id}/simulations/autopilot",
        json={"scenario": "The original scenario", "iterations": 3},
    )
    assert r.status_code == 202

    runs = await _runs(client, project_id)
    # Run 1 scored 100 with nothing to apply — it chained anyway, on a new scenario.
    assert len(runs) == 2
    assert [run["status"] for run in runs] == ["completed", "completed"]
    assert runs[0]["scenario"] == "The original scenario"
    assert runs[1]["scenario"] == "A brand new scenario."
    assert len(runner.calls) == 3  # run 1, scenario generation, run 2

    project = (await client.get(f"/api/v1/projects/{project_id}")).json()
    assert project["scenario"] == "A brand new scenario."


async def test_autopilot_stops_when_the_new_scenario_cannot_be_generated(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    perfect = _sim_reply(_payload(score=100, suggestions=[]))
    _use(claude_tree, session_factory, replies=[perfect, "   "])  # empty scenario reply
    project_id = await _create_project(client)

    r = await client.post(
        f"/api/v1/projects/{project_id}/simulations/autopilot",
        json={"scenario": "The original scenario", "iterations": 3},
    )
    assert r.status_code == 202

    runs = await _runs(client, project_id)
    assert len(runs) == 2
    assert runs[0]["status"] == "completed"
    assert runs[1]["status"] == "failed"
    assert "could not generate a new scenario" in runs[1]["error"]


async def test_autopilot_validates_iterations(
    client: AsyncClient, claude_tree: tuple[Path, Path], session_factory: async_sessionmaker
) -> None:
    _use(claude_tree, session_factory, replies=[])
    project_id = await _create_project(client)
    r = await client.post(
        f"/api/v1/projects/{project_id}/simulations/autopilot",
        json={"scenario": "", "iterations": 0},
    )
    assert r.status_code == 422


async def test_stop_unknown_autopilot_is_404(client: AsyncClient) -> None:
    r = await client.post("/api/v1/simulations/autopilot/00000000-0000-0000-0000-000000000000/stop")
    assert r.status_code == 404
