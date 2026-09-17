"""v1.49 Codex assets through the API: custom agents (read, edit, validate,
propose), plugin skills, and skills switched off in config.toml."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.api.deps import get_authoring_runner, get_providers
from app.main import app
from tests.helpers import FakeRunner, providers_for

AGENT = 'name = "reviewer"\ndescription = "Reviews diffs."\ndeveloper_instructions = "Be terse."\n'


@pytest.fixture
def codex(tmp_path: Path) -> dict[str, Path]:
    home = tmp_path / "codex-home"
    skills, agents = home / "skills", home / "agents"
    for name in ("deploy", "keep"):
        (skills / name).mkdir(parents=True)
        (skills / name / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
    agents.mkdir()
    (agents / "reviewer.toml").write_text(AGENT, encoding="utf-8")
    plugin = home / "plugins" / "cache" / "openai-bundled" / "sites" / "0.1.66"
    (plugin / ".codex-plugin").mkdir(parents=True)
    (plugin / ".codex-plugin" / "plugin.json").write_text('{"skills": "./skills/"}')
    (plugin / "skills" / "publish").mkdir(parents=True)
    (plugin / "skills" / "publish" / "SKILL.md").write_text(
        "---\nname: publish\ndescription: Ship a site.\n---\n", encoding="utf-8"
    )
    config = home / "config.toml"
    config.write_text(
        f'[plugins."sites@openai-bundled"]\nenabled = true\n\n'
        f'[[skills.config]]\npath = "{skills}/deploy/SKILL.md"\nenabled = false\n',
        encoding="utf-8",
    )
    return {"home": home, "skills": skills, "agents": agents, "config": config}


@pytest_asyncio.fixture
async def api(
    client: AsyncClient, claude_tree: tuple[Path, Path], codex: dict[str, Path]
) -> AsyncClient:
    app.dependency_overrides[get_providers] = lambda: providers_for(
        claude_tree,
        codex_root=codex["skills"],
        codex_agents_root=codex["agents"],
        codex_plugins_root=codex["home"] / "plugins",
        codex_config=codex["config"],
    )
    return client


def _url(asset_id: str) -> str:
    return f"/api/v1/assets/{quote(asset_id, safe='')}"


async def test_codex_agents_and_plugin_skills_are_listed(api: AsyncClient) -> None:
    r = await api.get("/api/v1/assets")
    by_id = {a["id"]: a for a in r.json()}
    agent = by_id["codex:agent:reviewer"]
    assert (agent["kind"], agent["provider"], agent["title"]) == ("agent", "codex", "reviewer")
    assert agent["description"] == "Reviews diffs."
    assert agent["read_only"] is False
    plugin = by_id["codex-plugin:skill:sites:publish"]
    assert plugin["read_only"] is True
    assert plugin["agents"] == ["codex"]
    assert plugin["disabled"] is False and plugin["disabled_by"] is None
    assert by_id["codex:skill:deploy"]["disabled_by"] == "codex-config"
    assert by_id["claude:skill:frontend-dev"]["disabled_by"] is None


async def test_put_writes_a_valid_agent(api: AsyncClient, codex: dict[str, Path]) -> None:
    content = AGENT.replace("Be terse.", "Be thorough.") + 'model = "gpt-5.6"\n'
    r = await api.put(_url("codex:agent:reviewer"), json={"content": content})
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "gpt-5.6"
    assert (codex["agents"] / "reviewer.toml").read_text(encoding="utf-8") == content


@pytest.mark.parametrize(
    ("content", "fragment"),
    [
        ("name = ", "not valid TOML"),
        ('name = "reviewer"\ndescription = "x"\n', "developer_instructions"),
        ('name = 1\ndescription = "x"\ndeveloper_instructions = "y"\n', "must be strings"),
    ],
)
async def test_put_refuses_an_agent_codex_could_not_load(
    api: AsyncClient, codex: dict[str, Path], content: str, fragment: str
) -> None:
    r = await api.put(_url("codex:agent:reviewer"), json={"content": content})
    assert r.status_code == 400
    assert fragment in r.json()["detail"]
    assert (codex["agents"] / "reviewer.toml").read_text(encoding="utf-8") == AGENT


async def test_enabling_a_config_disabled_skill_points_at_config_toml(
    api: AsyncClient, codex: dict[str, Path]
) -> None:
    r = await api.put(f"{_url('codex:skill:deploy')}/enabled", json={"enabled": True})
    assert r.status_code == 409
    assert "config.toml" in r.json()["detail"]
    # No .disabled/ dance happened, and the config file was not touched.
    assert (codex["skills"] / "deploy" / "SKILL.md").is_file()
    assert not (codex["skills"] / ".disabled").exists()
    assert "enabled = false" in codex["config"].read_text(encoding="utf-8")

    r = await api.put(f"{_url('codex:skill:deploy')}/enabled", json={"enabled": False})
    assert r.status_code == 200  # already off: the usual no-op


async def test_a_disabled_plugin_skill_cannot_be_enabled_here(
    api: AsyncClient, codex: dict[str, Path]
) -> None:
    codex["config"].write_text('[plugins."sites@openai-bundled"]\nenabled = false\n')
    r = await api.put(f"{_url('codex-plugin:skill:sites:publish')}/enabled", json={"enabled": True})
    assert r.status_code == 409
    assert "config.toml" in r.json()["detail"]


def _reply(changes: list[dict[str, Any]]) -> str:
    body = json.dumps({"summary": "codex agent", "changes": changes})
    return f"ok\n\n```proposal\n{body}\n```"


async def _propose(api: AsyncClient, changes: list[dict[str, Any]]) -> dict[str, Any]:
    app.dependency_overrides[get_authoring_runner] = lambda: FakeRunner(reply=_reply(changes))
    sid = (await api.post("/api/v1/chat/sessions", json={})).json()["id"]
    r = await api.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "add one"})
    pid = r.json()["assistant_message"]["proposal"]["id"]
    return dict((await api.post(f"/api/v1/proposals/{pid}/accept")).json())


async def test_a_proposal_may_create_a_codex_agent(
    api: AsyncClient, codex: dict[str, Path]
) -> None:
    target = codex["agents"] / "planner.toml"
    content = AGENT.replace("reviewer", "planner")
    body = await _propose(
        api,
        [{"path": str(target), "action": "create", "new_content": content, "description": "d"}],
    )
    assert body["status"] == "applied", body
    assert target.read_text(encoding="utf-8") == content


async def test_a_proposal_with_a_broken_codex_agent_fails_before_writing(
    api: AsyncClient, codex: dict[str, Path]
) -> None:
    target = codex["agents"] / "planner.toml"
    body = await _propose(
        api,
        [{"path": str(target), "action": "create", "new_content": "nope =", "description": "d"}],
    )
    assert body["status"] == "failed"
    assert "not valid TOML" in body["error"]
    assert not target.exists()
