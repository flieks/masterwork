"""v1.13: stages, agent lanes, and the derivation that gives every run both.

Against the real test database, like the rest of the coding suite.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.v1.coding import service
from app.core.exceptions import CodingSessionNotFoundError
from app.repositories import coding as coding_repo


async def _ingest(client: AsyncClient, **body: Any) -> None:
    r = await client.post("/api/v1/hooks/events", json=body)
    assert r.status_code == 204, r.text


async def _session(client: AsyncClient, session_id: str = "s1") -> dict[str, Any]:
    r = await client.get(f"/api/v1/coding-sessions/{session_id}")
    assert r.status_code == 200, r.text
    return dict(r.json())


async def _events(client: AsyncClient, session_id: str = "s1") -> list[dict[str, Any]]:
    return list((await client.get(f"/api/v1/coding-sessions/{session_id}/events")).json())


def _lanes(session: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {a["name"]: a for a in session["agents"]}


def _phases(session: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["name"]: p for p in session["phases"]}


# ------------------------------------------------------- explicit v1.13 ---


async def test_phase_and_agent_blocks_upsert_rows(client: AsyncClient) -> None:
    await _ingest(
        client,
        session_id="s1",
        event_type="stage",
        workflow="factory",
        title="Add a subtract() function",
        phase={"name": "build", "seq": 2, "kind": "agent", "agent": "builder"},
        agent={"name": "builder", "model": "opus", "color": "#f0f"},
    )

    session = await _session(client)
    assert session["title"] == "Add a subtract() function"
    assert session["workflow"] == "factory"
    assert session["status"] == "running"
    build = _phases(session)["build"]
    assert (build["seq"], build["kind"], build["agent"]) == (2, "agent", "builder")
    assert (build["status"], build["ended_at"], build["duration_ms"]) == ("running", None, None)
    assert _lanes(session)["builder"]["model"] == "opus"
    assert _lanes(session)["builder"]["color"] == "#f0f"


async def test_phase_updates_are_partial(client: AsyncClient) -> None:
    """A second event fills in what the first could not know, and clears nothing."""
    await _ingest(
        client,
        session_id="s1",
        event_type="stage",
        phase={"name": "review", "seq": 1, "kind": "agent", "description": "read-only"},
    )
    await _ingest(
        client,
        session_id="s1",
        event_type="stage",
        phase={
            "name": "review",
            "seq": 1,
            "status": "passed",
            "duration_ms": 1234,
            "corrections": 2,
        },
    )

    phase = (await _session(client))["phases"][0]
    assert (phase["status"], phase["duration_ms"]) == ("passed", 1234)

    detail = _phases(await _session(client))["review"]
    assert detail["kind"] == "agent"  # not cleared by the second event
    assert detail["description"] == "read-only"
    assert detail["corrections"] == 2
    assert detail["ended_at"] is not None  # a terminal status closes the stage


async def test_a_closed_phase_without_a_duration_gets_one(client: AsyncClient) -> None:
    await _ingest(client, session_id="s1", event_type="stage", phase={"name": "checks", "seq": 1})
    await _ingest(
        client, session_id="s1", event_type="stage", phase={"name": "checks", "status": "passed"}
    )

    phase = (await _session(client))["phases"][0]
    assert phase["duration_ms"] is not None and phase["duration_ms"] >= 0


async def test_a_bare_string_names_the_phase_and_lane(client: AsyncClient) -> None:
    """A hook that only knows the name must not have to learn the envelope."""
    await _ingest(client, session_id="s1", event_type="stage", phase="plan", agent="planner")

    session = await _session(client)
    assert [p["name"] for p in session["phases"]] == ["plan"]
    assert list(_lanes(session)) == ["planner"]


async def test_unknown_and_unusable_fields_never_422(client: AsyncClient) -> None:
    """A hook must never fail because the backend moved on and it did not."""
    r = await client.post(
        "/api/v1/hooks/events",
        json={
            "session_id": "s1",
            "event_type": "PostToolUse",
            "tool_name": "Bash",
            "some_future_field": {"nested": True},
            "duration_ms": "not a number",
            "phase": ["not", "an", "object"],
            "ok": "absolutely",
        },
    )
    assert r.status_code == 204, r.text

    event = (await _events(client))[0]
    assert event["duration_ms"] is None
    assert event["ok"] is None
    assert (await _session(client))["phases"] == []


async def test_events_carry_their_phase_lane_and_outcome(client: AsyncClient) -> None:
    await _ingest(
        client,
        session_id="s1",
        event_type="stage",
        phase={"name": "build", "seq": 1},
        agent="builder",
        ok=True,
        duration_ms=500,
    )
    # No phase block: it belongs to whatever is still running.
    await _ingest(client, session_id="s1", event_type="PostToolUse", tool_name="Edit")

    stage_id = (await _session(client))["phases"][0]["id"]
    first, second = await _events(client)
    assert first["phase_id"] == stage_id
    assert first["agent"] == "builder"
    assert first["ok"] is True
    assert first["duration_ms"] == 500
    # A duration means the work finished as it was reported.
    assert first["ended_at"] == first["created_at"]
    assert second["phase_id"] == stage_id
    assert second["ended_at"] is None


async def test_a_turn_is_counted_whoever_reports_it(client: AsyncClient) -> None:
    """`turns` is derived from the event type, not from the producer — an
    explicit `agent` block and the factory's payload have to agree."""
    await _ingest(
        client,
        session_id="s1",
        event_type="agent_turn",
        phase={"name": "plan", "seq": 1},
        agent={"name": "plan", "context_tokens": 180_707},
    )
    await _ingest(
        client, session_id="s1", event_type="agent_turn", phase={"name": "plan"}, agent="plan"
    )

    assert _lanes(await _session(client))["plan"]["turns"] == 2


