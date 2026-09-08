"""A Codex session, end to end: what its forwarder posts, read back as a run.

The bodies here are exactly what `forwarders/codex.py` builds, so the ingest is
exercised on the real shape rather than a hand-made one. Against the real test
database like the rest of the coding suite.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from app.observability.forwarders import codex as forwarder

SKILL = "/Users/dev/.agents/skills/code-review/SKILL.md"


async def _ingest(client: AsyncClient, **body: Any) -> None:
    r = await client.post("/api/v1/hooks/events", json=body)
    assert r.status_code == 204, r.text


async def _hook(client: AsyncClient, event: str, session_id: str = "thread-1", **raw: Any) -> None:
    """Post what the Codex forwarder would post for this hook firing."""
    body = forwarder.build_body(
        {
            "session_id": session_id,
            "hook_event_name": event,
            "cwd": "/Users/dev/Projects/app",
            "model": "gpt-6-astra",
            "permission_mode": "default",
            **raw,
        }
    )
    assert body is not None
    await _ingest(client, **body)


async def _session(client: AsyncClient, session_id: str = "thread-1") -> dict[str, Any]:
    r = await client.get(f"/api/v1/coding-sessions/{session_id}")
    assert r.status_code == 200, r.text
    return dict(r.json())


async def test_a_codex_session_is_filed_under_codex_and_reads_back_whole(
    client: AsyncClient, monkeypatch: Any
) -> None:
    monkeypatch.setattr(forwarder, "ancestry", lambda: ["1 /Applications/Codex.app"])
    await _hook(client, "SessionStart", source="startup")
    await _hook(client, "UserPromptSubmit", turn_id="t1", prompt="review the auth module")
    await _hook(
        client,
        "PostToolUse",
        turn_id="t1",
        tool_name="exec_command",
        tool_input={"cmd": f"sed -n '1,240p' {SKILL}", "workdir": "/Users/dev/Projects/app"},
        tool_response={"output": "# Code review\n..."},
    )
    await _hook(client, "SubagentStart", agent_type="spec_review", agent_id="a1")
    await _hook(client, "SubagentStop", agent_type="spec_review", agent_id="a1")
    await _hook(client, "Stop", turn_id="t1", last_assistant_message="Reviewed.")
    await _ingest(
        client,
        session_id="thread-1",
        event_type="Stop",
        source="codex",
        stats={"tokens_in": 350, "tokens_out": 40, "tokens_total": 390, "cache_read_tokens": 200},
    )
    await _hook(client, "SessionEnd", reason="exit")

    session = await _session(client)
    assert session["source"] == "codex"
    assert session["model"] == "gpt-6-astra"
    assert session["title"] == "review the auth module"
    assert session["status"] == "success"
    assert session["ended_at"] is not None
    assert session["launch_mode"] == "interactive"
    # Tokens landed; no price for this model, so no cost was invented.
    assert session["tokens_total"] == 390
    assert session["cache_read_tokens"] == 200
    assert session["cost_usd"] is None

    # The skill it printed and the subagent it spawned, on their lanes.
    by_key = {(a["kind"], a["name"]): a for a in session["assets"]}
    assert by_key[("skill", "code-review")]["uses"] == 1
    assert by_key[("skill", "code-review")]["lane"] == "main"
    assert by_key[("agent", "spec_review")]["uses"] == 2  # start and stop
    assert [lane["name"] for lane in session["agents"]] == ["main", "spec_review"]

    # One turn on main (prompt → Stop), one span on the subagent's lane.
    phases = {(p["agent"], p["name"]): p for p in session["phases"]}
    assert phases[("main", "turn 1")]["status"] == "passed"
    assert phases[("spec_review", "spec_review")]["status"] == "passed"

    # It lists as a run, badged the same way.
    listed = (await client.get("/api/v1/coding-sessions")).json()
    assert [(s["id"], s["source"]) for s in listed] == [("thread-1", "codex")]


async def test_the_skill_read_is_in_the_asset_log_with_its_path(client: AsyncClient) -> None:
    await _hook(client, "SessionStart", source="startup")
    await _hook(
        client,
        "PostToolUse",
        tool_name="exec_command",
        tool_input={"cmd": f"cat {SKILL}"},
        tool_response={"output": ""},
    )
    r = await client.get("/api/v1/coding-assets/generic:skill:code-review/sessions")
    assert r.status_code == 200, r.text
    (use,) = r.json()
    assert use["session_id"] == "thread-1"
    assert use["calls"][0]["source"] == "skill_read"
    assert use["calls"][0]["input"] == {"path": SKILL}


async def test_a_permission_request_mid_turn_reads_as_waiting_on_the_person(
    client: AsyncClient,
) -> None:
    await _hook(client, "SessionStart", source="startup")
    await _hook(client, "UserPromptSubmit", turn_id="t1", prompt="clean the build dir")
    await _hook(
        client,
        "PermissionRequest",
        turn_id="t1",
        tool_name="exec_command",
        tool_input={"cmd": "rm -rf build"},
    )
    session = await _session(client)
    assert session["status"] == "waiting_input"
    assert session["awaiting_input_since"] is not None

    # The person approved: work resumed, the wait is over.
    await _hook(
        client,
        "PostToolUse",
        turn_id="t1",
        tool_name="exec_command",
        tool_input={"cmd": "rm -rf build"},
        tool_response={"output": ""},
    )
    assert (await _session(client))["status"] == "running"


async def test_an_interrupt_closes_the_turn_without_calling_it_done(
    client: AsyncClient,
) -> None:
    await _hook(client, "SessionStart", source="startup")
    await _hook(client, "UserPromptSubmit", turn_id="t1", prompt="do the long thing")
    await _hook(client, "Interrupt", turn_id="t1")

    session = await _session(client)
    (turn,) = session["phases"]
    assert turn["status"] == "abandoned"
    assert turn["ended_at"] is not None
    assert session["agents"][0]["turns"] == 1
    # Nothing is waiting on the person after they cut the turn short.
    await _hook(client, "PermissionRequest", tool_name="exec_command", tool_input={"cmd": "ls"})
    assert (await _session(client))["status"] == "running"


async def test_a_headless_codex_exec_is_an_automated_run(
    client: AsyncClient, monkeypatch: Any
) -> None:
    monkeypatch.setattr(forwarder, "ancestry", lambda: ["42 codex exec …", "1 /sbin/launchd"])
    await _hook(client, "SessionStart", source="startup")
    assert (await _session(client))["launch_mode"] == "automated"


async def test_an_event_codex_alone_has_is_stored_as_it_came(client: AsyncClient) -> None:
    await _hook(client, "SessionStart", source="startup")
    await _hook(client, "PreCompact", turn_id="t1")
    events = (await client.get("/api/v1/coding-sessions/thread-1/events")).json()
    assert [e["event_type"] for e in events] == ["SessionStart", "PreCompact"]
    assert events[1]["agent"] is None
    assert events[1]["payload"] == {"turn_id": "t1"}


async def test_a_forwarder_that_says_nothing_still_files_under_claude_code(
    client: AsyncClient,
) -> None:
    await _ingest(client, session_id="old", event_type="SessionStart")
    assert (await _session(client, "old"))["source"] == "claude-code"


async def test_a_source_nobody_knows_falls_back_rather_than_422ing(client: AsyncClient) -> None:
    await _ingest(client, session_id="odd", event_type="SessionStart", source="cursor")
    assert (await _session(client, "odd"))["source"] == "claude-code"
