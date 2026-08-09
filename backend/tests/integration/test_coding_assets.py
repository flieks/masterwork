"""v1.14: which skills and which subagents a run actually used.

Against the real test database, like the rest of the coding suite. The subagent
signals touch the filesystem, so the transcript sidecars are written into
`tmp_path` and referenced by absolute path — nothing here reads `~/.claude`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.v1.coding import service

SKILLS = "/Users/me/.claude/skills"


async def _ingest(client: AsyncClient, **body: Any) -> None:
    r = await client.post("/api/v1/hooks/events", json=body)
    assert r.status_code == 204, r.text


async def _session(client: AsyncClient, session_id: str = "s1") -> dict[str, Any]:
    r = await client.get(f"/api/v1/coding-sessions/{session_id}")
    assert r.status_code == 200, r.text
    return dict(r.json())


async def _assets(client: AsyncClient, session_id: str = "s1") -> list[dict[str, Any]]:
    return list((await _session(client, session_id))["assets"])


async def _usage(client: AsyncClient, **params: Any) -> list[dict[str, Any]]:
    r = await client.get("/api/v1/coding-assets", params=params)
    assert r.status_code == 200, r.text
    return list(r.json())


async def _read(client: AsyncClient, path: str, session_id: str = "s1") -> None:
    await _ingest(
        client,
        session_id=session_id,
        event_type="PostToolUse",
        tool_name="Read",
        payload={"tool_input": {"file_path": path}},
    )


def _transcript(tmp_path: Path, agent_id: str, agent_type: str | None) -> str:
    """A subagent transcript, with the sidecar that names its type when asked."""
    jsonl = tmp_path / f"agent-{agent_id}.jsonl"
    jsonl.write_text('{"type": "user"}\n', encoding="utf-8")
    if agent_type is not None:
        (tmp_path / f"agent-{agent_id}.meta.json").write_text(
            json.dumps({"agentType": agent_type, "spawnDepth": 1}), encoding="utf-8"
        )
    return str(jsonl)


# ------------------------------------------------------------ the signals ---


async def test_an_explicit_skill_call_is_recorded(client: AsyncClient) -> None:
    await _ingest(
        client,
        session_id="s1",
        event_type="PostToolUse",
        tool_name="Skill",
        payload={"tool_input": {"skill": "caveman"}},
    )
    assert await _assets(client) == [
        {
            "kind": "skill",
            "name": "caveman",
            "asset_id": "claude:skill:caveman",
            "lane": "main",
            "uses": 1,
        }
    ]


async def test_reading_a_skill_md_counts_as_using_the_skill(client: AsyncClient) -> None:
    """The signal that actually fires: across 2 237 recorded tool calls there
    were two explicit Skill calls, and hundreds of SKILL.md reads."""
    await _read(client, f"{SKILLS}/backend-dev/SKILL.md")
    await _read(client, f"{SKILLS}/backend-dev/SKILL.md")
    await _read(client, f"{SKILLS}/tdd/SKILL.md")

    assert [(a["name"], a["uses"]) for a in await _assets(client)] == [
        ("backend-dev", 2),
        ("tdd", 1),
    ]


async def test_a_glob_for_a_skill_md_counts_too(client: AsyncClient) -> None:
    await _ingest(
        client,
        session_id="s1",
        event_type="PostToolUse",
        tool_name="Glob",
        payload={"tool_input": {"pattern": f"{SKILLS}/graphify/SKILL.md"}},
    )
    assert [a["name"] for a in await _assets(client)] == ["graphify"]


async def test_an_ordinary_file_read_is_not_an_asset(client: AsyncClient) -> None:
    await _read(client, "/Users/me/Projects/app/README.md")
    await _read(client, "/Users/me/Projects/app/docs/SKILL.md.bak")
    assert await _assets(client) == []


async def test_writing_a_skill_is_not_using_it(client: AsyncClient) -> None:
    """Authoring an asset is what the rest of masterwork is for."""
    await _ingest(
        client,
        session_id="s1",
        event_type="PostToolUse",
        tool_name="Write",
        payload={"tool_input": {"file_path": f"{SKILLS}/new-thing/SKILL.md"}},
    )
    assert await _assets(client) == []


async def test_a_denied_tool_call_is_not_a_use(client: AsyncClient) -> None:
    """PreToolUse fires before the permission answer; only PostToolUse means it ran."""
    await _ingest(
        client,
        session_id="s1",
        event_type="PreToolUse",
        tool_name="Read",
        payload={"tool_input": {"file_path": f"{SKILLS}/tdd/SKILL.md"}},
    )
    assert await _assets(client) == []


async def test_a_task_call_names_the_subagent(client: AsyncClient) -> None:
    await _ingest(
        client,
        session_id="s1",
        event_type="PostToolUse",
        tool_name="Task",
        payload={"tool_input": {"subagent_type": "code-reviewer"}},
    )
    assert await _assets(client) == [
        {
            "kind": "agent",
            "name": "code-reviewer",
            "asset_id": "claude:agent:code-reviewer",
            "lane": "main",
            "uses": 1,
        }
    ]


async def test_a_subagent_stop_is_named_from_its_transcript(
    client: AsyncClient, tmp_path: Path
) -> None:
    """No Task events exist in the real data at all — a subagent is only ever
    visible as the SubagentStop that ends it, pointing at a transcript."""
    path = _transcript(tmp_path, "aaa", "backend-developer")
    await _ingest(
        client,
        session_id="s1",
        event_type="SubagentStop",
        payload={"agent_transcript_path": path},
    )
    assert [(a["kind"], a["name"]) for a in await _assets(client)] == [
        ("agent", "backend-developer")
    ]


async def test_an_unreadable_transcript_degrades_to_subagent(
    client: AsyncClient, tmp_path: Path
) -> None:
    """A missing sidecar must cost the name, never the ingest."""
    missing = _transcript(tmp_path, "bbb", None)
    await _ingest(
        client,
        session_id="s1",
        event_type="SubagentStop",
        payload={"agent_transcript_path": missing},
    )
    await _ingest(
        client,
        session_id="s1",
        event_type="SubagentStop",
        payload={"agent_transcript_path": str(tmp_path / "gone" / "agent-ccc.jsonl")},
    )
    assert [(a["name"], a["uses"]) for a in await _assets(client)] == [("subagent", 2)]


async def test_a_stated_agent_type_beats_the_transcript(
    client: AsyncClient, tmp_path: Path
) -> None:
    path = _transcript(tmp_path, "ddd", "backend-developer")
    await _ingest(
        client,
        session_id="s1",
        event_type="SubagentStop",
        payload={"agent_type": "architect", "agent_transcript_path": path},
    )
    assert [a["name"] for a in await _assets(client)] == ["architect"]


async def test_a_malformed_sidecar_never_fails_the_ingest(
    client: AsyncClient, tmp_path: Path
) -> None:
    jsonl = tmp_path / "agent-eee.jsonl"
    jsonl.write_text("{}\n", encoding="utf-8")
    (tmp_path / "agent-eee.meta.json").write_text("not json at all", encoding="utf-8")
    await _ingest(
        client,
        session_id="s1",
        event_type="SubagentStop",
        payload={"agent_transcript_path": str(jsonl)},
    )
    assert [a["name"] for a in await _assets(client)] == ["subagent"]


# ----------------------------------------------------------------- lanes ---


async def test_a_factory_run_attributes_per_stage(client: AsyncClient) -> None:
    """Which stage reached for what is the point of tracking the lane at all."""
    for stage in ("plan", "build"):
        await _ingest(
            client,
            session_id="s1",
            event_type="PostToolUse",
            tool_name="Read",
            workflow="factory",
            phase={"name": stage},
            agent=stage,
            payload={"tool_input": {"file_path": f"{SKILLS}/backend-dev/SKILL.md"}},
        )
    assert [(a["name"], a["lane"], a["uses"]) for a in await _assets(client)] == [
        ("backend-dev", "build", 1),
        ("backend-dev", "plan", 1),
    ]


# ------------------------------------------------------ the rollup endpoint ---


async def test_the_rollup_ranks_by_uses_across_sessions(client: AsyncClient) -> None:
    await _read(client, f"{SKILLS}/tdd/SKILL.md", "s1")
    await _read(client, f"{SKILLS}/tdd/SKILL.md", "s1")
    await _read(client, f"{SKILLS}/tdd/SKILL.md", "s2")
    await _read(client, f"{SKILLS}/caveman/SKILL.md", "s2")
    await _ingest(
        client,
        session_id="s2",
        event_type="PostToolUse",
        tool_name="Task",
        payload={"tool_input": {"subagent_type": "architect"}},
    )

    assert [(r["kind"], r["name"], r["sessions"], r["uses"]) for r in await _usage(client)] == [
        ("skill", "tdd", 2, 3),
        ("agent", "architect", 1, 1),
        ("skill", "caveman", 1, 1),
    ]
    assert (await _usage(client))[0]["asset_id"] == "claude:skill:tdd"


async def test_the_rollup_filters_by_kind(client: AsyncClient) -> None:
    await _read(client, f"{SKILLS}/tdd/SKILL.md")
    await _ingest(
        client,
        session_id="s1",
        event_type="PostToolUse",
        tool_name="Task",
        payload={"tool_input": {"subagent_type": "architect"}},
    )
    assert [r["name"] for r in await _usage(client, kind="agent")] == ["architect"]
    assert [r["name"] for r in await _usage(client, kind="skill")] == ["tdd"]


async def test_the_rollup_filters_by_since(client: AsyncClient) -> None:
    await _read(client, f"{SKILLS}/tdd/SKILL.md")
    rows = await _usage(client)
    last_used = rows[0]["last_used_at"]

    assert [r["name"] for r in await _usage(client, since=last_used)] == ["tdd"]
    assert await _usage(client, since="2099-01-01T00:00:00Z") == []


async def test_the_rollup_is_empty_before_anything_is_used(client: AsyncClient) -> None:
    assert await _usage(client) == []


# --------------------------------------------------------------- backfill ---


async def test_backfill_rebuilds_assets_and_is_idempotent(
    client: AsyncClient, session_factory: async_sessionmaker, tmp_path: Path
) -> None:
    """History is not blank: a session recorded before v1.14 gets its assets by
    replaying its own stored events. Running it twice must not double a count."""
    await _read(client, f"{SKILLS}/backend-dev/SKILL.md")
    await _read(client, f"{SKILLS}/backend-dev/SKILL.md")
    await _ingest(
        client,
        session_id="s1",
        event_type="SubagentStop",
        payload={"agent_transcript_path": _transcript(tmp_path, "fff", "qa-tester")},
    )
    before = await _assets(client)

    async with session_factory() as db:
        first = await service.backfill_session(db, "s1")
        second = await service.backfill_session(db, "s1")

    assert first.assets == second.assets == 2
    assert await _assets(client) == before


async def test_backfill_all_replays_every_session(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    await _read(client, f"{SKILLS}/tdd/SKILL.md", "s1")
    await _read(client, f"{SKILLS}/caveman/SKILL.md", "s2")

    async with session_factory() as db:
        totals = await service.backfill_all(db)

    assert (totals.sessions, totals.assets) == (2, 2)
    assert sorted(r["name"] for r in await _usage(client)) == ["caveman", "tdd"]


async def test_backfill_relinks_a_factory_child(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """A parent's stages are rebuilt too, so the replay must run oldest-first
    for a child to still find the stage it belonged to."""
    await _ingest(
        client,
        session_id="factory-abc",
        event_type="phase_start",
        cwd="/repo",
        payload={"event": "phase_start", "phase": "build", "agent": "build"},
    )
    await _ingest(
        client,
        session_id="kid",
        event_type="SessionStart",
        cwd="/repo",
        payload={
            "launched_by": [
                "1 /Users/me/.local/bin/claude -p …",
                "2 /usr/bin/python factory/run.py --repo /repo the request",
            ]
        },
    )

    async with session_factory() as db:
        await service.backfill_all(db)

    child = await _session(client, "kid")
    assert child["parent_session_id"] == "factory-abc"
    assert child["title"] == "build stage · factory-abc"


async def test_the_rollup_leaves_out_masterworks_own_inspection_runs(
    client: AsyncClient,
) -> None:
    """masterwork Reads every linked asset's SKILL.md to analyse it. Counting that
    would rank assets by how often they were inspected, not used."""
    await _ingest(
        client,
        session_id="inspect",
        event_type="SessionStart",
        cwd=service.INSPECTION_CWD,
    )
    await _read(client, f"{SKILLS}/tdd/SKILL.md", "inspect")
    await _read(client, f"{SKILLS}/mobile-dev/SKILL.md", "inspect")
    await _ingest(client, session_id="real", event_type="SessionStart", cwd="/repo")
    await _read(client, f"{SKILLS}/tdd/SKILL.md", "real")

    assert [(r["name"], r["uses"]) for r in await _usage(client)] == [("tdd", 1)]

    both = {r["name"]: r["uses"] for r in await _usage(client, include_inspection="true")}
    assert both == {"tdd": 2, "mobile-dev": 1}


# ------------------------------------------------- the per-asset session log ---


async def _log(client: AsyncClient, asset_id: str, **params: Any) -> list[dict[str, Any]]:
    r = await client.get(f"/api/v1/coding-assets/{asset_id}/sessions", params=params)
    assert r.status_code == 200, r.text
    return list(r.json())


async def test_the_log_lists_the_runs_that_used_an_asset(client: AsyncClient) -> None:
    await _ingest(
        client,
        session_id="s1",
        event_type="UserPromptSubmit",
        cwd="/repo",
        payload={"prompt": "ship the settings page"},
    )
    await _read(client, f"{SKILLS}/tdd/SKILL.md", "s1")
    await _read(client, f"{SKILLS}/tdd/SKILL.md", "s1")
    await _ingest(
        client,
        session_id="s2",
        event_type="UserPromptSubmit",
        cwd="/other",
        payload={"prompt": "fix the flaky test"},
    )
    await _read(client, f"{SKILLS}/tdd/SKILL.md", "s2")

    rows = await _log(client, "claude:skill:tdd")
    assert [(r["session_id"], r["title"], r["uses"]) for r in rows] == [
        ("s2", "fix the flaky test", 1),
        ("s1", "ship the settings page", 2),
    ]


async def test_the_log_carries_the_arguments_a_skill_was_called_with(client: AsyncClient) -> None:
    await _ingest(
        client,
        session_id="s1",
        event_type="PostToolUse",
        tool_name="Skill",
        payload={"tool_input": {"skill": "caveman", "args": "ultra"}},
    )
    (row,) = await _log(client, "claude:skill:caveman")
    assert [(c["source"], c["input"]) for c in row["calls"]] == [("skill_call", {"args": "ultra"})]


async def test_the_log_carries_the_brief_a_subagent_was_spawned_with(client: AsyncClient) -> None:
    await _ingest(
        client,
        session_id="s1",
        event_type="PostToolUse",
        tool_name="Task",
        payload={
            "tool_input": {
                "subagent_type": "code-reviewer",
                "description": "Review the branch",
                "prompt": "Review every change on this branch for contract drift.",
            }
        },
    )
    (row,) = await _log(client, "claude:agent:code-reviewer")
    (call,) = row["calls"]
    assert call["source"] == "spawn_call"
    assert call["input"] == {
        "subagent_type": "code-reviewer",
        "description": "Review the branch",
        "prompt": "Review every change on this branch for contract drift.",
    }


async def test_a_skill_read_records_the_path_and_no_arguments(client: AsyncClient) -> None:
    """A read is how a skill loads, not how it is called — there are no args to show."""
    await _read(client, f"{SKILLS}/tdd/SKILL.md")
    (row,) = await _log(client, "claude:skill:tdd")
    assert [(c["source"], c["input"]) for c in row["calls"]] == [
        ("skill_read", {"path": f"{SKILLS}/tdd/SKILL.md"})
    ]


async def test_a_subagent_stop_records_a_call_with_no_input(
    client: AsyncClient, tmp_path: Path
) -> None:
    await _ingest(
        client,
        session_id="s1",
        event_type="SubagentStop",
        payload={"agent_transcript_path": _transcript(tmp_path, "log", "qa-tester")},
    )
    (row,) = await _log(client, "claude:agent:qa-tester")
    assert [(c["source"], c["input"]) for c in row["calls"]] == [("subagent_stop", None)]


async def test_the_log_names_the_lane_that_made_the_call(client: AsyncClient) -> None:
    await _ingest(
        client,
        session_id="s1",
        event_type="PostToolUse",
        tool_name="Read",
        workflow="factory",
        phase={"name": "build"},
        agent="build",
        payload={"tool_input": {"file_path": f"{SKILLS}/backend-dev/SKILL.md"}},
    )
    (row,) = await _log(client, "claude:skill:backend-dev")
    assert [c["lane"] for c in row["calls"]] == ["build"]


async def test_a_plugin_assets_own_id_finds_its_uses(client: AsyncClient) -> None:
    """A plugin skill is recorded under the name Claude Code calls it by, while
    its asset id names the provider that installed it."""
    await _ingest(
        client,
        session_id="s1",
        event_type="PostToolUse",
        tool_name="Skill",
        payload={"tool_input": {"skill": "vercel:deploy"}},
    )
    rows = await _log(client, "claude-plugin:skill:vercel:deploy")
    assert [r["session_id"] for r in rows] == ["s1"]


async def test_the_log_leaves_out_masterworks_own_inspection_runs(client: AsyncClient) -> None:
    await _ingest(
        client, session_id="inspect", event_type="SessionStart", cwd=service.INSPECTION_CWD
    )
    await _read(client, f"{SKILLS}/tdd/SKILL.md", "inspect")

    assert await _log(client, "claude:skill:tdd") == []
    both = await _log(client, "claude:skill:tdd", include_inspection="true")
    assert [r["session_id"] for r in both] == ["inspect"]


async def test_an_unused_asset_has_an_empty_log(client: AsyncClient) -> None:
    assert await _log(client, "claude:skill:never-used") == []


async def test_a_malformed_asset_id_is_rejected(client: AsyncClient) -> None:
    r = await client.get("/api/v1/coding-assets/not-an-id/sessions")
    assert r.status_code == 400, r.text


async def test_backfill_rebuilds_the_log_without_doubling_it(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    await _read(client, f"{SKILLS}/tdd/SKILL.md")
    await _read(client, f"{SKILLS}/tdd/SKILL.md")

    async with session_factory() as db:
        await service.backfill_session(db, "s1")
        await service.backfill_session(db, "s1")

    (row,) = await _log(client, "claude:skill:tdd")
    assert (row["uses"], len(row["calls"])) == (2, 2)