async def test_session_totals_roll_up_from_phases(client: AsyncClient) -> None:
    for seq, cost in ((1, 0.25), (2, 0.75)):
        await _ingest(
            client,
            session_id="s1",
            event_type="stage",
            phase={
                "name": f"p{seq}",
                "seq": seq,
                "cost_usd": cost,
                "tokens_in": 100 * seq,
                "tokens_out": seq,
            },
        )

    session = await _session(client)
    assert session["cost_usd"] == pytest.approx(1.0)
    assert (session["tokens_in"], session["tokens_out"]) == (300, 3)
    assert session["tokens_total"] == 303


async def test_stats_keys_with_columns_are_promoted(client: AsyncClient) -> None:
    """`stats` stays the overflow, but the card should not have to parse it."""
    await _ingest(
        client,
        session_id="s1",
        event_type="Stop",
        stats={"cost_usd": 0.5, "cache_read_input_tokens": 900, "turns": 3},
    )

    session = await _session(client)
    assert session["cost_usd"] == pytest.approx(0.5)
    assert session["cache_read_tokens"] == 900
    assert session["stats"]["turns"] == 3


# ------------------------------------------------------- read endpoints ---


async def test_list_carries_phases_and_lanes(client: AsyncClient) -> None:
    """One call has to be enough to draw a card's mini-lane chart."""
    await _ingest(
        client,
        session_id="s1",
        event_type="stage",
        phase={"name": "plan", "seq": 1, "status": "passed", "duration_ms": 10},
        agent="planner",
    )
    await _ingest(
        client,
        session_id="s1",
        event_type="stage",
        phase={"name": "build", "seq": 2},
        agent="builder",
    )

    listed = (await client.get("/api/v1/coding-sessions")).json()
    assert len(listed) == 1
    assert [(p["seq"], p["name"], p["agent"], p["status"]) for p in listed[0]["phases"]] == [
        (1, "plan", "planner", "passed"),
        (2, "build", "builder", "running"),
    ]
    assert [a["name"] for a in listed[0]["agents"]] == ["planner", "builder"]
    # The card shape is deliberately small: no ids, no descriptions.
    assert set(listed[0]["phases"][0]) == {
        "seq",
        "name",
        "agent",
        "status",
        "started_at",
        "duration_ms",
    }


