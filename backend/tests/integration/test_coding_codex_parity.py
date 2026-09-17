"""v1.49: Codex sessions attributed at parity with Claude Code's.

Asset ids resolve to what is installed (Codex, generic and plugin skills, Codex
agents), Codex-only skill signals are read (`.system`, plugin caches, `$name`
mentions), every usage and analytics read takes a `source` filter, and a Codex
stage child is attached to its factory run by the env the runner exports.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.api.deps import get_providers
from app.api.v1.coding import service
from app.main import app
from app.observability.forwarders import codex as forwarder
from tests.helpers import providers_for

CWD = "/Users/dev/Projects/app"


@pytest.fixture
def installed(tmp_path: Path, claude_tree: tuple[Path, Path]) -> dict[str, Path]:
    """frontend-dev exists twice (Claude + Codex), shared is generic, sites:publish
    is a Codex plugin skill, reviewer a Codex custom agent."""
    home = tmp_path / "codex-home"
    codex, agents, generic = home / "skills", home / "agents", tmp_path / "agents" / "skills"
    for root, name in ((codex, "frontend-dev"), (generic, "shared"), (codex, "deploy")):
        (root / name).mkdir(parents=True)
        (root / name / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
    agents.mkdir()
    (agents / "reviewer.toml").write_text(
        'name = "reviewer"\ndescription = "d"\ndeveloper_instructions = "i"\n', encoding="utf-8"
    )
    version = home / "plugins" / "cache" / "openai-bundled" / "sites" / "0.1.66"
    (version / ".codex-plugin").mkdir(parents=True)
    (version / ".codex-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
    (version / "skills" / "publish").mkdir(parents=True)
    (version / "skills" / "publish" / "SKILL.md").write_text("---\nname: publish\n---\n")
    return {"home": home, "codex": codex, "agents": agents, "generic": generic}


@pytest_asyncio.fixture
async def api(
    client: AsyncClient, claude_tree: tuple[Path, Path], installed: dict[str, Path]
) -> AsyncClient:
    app.dependency_overrides[get_providers] = lambda: providers_for(
        claude_tree,
        generic_root=installed["generic"],
        codex_root=installed["codex"],
        codex_agents_root=installed["agents"],
        codex_plugins_root=installed["home"] / "plugins",
    )
    return client


async def _ingest(client: AsyncClient, **body: Any) -> None:
    r = await client.post("/api/v1/hooks/events", json=body)
    assert r.status_code == 204, r.text


async def _codex(client: AsyncClient, event: str, session_id: str, **raw: Any) -> None:
    body = forwarder.build_body(
        {"session_id": session_id, "hook_event_name": event, "cwd": CWD, **raw}
    )
    assert body is not None
    await _ingest(client, **body)


async def _codex_reads(client: AsyncClient, session_id: str, path: str) -> None:
    await _codex(
        client,
        "PostToolUse",
        session_id,
        tool_name="exec_command",
        tool_input={"cmd": f"sed -n '1,200p' {path}"},
        tool_response={},
    )


async def _claude_reads(client: AsyncClient, session_id: str, path: str) -> None:
    await _ingest(
        client,
        session_id=session_id,
        event_type="PostToolUse",
        tool_name="Read",
        cwd=CWD,
        payload={"tool_input": {"file_path": path}},
    )


async def _assets(client: AsyncClient, session_id: str) -> dict[str, dict[str, Any]]:
    r = await client.get(f"/api/v1/coding-sessions/{session_id}")
    assert r.status_code == 200, r.text
    return {a["name"]: a for a in r.json()["assets"]}


# --------------------------------------------------------- id resolution ---


async def test_each_agent_resolves_a_shared_name_to_its_own_copy(api: AsyncClient) -> None:
    await _codex_reads(api, "thread-1", "/Users/dev/.codex/skills/frontend-dev/SKILL.md")
    await _claude_reads(api, "claude-1", "/Users/dev/.claude/skills/frontend-dev/SKILL.md")

    codex_use = (await _assets(api, "thread-1"))["frontend-dev"]
    claude_use = (await _assets(api, "claude-1"))["frontend-dev"]
    assert (codex_use["asset_id"], codex_use["asset_found"]) == ("codex:skill:frontend-dev", True)
    assert claude_use["asset_id"] == "claude:skill:frontend-dev"


async def test_generic_plugin_and_agent_uses_link_to_real_assets(api: AsyncClient) -> None:
    await _codex_reads(api, "thread-1", "/Users/dev/.agents/skills/shared/SKILL.md")
    await _codex_reads(
        api,
        "thread-1",
        "/Users/dev/.codex/plugins/cache/openai-bundled/sites/0.1.66/skills/publish/SKILL.md",
    )
    await _codex(api, "SubagentStart", "thread-1", agent_type="reviewer", agent_id="a1")
    await _codex(api, "SubagentStart", "thread-1", agent_type="explorer", agent_id="a2")

    assets = await _assets(api, "thread-1")
    assert assets["shared"]["asset_id"] == "generic:skill:shared"
    assert assets["sites:publish"]["asset_id"] == "codex-plugin:skill:sites:publish"
    assert assets["reviewer"]["asset_id"] == "codex:agent:reviewer"
    # A built-in Codex agent has no file: the Codex form, honestly not found.
    assert (assets["explorer"]["asset_id"], assets["explorer"]["asset_found"]) == (
        "codex:agent:explorer",
        False,
    )


async def test_a_codex_read_of_the_shared_folder_resolves_to_the_generic_asset(
    api: AsyncClient, installed: dict[str, Path]
) -> None:
    # A pre-v1.50 link in ~/.codex/skills is deduped: still one generic asset.
    (installed["codex"] / "shared").symlink_to(installed["generic"] / "shared")
    await _codex_reads(api, "thread-1", "/Users/dev/.agents/skills/shared/SKILL.md")
    await _codex_reads(api, "thread-2", "/Users/dev/.codex/skills/shared/SKILL.md")

    for thread in ("thread-1", "thread-2"):
        use = (await _assets(api, thread))["shared"]
        assert (use["asset_id"], use["asset_found"]) == ("generic:skill:shared", True)
    r = await api.get("/api/v1/assets/" + quote("generic:skill:shared", safe=""))
    assert r.json()["agents"] == ["codex"]


async def test_a_system_skill_read_is_counted(api: AsyncClient) -> None:
    await _codex_reads(api, "thread-1", "/Users/dev/.codex/skills/.system/imagegen/SKILL.md")
    assert "imagegen" in await _assets(api, "thread-1")


async def test_the_asset_page_splits_same_named_copies_by_agent(api: AsyncClient) -> None:
    await _codex_reads(api, "thread-1", "/Users/dev/.codex/skills/frontend-dev/SKILL.md")
    await _claude_reads(api, "claude-1", "/Users/dev/.claude/skills/frontend-dev/SKILL.md")

    async def runs(asset_id: str, **params: Any) -> list[str]:
        url = f"/api/v1/coding-assets/{quote(asset_id, safe='')}/sessions"
        r = await api.get(url, params=params)
        assert r.status_code == 200, r.text
        return [row["session_id"] for row in r.json()]

    assert await runs("codex:skill:frontend-dev") == ["thread-1"]
    assert await runs("claude:skill:frontend-dev") == ["claude-1"]
    assert await runs("claude:skill:frontend-dev", source="codex") == []


async def test_the_rollup_resolves_by_the_source_filter(api: AsyncClient) -> None:
    await _codex_reads(api, "thread-1", "/Users/dev/.codex/skills/frontend-dev/SKILL.md")
    await _claude_reads(api, "claude-1", "/Users/dev/.claude/skills/frontend-dev/SKILL.md")

    async def usage(**params: Any) -> list[dict[str, Any]]:
        r = await api.get("/api/v1/coding-assets", params=params)
        assert r.status_code == 200, r.text
        return list(r.json())

    (both,) = await usage()
    assert (both["asset_id"], both["sessions"]) == ("claude:skill:frontend-dev", 2)
    (codex_only,) = await usage(source="codex")
    assert (codex_only["asset_id"], codex_only["sessions"]) == ("codex:skill:frontend-dev", 1)
    assert (await api.get("/api/v1/coding-assets", params={"source": "cursor"})).status_code == 422


async def test_a_backfill_needs_no_stored_id_to_follow_a_migrated_skill(
    api: AsyncClient, installed: dict[str, Path]
) -> None:
    await _codex_reads(api, "thread-1", "/Users/dev/.codex/skills/deploy/SKILL.md")
    assert (await _assets(api, "thread-1"))["deploy"]["asset_id"] == "codex:skill:deploy"
    # Made generic after the run: the recorded name is unchanged, the id follows.
    (installed["codex"] / "deploy").rename(installed["generic"] / "deploy")
    assert (await _assets(api, "thread-1"))["deploy"]["asset_id"] == "generic:skill:deploy"


# ------------------------------------------------------------- mentions ---


async def test_a_dollar_mention_of_an_installed_skill_counts(api: AsyncClient) -> None:
    await _codex(
        api,
        "UserPromptSubmit",
        "thread-1",
        prompt="Use $deploy then $shared, $deploy again, $sites:publish and $not-installed.",
    )
    assets = await _assets(api, "thread-1")
    assert set(assets) == {"deploy", "shared", "sites:publish"}
    assert assets["deploy"]["uses"] == 1  # once per prompt
    r = await api.get(f"/api/v1/coding-assets/{quote('codex:skill:deploy', safe='')}/sessions")
    (run,) = r.json()
    assert run["calls"][0]["source"] == "skill_mention"
    assert run["calls"][0]["input"] == {"mention": "$deploy"}


async def test_mentions_are_codex_only_and_skip_inspection_runs(
    api: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _ingest(
        api,
        session_id="claude-1",
        event_type="UserPromptSubmit",
        cwd=CWD,
        payload={"prompt": "costs $deploy dollars"},
    )
    assert await _assets(api, "claude-1") == {}

    monkeypatch.setattr(service, "INSPECTION_CWDS", ("/Users/dev/.masterwork",))
    body = forwarder.build_body(
        {
            "session_id": "inspect-1",
            "hook_event_name": "UserPromptSubmit",
            "cwd": "/Users/dev/.masterwork",
            "prompt": "Explain when $deploy triggers.",
        }
    )
    assert body is not None
    await _ingest(api, **body)
    assert await _assets(api, "inspect-1") == {}


# --------------------------------------------------------- source filter ---


async def _two_agents(client: AsyncClient) -> None:
    await _codex(client, "UserPromptSubmit", "thread-1", prompt="codex work")
    await _claude_reads(client, "claude-1", "/Users/dev/.claude/skills/frontend-dev/SKILL.md")


async def test_the_sessions_list_filters_by_agent(api: AsyncClient) -> None:
    await _two_agents(api)

    async def ids(**params: Any) -> set[str]:
        r = await api.get("/api/v1/coding-sessions", params={"include_empty": True, **params})
        assert r.status_code == 200, r.text
        return {s["id"] for s in r.json()}

    assert await ids() == {"thread-1", "claude-1"}
    assert await ids(source="codex") == {"thread-1"}
    assert await ids(source="claude-code") == {"claude-1"}


async def test_analytics_filter_by_agent(api: AsyncClient) -> None:
    await _two_agents(api)

    async def runs(**params: Any) -> set[str]:
        r = await api.get("/api/v1/coding-analytics/runs", params=params)
        assert r.status_code == 200, r.text
        return {row["session_id"] for row in r.json()}

    assert await runs() == {"thread-1", "claude-1"}
    assert await runs(source="codex") == {"thread-1"}
    for path in ("gates", "roles", "models"):
        r = await api.get(f"/api/v1/coding-analytics/{path}", params={"source": "codex"})
        assert r.status_code == 200, r.text


# ------------------------------------------------- factory attribution ---


async def test_a_codex_stage_child_links_to_its_run_by_env(
    api: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _ingest(
        api,
        session_id="factory-abc",
        event_type="phase_start",
        cwd="/repo",
        payload={"event": "phase_start", "phase": "build", "agent": "build"},
    )
    monkeypatch.setenv(forwarder.FACTORY_RUN_ID_ENV, "abc")
    monkeypatch.setenv(forwarder.FACTORY_STAGE_ENV, "build")
    monkeypatch.setattr(forwarder, "ancestry", lambda: [])
    await _codex(api, "SessionStart", "codex-child", source="exec")

    r = await api.get("/api/v1/coding-sessions/codex-child")
    child = r.json()
    assert child["source"] == "codex"
    assert child["parent_session_id"] == "factory-abc"
    assert child["launch_mode"] == "automated"
