"""POST /skills/installed/match with a fake one-shot runner (never the real CLI)."""

from __future__ import annotations

import json
from pathlib import Path

from httpx import AsyncClient

from app.api.deps import get_light_runner, get_providers
from app.main import app
from tests.helpers import FakeRunner, providers_for

_URL = "/api/v1/skills/installed/match"


def _use(tree: tuple[Path, Path], *, reply: str | None = None, error: str | None = None) -> None:
    app.dependency_overrides[get_providers] = lambda: providers_for(tree)
    app.dependency_overrides[get_light_runner] = lambda: FakeRunner(reply=reply, error=error)


async def test_matches_come_back_in_the_models_order(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    _use(
        claude_tree,
        reply=json.dumps(
            [
                {"name": "backend-dev", "reason": "FastAPI work"},
                {"name": "frontend-dev", "reason": "React work"},
            ]
        ),
    )

    r = await client.post(_URL, json={"query": "I need to build an API"})

    assert r.status_code == 200
    assert r.json()["matches"] == [
        {"name": "backend-dev", "reason": "FastAPI work"},
        {"name": "frontend-dev", "reason": "React work"},
    ]


async def test_a_name_that_is_not_installed_is_dropped(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    _use(
        claude_tree,
        reply=json.dumps(
            [
                {"name": "frontend-dev", "reason": "React work"},
                {"name": "nonexistent-skill", "reason": "made up"},
            ]
        ),
    )

    r = await client.post(_URL, json={"query": "React"})

    assert [m["name"] for m in r.json()["matches"]] == ["frontend-dev"]


async def test_more_than_eight_matches_are_capped(client: AsyncClient, tmp_path: Path) -> None:
    skills_root = tmp_path / "skills"
    agents_root = tmp_path / "agents"
    skills_root.mkdir()
    agents_root.mkdir()
    names = [f"skill-{i}" for i in range(10)]
    for name in names:
        skill_dir = skills_root / name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: A test skill.\n---\n\nBody.\n", encoding="utf-8"
        )

    _use(
        (skills_root, agents_root),
        reply=json.dumps([{"name": name, "reason": "fits"} for name in names]),
    )

    r = await client.post(_URL, json={"query": "anything"})

    assert len(r.json()["matches"]) == 8


async def test_runner_failure_502s(client: AsyncClient, claude_tree: tuple[Path, Path]) -> None:
    _use(claude_tree, error="claude timed out after 300s")

    r = await client.post(_URL, json={"query": "anything"})

    assert r.status_code == 502


async def test_non_json_reply_502s(client: AsyncClient, claude_tree: tuple[Path, Path]) -> None:
    _use(claude_tree, reply="Sorry, I don't know.")

    r = await client.post(_URL, json={"query": "anything"})

    assert r.status_code == 502


async def test_empty_array_returns_200_with_no_matches(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    _use(claude_tree, reply="[]")

    r = await client.post(_URL, json={"query": "something nothing installed does"})

    assert r.status_code == 200
    assert r.json()["matches"] == []


async def test_the_prompt_lists_every_installed_skill_name(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    runner = FakeRunner(reply="[]")
    app.dependency_overrides[get_providers] = lambda: providers_for(claude_tree)
    app.dependency_overrides[get_light_runner] = lambda: runner

    await client.post(_URL, json={"query": "anything"})

    prompt = runner.calls[0]["prompt"]
    assert "frontend-dev" in prompt
    assert "backend-dev" in prompt