async def test_detail_returns_whole_phase_rows(client: AsyncClient) -> None:
    await _ingest(
        client,
        session_id="s1",
        event_type="stage",
        phase={
            "name": "plan",
            "seq": 1,
            "kind": "agent",
            "description": "plan.md",
            "cost_usd": 0.06,
            "commit_sha": "abc123",
        },
    )

    phase = (await _session(client))["phases"][0]
    assert phase["kind"] == "agent"
    assert phase["description"] == "plan.md"
    assert phase["cost_usd"] == pytest.approx(0.06)
    assert phase["commit_sha"] == "abc123"
    assert (phase["gates_passed"], phase["gates_failed"], phase["corrections"]) == (0, 0, 0)


async def test_workflow_filter_treats_null_as_chat(client: AsyncClient) -> None:
    await _ingest(client, session_id="pipeline", event_type="run", workflow="factory", phase="plan")
    await _ingest(
        client, session_id="typed", event_type="UserPromptSubmit", payload={"prompt": "x"}
    )

    factory = (await client.get("/api/v1/coding-sessions?workflow=factory")).json()
    assert [s["id"] for s in factory] == ["pipeline"]

    chat = (await client.get("/api/v1/coding-sessions?workflow=chat")).json()
    assert [s["id"] for s in chat] == ["typed"]
    assert chat[0]["workflow"] is None  # never claimed one, still counts as chat


async def test_status_filter(client: AsyncClient) -> None:
    await _ingest(client, session_id="open", event_type="PostToolUse", tool_name="Read")
    await _ingest(client, session_id="done", event_type="PostToolUse", tool_name="Read")
    await _ingest(client, session_id="done", event_type="SessionEnd", ended=True)
    await _ingest(client, session_id="broken", event_type="PostToolUse", tool_name="Read")
    await _ingest(client, session_id="broken", event_type="run_end", status="failed")

    for status, expected in (("running", ["open"]), ("success", ["done"]), ("failed", ["broken"])):
        listed = (await client.get(f"/api/v1/coding-sessions?status={status}")).json()
        assert [s["id"] for s in listed] == expected, status


# ------------------------------- derivation for plain Claude Code hooks ---


async def test_a_prompt_opens_a_turn_and_a_stop_closes_it(client: AsyncClient) -> None:
    await _ingest(
        client, session_id="s1", event_type="UserPromptSubmit", payload={"prompt": "do the thing"}
    )
    await _ingest(client, session_id="s1", event_type="PostToolUse", tool_name="Read")
    await _ingest(client, session_id="s1", event_type="Stop")
    await _ingest(
        client, session_id="s1", event_type="UserPromptSubmit", payload={"prompt": "more"}
    )

    session = await _session(client)
    assert [(p["seq"], p["name"], p["status"], p["kind"]) for p in session["phases"]] == [
        (1, "turn 1", "passed", "agent"),
        (2, "turn 2", "running", "agent"),
    ]
    assert all(p["agent"] == "main" for p in session["phases"])
    assert session["phases"][0]["ended_at"] is not None
    assert session["phases"][1]["ended_at"] is None

    turn1, turn2 = (p["id"] for p in session["phases"])
    assert [e["phase_id"] for e in await _events(client)] == [turn1, turn1, turn1, turn2]


async def test_a_prompt_closes_a_main_turn_whose_stop_never_arrived(
    client: AsyncClient,
) -> None:
    """A dropped `Stop` used to leave its turn running for the rest of the run,
    so it covered every later turn on the chart."""
    await _ingest(client, session_id="s1", event_type="UserPromptSubmit", payload={"prompt": "a"})
    # No Stop: the next prompt is the only proof the turn ended.
    await _ingest(client, session_id="s1", event_type="UserPromptSubmit", payload={"prompt": "b"})
    await _ingest(client, session_id="s1", event_type="Stop")

    phases = (await _session(client))["phases"]
    assert [(p["name"], p["status"]) for p in phases] == [
        ("turn 1", "abandoned"),
        ("turn 2", "passed"),
    ]
    # Closed, and not left claiming the rest of the run. `abandoned` is the
    # whole claim about its end; the description still says what started it.
    assert phases[0]["ended_at"] is not None
    assert phases[0]["duration_ms"] is not None
    assert phases[0]["ended_at"] <= phases[1]["started_at"]
    assert phases[0]["description"] == "a"


NOTIFICATION = """<task-notification>
<task-id>ad383abed70243167</task-id>
<status>completed</status>
<summary>Agent "Build factory pipeline runner" finished</summary>
<result>Built and verified.</result>
</task-notification>"""


async def test_a_turn_records_what_started_it(client: AsyncClient) -> None:
    """Most turns of a long session are not a person typing — a background task
    finishing re-enters as a prompt too — and a stage with no stated cause reads
    as one that started for no reason."""
    await _ingest(
        client,
        session_id="s1",
        event_type="UserPromptSubmit",
        payload={"prompt": "ok go to stage3\nand tell me when it lands"},
    )
    await _ingest(client, session_id="s1", event_type="Stop")
    await _ingest(
        client, session_id="s1", event_type="UserPromptSubmit", payload={"prompt": NOTIFICATION}
    )
    await _ingest(client, session_id="s1", event_type="Stop")
    await _ingest(
        client,
        session_id="s1",
        event_type="UserPromptSubmit",
        payload={"prompt": "<system-reminder>\nThe user opened a file\n</system-reminder>"},
    )

    assert [p["description"] for p in (await _session(client))["phases"]] == [
        # A person: their opening line, not the whole message.
        "ok go to stage3",
        # A machine: the envelope's own one-liner, not its markup.
        'resumed — Agent "Build factory pipeline runner" finished',
        "resumed — system reminder",
    ]


async def test_a_notification_without_a_summary_still_says_what_happened(
    client: AsyncClient,
) -> None:
    await _ingest(
        client,
        session_id="s1",
        event_type="UserPromptSubmit",
        payload={"prompt": "<task-notification>\n<status>failed</status>\n</task-notification>"},
    )

    assert (await _session(client))["phases"][0][
        "description"
    ] == "resumed — background task failed"


async def test_backfill_over_http_closes_a_turn_recorded_before_the_fix(
    client: AsyncClient,
) -> None:
    """Stages are derived, so a run recorded while the leak existed keeps its
    open turn until its own events are replayed. That is what the route is for."""
    await _ingest(client, session_id="s1", event_type="UserPromptSubmit", payload={"prompt": "a"})
    await _ingest(client, session_id="s1", event_type="UserPromptSubmit", payload={"prompt": "b"})
    await _ingest(client, session_id="s1", event_type="Stop")

    r = await client.post("/api/v1/coding-sessions/s1/backfill")
    assert r.status_code == 200, r.text
    assert r.json()["session_id"] == "s1"
    assert r.json()["phases"] == 2

    phases = (await _session(client))["phases"]
    assert [(p["name"], p["status"]) for p in phases] == [
        ("turn 1", "abandoned"),
        ("turn 2", "passed"),
    ]

    # Idempotent: the derived rows are dropped and rebuilt, never doubled.
    await client.post("/api/v1/coding-sessions/s1/backfill")
    assert len((await _session(client))["phases"]) == 2


async def test_backfill_of_an_unknown_session_is_a_404(client: AsyncClient) -> None:
    assert (await client.post("/api/v1/coding-sessions/nope/backfill")).status_code == 404


async def test_two_agents_of_one_type_do_not_close_each_other(client: AsyncClient) -> None:
    """`parallel()` spawns several agents of the same type at once, so two open
    turns on a subagent lane are two agents working, not a lost hook."""
    spawn = {"tool_input": {"subagent_type": "researcher", "description": "look it up"}}
    await _ingest(client, session_id="s1", event_type="UserPromptSubmit", payload={"prompt": "a"})
    await _ingest(client, session_id="s1", event_type="PreToolUse", tool_name="Task", payload=spawn)
    await _ingest(client, session_id="s1", event_type="PreToolUse", tool_name="Task", payload=spawn)

    lane = [p for p in (await _session(client))["phases"] if p["agent"] == "researcher"]
    assert [p["status"] for p in lane] == ["running", "running"]


async def test_title_is_the_first_prompt_truncated(client: AsyncClient) -> None:
    await _ingest(
        client, session_id="s1", event_type="UserPromptSubmit", payload={"prompt": "x" * 500}
    )
    await _ingest(
        client, session_id="s1", event_type="UserPromptSubmit", payload={"prompt": "second"}
    )

    assert (await _session(client))["title"] == "x" * 300


async def test_main_lane_takes_the_session_model_and_counts_turns(client: AsyncClient) -> None:
    await _ingest(client, session_id="s1", event_type="SessionStart", model="opus")
    await _ingest(client, session_id="s1", event_type="UserPromptSubmit", payload={"prompt": "a"})
    await _ingest(client, session_id="s1", event_type="Stop")
    await _ingest(client, session_id="s1", event_type="UserPromptSubmit", payload={"prompt": "b"})
    await _ingest(client, session_id="s1", event_type="Stop")

    main = _lanes(await _session(client))["main"]
    assert main["model"] == "opus"
    assert main["turns"] == 2


async def test_a_lane_per_subagent_seen(client: AsyncClient) -> None:
    await _ingest(client, session_id="s1", event_type="UserPromptSubmit", payload={"prompt": "a"})
    await _ingest(
        client,
        session_id="s1",
        event_type="PostToolUse",
        tool_name="Task",
        payload={"tool_input": {"subagent_type": "researcher", "prompt": "look it up"}},
    )
    await _ingest(
        client, session_id="s1", event_type="SubagentStop", payload={"agent_type": "researcher"}
    )
    await _ingest(
        client,
        session_id="s1",
        event_type="PostToolUse",
        tool_name="Task",
        payload={"tool_input": {"subagent_type": "researcher"}},
    )
    await _ingest(client, session_id="s1", event_type="Stop")

    lanes = _lanes(await _session(client))
    assert list(lanes) == ["main", "researcher"]  # one lane per type, not per call
    assert lanes["researcher"]["turns"] == 1
    assert lanes["main"]["turns"] == 1


async def test_a_truncated_tool_input_does_not_invent_a_lane(client: AsyncClient) -> None:
    await _ingest(client, session_id="s1", event_type="UserPromptSubmit", payload={"prompt": "a"})
    await _ingest(
        client,
        session_id="s1",
        event_type="PostToolUse",
        tool_name="Task",
        payload={"tool_input": {"_truncated": '{"subagent_type": "resea'}},
    )

    assert list(_lanes(await _session(client))) == ["main"]


async def test_a_spawn_opens_a_span_on_the_subagents_own_lane(client: AsyncClient) -> None:
    """The gap that left every subagent lane an empty row: a lane was declared,
    but nothing ever gave it a stage, so it had nothing to draw."""
    await _ingest(client, session_id="s1", event_type="UserPromptSubmit", payload={"prompt": "a"})
    await _ingest(
        client,
        session_id="s1",
        event_type="PreToolUse",
        tool_name="Agent",
        payload={"tool_input": {"subagent_type": "backend-developer", "description": "Add route"}},
    )
    await _ingest(client, session_id="s1", event_type="PostToolUse", tool_name="Bash")
    session = await _session(client)
    span = _phases(session)["Add route"]
    assert (span["agent"], span["status"], span["ended_at"]) == (
        "backend-developer",
        "running",
        None,
    )
    # main's own tool call belongs to main's turn, not to the span running beside it.
    assert (await _events(client))[-1]["phase_id"] == _phases(session)["turn 1"]["id"]

    await _ingest(
        client,
        session_id="s1",
        event_type="SubagentStop",
        payload={"agent_type": "backend-developer"},
    )
    await _ingest(client, session_id="s1", event_type="Stop")

    session = await _session(client)
    span, turn = _phases(session)["Add route"], _phases(session)["turn 1"]
    assert (span["status"], span["duration_ms"] is not None) == ("passed", True)
    assert turn["status"] == "passed"
    assert _lanes(session)["backend-developer"]["turns"] == 1


async def test_a_stop_does_not_close_a_subagents_span(client: AsyncClient) -> None:
    await _ingest(client, session_id="s1", event_type="UserPromptSubmit", payload={"prompt": "a"})
    await _ingest(
        client,
        session_id="s1",
        event_type="PreToolUse",
        tool_name="Agent",
        payload={"tool_input": {"subagent_type": "qa-tester"}},
    )
    await _ingest(client, session_id="s1", event_type="Stop")

    phases = _phases(await _session(client))
    assert phases["turn 1"]["status"] == "passed"
    assert phases["qa-tester"]["status"] == "running"


async def test_turns_are_numbered_per_lane(client: AsyncClient) -> None:
    """`seq` is session-wide, so once a span sits between two prompts it can no
    longer double as the turn number — main would jump from 1 to 3."""
    for _ in range(2):
        await _ingest(
            client, session_id="s1", event_type="UserPromptSubmit", payload={"prompt": "a"}
        )
        await _ingest(
            client,
            session_id="s1",
            event_type="PreToolUse",
            tool_name="Agent",
            payload={"tool_input": {"subagent_type": "qa-tester"}},
        )
        await _ingest(client, session_id="s1", event_type="SubagentStop")
        await _ingest(client, session_id="s1", event_type="Stop")

    session = await _session(client)
    assert [p["name"] for p in session["phases"] if p["agent"] == "main"] == ["turn 1", "turn 2"]
    assert [p["name"] for p in session["phases"] if p["agent"] == "qa-tester"] == [
        "qa-tester",
        "qa-tester",
    ]


async def test_an_unnamed_subagent_stop_still_lands_on_a_lane(client: AsyncClient) -> None:
    """More than half of real `SubagentStop`s carry only a transcript path."""
    await _ingest(client, session_id="s1", event_type="UserPromptSubmit", payload={"prompt": "a"})
    await _ingest(
        client,
        session_id="s1",
        event_type="SubagentStop",
        payload={"agent_transcript_path": "/nowhere/agent-1.jsonl"},
    )

    session = await _session(client)
    assert _lanes(session)["subagent"]["turns"] == 1
    # No spawn event ever recorded a start, so the lane gets the instant it ended.
    marker = session["phases"][-1]
    assert (marker["agent"], marker["duration_ms"], marker["status"]) == ("subagent", 0, "passed")
    assert "start not recorded" in marker["description"]


async def test_session_end_succeeds_the_run(client: AsyncClient) -> None:
    await _ingest(client, session_id="s1", event_type="UserPromptSubmit", payload={"prompt": "a"})
    assert (await _session(client))["status"] == "running"

    await _ingest(client, session_id="s1", event_type="SessionEnd", ended=True)
    assert (await _session(client))["status"] == "success"


async def test_a_ghost_session_grows_no_lanes(client: AsyncClient) -> None:
    """Three quarters of all rows are discarded startup processes; they must not
    each cost two extra inserts."""
    await _ingest(client, session_id="ghost", event_type="SessionStart")
    await _ingest(client, session_id="ghost", event_type="SessionEnd", ended=True)

    session = await _session(client, "ghost")
    assert session["agents"] == []
    assert session["phases"] == []


# ------------------------------------------ derivation from factory rows ---

FACTORY_RUN: list[dict[str, Any]] = [
    {
        "event_type": "phase_start",
        "payload": {
            "event": "phase_start",
            "phase": "run",
            "agent": "",
            "detail": "Add subtract()",
            "result": "ok",
        },
    },
    {
        "event_type": "phase_start",
        "payload": {
            "event": "phase_start",
            "phase": "plan",
            "agent": "plan",
            "detail": "plan.md",
            "result": "ok",
        },
    },
    {
        "event_type": "agent_turn",
        "payload": {
            "event": "agent_turn",
            "phase": "plan",
            "agent": "plan",
            "result": "ok",
            "cost_usd": 0.06,
            "tokens_in": 180_707,
            "tokens_out": 2_223,
            "duration_ms": 28_156,
        },
    },
    {
        "event_type": "gate_pass",
        "payload": {"event": "gate_pass", "phase": "plan", "agent": "plan", "result": "ok"},
    },
    {
        "event_type": "gate_fail",
        "payload": {"event": "gate_fail", "phase": "plan", "agent": "plan", "result": "fail"},
    },
    {
        "event_type": "commit",
        "payload": {
            "event": "commit",
            "phase": "plan",
            "agent": "plan",
            "result": "ok",
            "payload": {"sha": "1f91475"},
        },
    },
    {
        "event_type": "phase_end",
        "payload": {
            "event": "phase_end",
            "phase": "plan",
            "agent": "plan",
            "detail": "planned it",
            "result": "ok",
            "cost_usd": 0.06,
            "duration_ms": 30_151,
            "payload": {"corrections": 1, "commit": "1f91475"},
        },
    },
    {
        "event_type": "phase_start",
        "payload": {
            "event": "phase_start",
            "phase": "checks",
            "agent": "",
            "detail": "1 command",
            "result": "ok",
        },
    },
    {
        "event_type": "phase_end",
        "payload": {
            "event": "phase_end",
            "phase": "checks",
            "agent": "",
            "detail": "1 check passed",
            "result": "ok",
            "duration_ms": 87,
            "payload": {"corrections": 0},
        },
    },
    {
        "event_type": "run_end",
        "ended": True,
        "payload": {
            "event": "run_end",
            "phase": "run",
            "agent": "",
            "result": "ok",
            "stats": {"cost_usd": 0.06, "turns": 1},
        },
    },
]


async def _ingest_factory_run(client: AsyncClient, session_id: str = "s1") -> None:
    for event in FACTORY_RUN:
        await _ingest(client, session_id=session_id, **event)


async def test_a_factory_payload_is_promoted_without_the_new_blocks(client: AsyncClient) -> None:
    """The runner predates the `phase`/`agent` blocks and cannot be asked to
    change, so its own vocabulary is read straight out of the payload."""
    await _ingest_factory_run(client)

    session = await _session(client)
    assert session["workflow"] == "factory"
    assert session["status"] == "success"
    assert session["title"] == "Add subtract()"  # the run envelope's detail

    phases = _phases(session)
    assert list(phases) == ["plan", "checks"]  # the "run" envelope is not a stage
    assert phases["plan"]["kind"] == "agent"
    assert phases["checks"]["kind"] == "code"  # nobody owned it
    assert phases["plan"]["status"] == "passed"
    assert phases["plan"]["duration_ms"] == 30_151
    assert phases["plan"]["description"] == "planned it"  # phase_end, not phase_start
    assert phases["plan"]["commit_sha"] == "1f91475"
    assert phases["plan"]["corrections"] == 1
    assert (phases["plan"]["gates_passed"], phases["plan"]["gates_failed"]) == (1, 1)
    assert phases["plan"]["tokens_in"] == 180_707

    lane = _lanes(session)["plan"]
    assert (lane["turns"], lane["tokens_out"]) == (1, 2_223)
    assert lane["cost_usd"] == pytest.approx(0.06)
    assert lane["context_tokens"] == 180_707

    assert session["cost_usd"] == pytest.approx(0.06)
    assert session["tokens_total"] == 182_930


async def test_a_failed_factory_run_is_failed(client: AsyncClient) -> None:
    await _ingest(
        client,
        session_id="s1",
        event_type="run_end",
        ended=True,
        payload={"event": "run_end", "phase": "run", "agent": "", "result": "fail"},
    )
    assert (await _session(client))["status"] == "failed"


async def test_factory_events_land_in_their_stage(client: AsyncClient) -> None:
    await _ingest_factory_run(client)

    session = await _session(client)
    plan_id = _phases(session)["plan"]["id"]
    linked = [e for e in await _events(client) if e["phase_id"] == plan_id]
    assert [e["event_type"] for e in linked] == [
        "phase_start",
        "agent_turn",
        "gate_pass",
        "gate_fail",
        "commit",
        "phase_end",
    ]
    assert [e["ok"] for e in linked] == [True, True, True, False, True, True]
    assert all(e["agent"] == "plan" for e in linked)


# ------------------------------------------------------------- backfill ---


async def _snapshot(client: AsyncClient) -> dict[str, Any]:
    """The derived state, minus the row ids a rebuild is free to change."""
    session = await _session(client)
    return {
        "title": session["title"],
        "workflow": session["workflow"],
        "status": session["status"],
        "cost_usd": session["cost_usd"],
        "tokens_total": session["tokens_total"],
        "phases": [{k: v for k, v in p.items() if k != "id"} for p in session["phases"]],
        "agents": session["agents"],
    }


async def test_backfill_rebuilds_what_ingest_derived(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """A pre-v1.13 session has the events but none of the derived rows; the
    backfill replays them through the same derivation the ingest runs."""
    await _ingest_factory_run(client)
    expected = await _snapshot(client)

    async with session_factory() as db:
        await coding_repo.clear_derived(db, "s1")
        stale = await coding_repo.get_session(db, "s1")
        assert stale is not None
        stale.title = stale.workflow = None
        stale.status = "running"
        stale.cost_usd = stale.tokens_total = None
        await db.commit()

    stripped = await _session(client)
    assert (stripped["phases"], stripped["agents"], stripped["title"]) == ([], [], None)

    async with session_factory() as db:
        result = await service.backfill_session(db, "s1")
    assert (result.events, result.phases, result.agents) == (len(FACTORY_RUN), 2, 1)
    assert await _snapshot(client) == expected


async def test_backfill_is_idempotent(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """Gates and turns are counted across events, so a rebuild that updated in
    place instead of replacing would double them."""
    await _ingest_factory_run(client)
    expected = await _snapshot(client)

    for _ in range(2):
        async with session_factory() as db:
            await service.backfill_session(db, "s1")
        assert await _snapshot(client) == expected


async def test_backfill_relinks_events_to_the_new_rows(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    await _ingest_factory_run(client)
    async with session_factory() as db:
        await service.backfill_session(db, "s1")

    live_ids = {p["id"] for p in (await _session(client))["phases"]}
    linked = {e["phase_id"] for e in await _events(client) if e["phase_id"] is not None}
    assert linked and linked <= live_ids


async def test_backfill_of_an_unknown_session_raises(session_factory: async_sessionmaker) -> None:
    async with session_factory() as db:
        with pytest.raises(CodingSessionNotFoundError):
            await service.backfill_session(db, "nope")


# ---------------------------------------------------- v1.12 compatibility ---


async def test_a_v112_body_still_works_and_defaults_the_new_fields(client: AsyncClient) -> None:
    """The hook script in the wild sends none of this. It must not notice."""
    await _ingest(
        client,
        session_id="s1",
        event_type="PostToolUse",
        cwd="/tmp/x",
        model="opus",
        tool_name="Bash",
        payload={"tool_input": {"command": "ls"}},
        stats={"turns": 1},
    )

    session = await _session(client)
    assert session["event_count"] == 1
    assert session["tool_call_count"] == 1
    # No prompt, so v1.14 names it for where it ran rather than leaving it blank.
    assert (session["title"], session["title_source"]) == ("x", "cwd")
    assert session["workflow"] is None
    assert session["status"] == "running"
    assert session["cost_usd"] is None
    assert session["phases"] == []
    assert [a["name"] for a in session["agents"]] == ["main"]

    event = (await _events(client))[0]
    assert (event["phase_id"], event["ok"], event["duration_ms"], event["ended_at"]) == (
        None,
        None,
        None,
        None,
    )
    assert event["agent"] == "main"
