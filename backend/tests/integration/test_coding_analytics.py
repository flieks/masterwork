"""v1.20: the cross-run aggregates, and a parent run's child attribution.

Seeded through the real hook ingest against the real test database, like the
rest of the coding suite — the numbers these endpoints report are only worth
anything if they came out of the same path production data comes out of.

The seed is two pipeline runs of a known shape, described in `_seed_two_runs`.
Every expectation below is derivable from it by hand, on purpose.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.v1.coding import service
from app.db.models.coding import CodingEnvelope, CodingGateCheck, CodingPhase, CodingSession

REPO = "/repo"
SKILLS = "/Users/me/.claude/skills"
CHANGED_FILES_NOTE = "claimed but not changed on disk: README.md"
NO_FENCE_NOTE = "no fenced code block found in the reply"


async def _ingest(client: AsyncClient, **body: Any) -> None:
    r = await client.post("/api/v1/hooks/events", json=body)
    assert r.status_code == 204, r.text


async def _get(client: AsyncClient, path: str, **params: Any) -> list[dict[str, Any]]:
    r = await client.get(f"/api/v1/coding-analytics/{path}", params=params)
    assert r.status_code == 200, r.text
    return list(r.json())


async def _stage(
    client: AsyncClient,
    session_id: str,
    *,
    seq: int,
    role: str,
    status: str = "passed",
    duration_ms: int | None = 1_000,
    cost: float | None = 0.01,
    corrections: int = 0,
    model: str | None = None,
    cwd: str = REPO,
) -> None:
    """One finished stage, stated on the hook body the way the runner states it."""
    await _ingest(
        client,
        session_id=session_id,
        event_type="phase_end",
        cwd=cwd,
        workflow="factory",
        phase={
            "name": role,
            "seq": seq,
            "kind": "agent",
            "agent": role,
            "status": status,
            "duration_ms": duration_ms,
            "cost_usd": cost,
            "corrections": corrections,
        },
        agent={"name": role, "model": model, "cost_usd": cost},
    )


async def _gate(
    client: AsyncClient,
    session_id: str,
    *,
    seq: int,
    role: str,
    gate: str,
    ok: bool,
    note: str | None = None,
    cwd: str = REPO,
) -> None:
    await _ingest(
        client,
        session_id=session_id,
        event_type="gate_pass" if ok else "gate_fail",
        cwd=cwd,
        workflow="factory",
        phase={"name": role, "seq": seq, "agent": role},
        gate={"name": gate, "note": note},
    )


async def _envelope(
    client: AsyncClient,
    session_id: str,
    *,
    seq: int,
    role: str,
    parsed: bool = True,
    parse_error: str | None = None,
) -> None:
    await _ingest(
        client,
        session_id=session_id,
        event_type="agent_reply",
        cwd=REPO,
        workflow="factory",
        phase={"name": role, "seq": seq, "agent": role},
        envelope={
            "role": role,
            "parsed": parsed,
            "parse_error": parse_error,
            "status": "ok" if parsed else None,
            "body": {"status": "ok"} if parsed else None,
        },
    )


async def _finish(client: AsyncClient, session_id: str, *, status: str) -> None:
    await _ingest(
        client,
        session_id=session_id,
        event_type="run_end",
        cwd=REPO,
        workflow="factory",
        status=status,
        ended=True,
    )


async def _seed_two_runs(client: AsyncClient) -> None:
    """Two pipeline runs, by hand:

    run-a (haiku, success)
      plan    passed  1 000 ms  $0.01  0 corrections  gates: envelope ok, changed_files ok
      review  passed  2 000 ms  $0.03  1 correction   gates: envelope ok, changed_files FAIL
      checks  passed  no duration, no cost, no gates
    run-b (opus, failed)
      plan    failed  3 000 ms  $0.05  2 corrections  gates: envelope FAIL, envelope unparsed
      review  passed  4 000 ms  $0.07  0 corrections  gates: changed_files FAIL (same note)
    """
    await _stage(client, "run-a", seq=1, role="plan", cost=0.01, duration_ms=1_000, model="haiku")
    await _gate(client, "run-a", seq=1, role="plan", gate="envelope", ok=True, note="parsed")
    await _gate(client, "run-a", seq=1, role="plan", gate="changed_files", ok=True, note="2 files")
    await _envelope(client, "run-a", seq=1, role="plan")
    await _stage(
        client,
        "run-a",
        seq=2,
        role="review",
        cost=0.03,
        duration_ms=2_000,
        corrections=1,
        model="haiku",
    )
    await _gate(client, "run-a", seq=2, role="review", gate="envelope", ok=True, note="parsed")
    await _gate(
        client,
        "run-a",
        seq=2,
        role="review",
        gate="changed_files",
        ok=False,
        note=CHANGED_FILES_NOTE,
    )
    await _envelope(client, "run-a", seq=2, role="review")
    # A code stage: no model, no cost, no duration, no gate. Its averages must
    # come back null rather than 0.0.
    await _stage(client, "run-a", seq=3, role="checks", cost=None, duration_ms=None)
    await _finish(client, "run-a", status="success")

    await _stage(
        client,
        "run-b",
        seq=1,
        role="plan",
        status="failed",
        cost=0.05,
        duration_ms=3_000,
        corrections=2,
        model="opus",
    )
    await _gate(client, "run-b", seq=1, role="plan", gate="envelope", ok=False, note=NO_FENCE_NOTE)
    await _envelope(client, "run-b", seq=1, role="plan", parsed=False, parse_error=NO_FENCE_NOTE)
    await _stage(client, "run-b", seq=2, role="review", cost=0.07, duration_ms=4_000, model="opus")
    await _gate(
        client,
        "run-b",
        seq=2,
        role="review",
        gate="changed_files",
        ok=False,
        note=CHANGED_FILES_NOTE,
    )
    await _envelope(client, "run-b", seq=2, role="review")
    await _finish(client, "run-b", status="failed")


# ------------------------------------------------------------------ gates ---


async def test_gates_report_checks_failures_and_the_runs_they_ran_in(
    client: AsyncClient,
) -> None:
    await _seed_two_runs(client)
    rows = await _get(client, "gates")

    # Ordered by failures, so the gate worth looking at is first.
    assert [(r["gate"], r["checks"], r["failures"], r["runs"]) for r in rows] == [
        ("changed_files", 3, 2, 2),
        ("envelope", 3, 1, 2),
    ]
    assert rows[0]["failure_rate"] == 2 / 3
    assert rows[1]["failure_rate"] == 1 / 3


async def test_a_gate_is_broken_down_by_the_role_that_ran_it(client: AsyncClient) -> None:
    """The signal the whole endpoint exists for: `changed_files` never fails on
    plan and always fails on review, which is a defect in one role's prompt."""
    await _seed_two_runs(client)
    (changed_files, _) = await _get(client, "gates")

    assert [
        (r["role"], r["checks"], r["failures"], r["failure_rate"], r["runs"])
        for r in changed_files["by_role"]
    ] == [("review", 2, 2, 1.0, 2), ("plan", 1, 0, 0.0, 1)]


async def test_the_failure_notes_come_back_verbatim_and_counted(client: AsyncClient) -> None:
    await _seed_two_runs(client)
    (changed_files, envelope) = await _get(client, "gates")

    assert [
        (n["note"], n["role"], n["occurrences"]) for n in changed_files["top_failure_notes"]
    ] == [(CHANGED_FILES_NOTE, "review", 2)]
    assert [(n["note"], n["occurrences"]) for n in envelope["top_failure_notes"]] == [
        (NO_FENCE_NOTE, 1)
    ]


async def test_a_gate_that_only_passed_reports_no_notes(client: AsyncClient) -> None:
    """A passing check writes a note too; the list is about failures only."""
    await _stage(client, "run-a", seq=1, role="plan")
    await _gate(client, "run-a", seq=1, role="plan", gate="boundary", ok=True, note="in bounds")

    (boundary,) = await _get(client, "gates")
    assert boundary["failures"] == 0
    assert boundary["failure_rate"] == 0.0
    assert boundary["top_failure_notes"] == []


# ------------------------------------------------------------------ roles ---


def _role(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return next(r for r in rows if r["role"] == name)


async def test_roles_are_ranked_by_what_they_cost_in_corrections(client: AsyncClient) -> None:
    await _seed_two_runs(client)
    rows = await _get(client, "roles")

    assert [(r["role"], r["corrections"], r["stages"]) for r in rows] == [
        ("plan", 2, 2),
        ("review", 1, 2),
        ("checks", 0, 1),
    ]
    assert _role(rows, "plan")["avg_corrections"] == 1.0
    assert _role(rows, "review")["avg_corrections"] == 0.5


async def test_a_role_reports_its_duration_cost_and_gate_failures(client: AsyncClient) -> None:
    await _seed_two_runs(client)
    review = _role(await _get(client, "roles"), "review")

    assert (review["runs"], review["stages"], review["failed_stages"]) == (2, 2, 0)
    assert (review["timed_stages"], review["total_duration_ms"], review["avg_duration_ms"]) == (
        2,
        6_000,
        3_000.0,
    )
    assert (review["costed_stages"], round(review["total_cost_usd"], 4)) == (2, 0.10)
    assert round(review["avg_cost_usd"], 4) == 0.05
    # One pass (envelope, run-a) and two failures (changed_files, both runs).
    assert (review["gate_checks"], review["gate_failures"], review["gate_failure_rate"]) == (
        3,
        2,
        2 / 3,
    )


async def test_a_role_that_cannot_emit_valid_json_shows_up_as_a_parse_rate(
    client: AsyncClient,
) -> None:
    await _seed_two_runs(client)
    rows = await _get(client, "roles")

    plan = _role(rows, "plan")
    assert (plan["envelope_attempts"], plan["envelope_failures"]) == (2, 1)
    assert plan["envelope_failure_rate"] == 0.5
    review = _role(rows, "review")
    assert (review["envelope_attempts"], review["envelope_failures"]) == (2, 0)
    assert review["envelope_failure_rate"] == 0.0
    assert _role(rows, "plan")["stage_failure_rate"] == 0.5


async def test_a_stage_that_reported_nothing_averages_to_null_not_zero(
    client: AsyncClient,
) -> None:
    """`checks` runs no agent: it reports no cost, runs no gate and returns no
    envelope. A rate over nothing is unknown, and 0.0 would read as "never
    fails" — three different claims that must not be made here."""
    await _seed_two_runs(client)
    checks = _role(await _get(client, "roles"), "checks")

    assert (checks["stages"], checks["costed_stages"]) == (1, 0)
    assert (checks["total_cost_usd"], checks["avg_cost_usd"]) == (0.0, None)
    assert (checks["gate_checks"], checks["gate_failures"], checks["gate_failure_rate"]) == (
        0,
        0,
        None,
    )
    assert (checks["envelope_attempts"], checks["envelope_failure_rate"]) == (0, None)
    # The denominators it does have still yield real averages. A stage closed
    # with no reported duration is measured at 0 ms, which is a timed stage.
    assert (checks["timed_stages"], checks["avg_duration_ms"]) == (1, 0.0)
    assert checks["avg_corrections"] == 0.0


# ------------------------------------------------------------------- runs ---


async def test_runs_come_back_oldest_first_with_their_outcome(client: AsyncClient) -> None:
    await _seed_two_runs(client)
    rows = await _get(client, "runs")

    assert [(r["session_id"], r["status"], r["accepted"]) for r in rows] == [
        ("run-a", "success", True),
        ("run-b", "failed", False),
    ]


async def test_a_run_carries_the_series_a_client_plots(client: AsyncClient) -> None:
    await _seed_two_runs(client)
    (run_a, run_b) = await _get(client, "runs")

    assert (run_a["stages"], run_a["corrections"]) == (3, 1)
    assert (run_a["gates_passed"], run_a["gates_failed"]) == (3, 1)
    assert (run_a["gate_checks"], run_a["gate_failures"]) == (4, 1)
    assert (run_a["envelope_attempts"], run_a["envelope_failures"]) == (2, 0)
    # A pipeline run measures its own stages, so active_ms is their sum.
    assert run_a["active_ms"] == 3_000
    assert run_b["active_ms"] == 7_000
    assert (run_b["corrections"], run_b["envelope_failures"]) == (2, 1)


async def test_the_run_list_returns_the_most_recent_ones(client: AsyncClient) -> None:
    await _seed_two_runs(client)
    assert [r["session_id"] for r in await _get(client, "runs", limit=1)] == ["run-b"]


# ----------------------------------------------------------------- models ---


async def test_models_are_compared_on_cost_corrections_and_acceptance(
    client: AsyncClient,
) -> None:
    await _seed_two_runs(client)
    rows = await _get(client, "models")

    by_model = {r["model"]: r for r in rows}
    haiku, opus = by_model["haiku"], by_model["opus"]
    assert (haiku["runs"], haiku["accepted_runs"], haiku["acceptance_rate"]) == (1, 1, 1.0)
    assert (opus["runs"], opus["accepted_runs"], opus["acceptance_rate"]) == (1, 0, 0.0)
    assert (haiku["stages"], haiku["corrections"], haiku["avg_corrections"]) == (2, 1, 0.5)
    assert (opus["stages"], opus["corrections"], opus["avg_corrections"]) == (2, 2, 1.0)
    assert round(haiku["cost_usd"], 4) == 0.04
    assert round(opus["cost_usd"], 4) == 0.12


async def test_the_lanes_that_never_reported_a_model_are_their_own_row(
    client: AsyncClient,
) -> None:
    """Every run recorded before the runner sent a model is in here. Dropping
    the row would hide them; labelling it null says what is actually known."""
    await _seed_two_runs(client)
    rows = await _get(client, "models")

    unnamed = next(r for r in rows if r["model"] is None)
    assert (unnamed["lanes"], unnamed["stages"]) == (1, 1)
    # The unnamed row sorts last.
    assert [r["model"] for r in rows] == ["haiku", "opus", None]


# ------------------------------------------------------- the shared filters ---


async def test_every_aggregate_is_empty_before_anything_ran(client: AsyncClient) -> None:
    for path in ("gates", "roles", "runs", "models"):
        assert await _get(client, path) == []


async def _age_run(factory: async_sessionmaker, session_id: str, *, days: float) -> None:
    """Push one whole run that far into the past — the run, its stages and its
    evidence, since each aggregate keys off its own clock.

    Rewritten directly, like the ageing helpers in the assets suite: the window
    keys off wall-clock distance and no amount of ingesting produces yesterday.
    """
    moment = datetime.now(tz=UTC) - timedelta(days=days)
    async with factory() as db:
        await db.execute(
            update(CodingSession).where(CodingSession.id == session_id).values(started_at=moment)
        )
        await db.execute(
            update(CodingPhase)
            .where(CodingPhase.session_id == session_id)
            .values(started_at=moment)
        )
        await db.execute(
            update(CodingGateCheck)
            .where(CodingGateCheck.session_id == session_id)
            .values(created_at=moment)
        )
        await db.execute(
            update(CodingEnvelope)
            .where(CodingEnvelope.session_id == session_id)
            .values(created_at=moment)
        )
        await db.commit()


def _a_day_ago() -> str:
    return (datetime.now(tz=UTC) - timedelta(days=1)).isoformat()


async def test_the_window_counts_only_what_happened_inside_it(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """v1.17's rule, applied to all four: a window reports what happened in it,
    never the whole history of anything that touched it."""
    await _seed_two_runs(client)
    await _age_run(session_factory, "run-a", days=2)
    since = _a_day_ago()

    gates = await _get(client, "gates", since=since)
    assert [(g["gate"], g["checks"], g["failures"]) for g in gates] == [
        ("changed_files", 1, 1),
        ("envelope", 1, 1),
    ]
    roles = await _get(client, "roles", since=since)
    assert [(r["role"], r["stages"], r["corrections"]) for r in roles] == [
        ("plan", 1, 2),
        ("review", 1, 0),
    ]
    assert _role(roles, "plan")["envelope_attempts"] == 1
    assert [r["session_id"] for r in await _get(client, "runs", since=since)] == ["run-b"]
    assert [m["model"] for m in await _get(client, "models", since=since)] == ["opus"]


async def test_the_window_can_be_empty_without_dividing_by_zero(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    await _seed_two_runs(client)
    await _age_run(session_factory, "run-a", days=2)
    await _age_run(session_factory, "run-b", days=2)

    for path in ("gates", "roles", "runs", "models"):
        assert await _get(client, path, since=_a_day_ago()) == []


async def test_the_workflow_filter_narrows_every_aggregate(client: AsyncClient) -> None:
    await _seed_two_runs(client)
    await _ingest(
        client,
        session_id="chat-1",
        event_type="UserPromptSubmit",
        cwd=REPO,
        payload={"prompt": "do a thing"},
    )

    assert [r["session_id"] for r in await _get(client, "runs", workflow="chat")] == ["chat-1"]
    assert [r["role"] for r in await _get(client, "roles", workflow="chat")] == ["main"]
    assert await _get(client, "gates", workflow="chat") == []
    assert {r["session_id"] for r in await _get(client, "runs", workflow="factory")} == {
        "run-a",
        "run-b",
    }


async def test_masterworks_own_inspection_runs_are_left_out_by_default(
    client: AsyncClient,
) -> None:
    """Getting this wrong makes every number a lie: 14 of the first 22 recorded
    skill uses were masterwork inspecting assets, not agents using them."""
    await _stage(client, "run-a", seq=1, role="plan", corrections=1)
    await _gate(client, "run-a", seq=1, role="plan", gate="envelope", ok=True, note="parsed")
    await _stage(client, "inspect", seq=1, role="plan", corrections=9, cwd=service.INSPECTION_CWD)
    await _gate(
        client,
        "inspect",
        seq=1,
        role="plan",
        gate="envelope",
        ok=False,
        note="inspection",
        cwd=service.INSPECTION_CWD,
    )

    assert [(r["role"], r["corrections"]) for r in await _get(client, "roles")] == [("plan", 1)]
    assert [(g["gate"], g["failures"]) for g in await _get(client, "gates")] == [("envelope", 0)]
    assert [r["session_id"] for r in await _get(client, "runs")] == ["run-a"]

    with_inspection = await _get(client, "roles", include_inspection="true")
    assert [(r["role"], r["corrections"]) for r in with_inspection] == [("plan", 10)]
    assert [
        (g["gate"], g["failures"]) for g in await _get(client, "gates", include_inspection="true")
    ] == [("envelope", 1)]


# ------------------------------------------- a run's children, rolled up ---


async def _child_of(client: AsyncClient, parent: str, child: str) -> None:
    """A headless stage child, linked by the provenance chain the hook records."""
    await _ingest(
        client,
        session_id=child,
        event_type="SessionStart",
        cwd=REPO,
        payload={
            "launched_by": [
                "1 /Users/me/.local/bin/claude -p …",
                f"2 /usr/bin/python factory/run.py --repo {REPO} the request",
            ]
        },
    )
    session = await client.get(f"/api/v1/coding-sessions/{child}")
    assert session.json()["parent_session_id"] == parent


async def _read_skill(client: AsyncClient, session_id: str, name: str) -> None:
    await _ingest(
        client,
        session_id=session_id,
        event_type="PostToolUse",
        tool_name="Read",
        payload={"tool_input": {"file_path": f"{SKILLS}/{name}/SKILL.md"}},
    )


async def _detail(client: AsyncClient, session_id: str) -> dict[str, Any]:
    r = await client.get(f"/api/v1/coding-sessions/{session_id}")
    assert r.status_code == 200, r.text
    return dict(r.json())


async def test_a_parent_runs_assets_include_what_its_children_used(
    client: AsyncClient,
) -> None:
    """The pipeline's parent process issues no tool calls at all — every skill
    is reached for inside a headless stage child — so without the roll-up a
    factory run's `assets` is empty however many skills the pipeline used."""
    await _ingest(
        client,
        session_id="factory-abc",
        event_type="phase_start",
        cwd=REPO,
        payload={"event": "phase_start", "phase": "build", "agent": "build"},
    )
    await _child_of(client, "factory-abc", "kid")
    await _read_skill(client, "kid", "tdd")
    await _read_skill(client, "kid", "tdd")

    assert (await _detail(client, "factory-abc"))["assets"] == [
        {
            "kind": "skill",
            "name": "tdd",
            "asset_id": "claude:skill:tdd",
            "lane": None,
            "uses": 2,
            "via_children": 2,
        }
    ]


async def test_two_children_using_one_skill_collapse_into_one_row(
    client: AsyncClient,
) -> None:
    await _ingest(
        client,
        session_id="factory-abc",
        event_type="phase_start",
        cwd=REPO,
        payload={"event": "phase_start", "phase": "build", "agent": "build"},
    )
    await _child_of(client, "factory-abc", "kid-1")
    await _child_of(client, "factory-abc", "kid-2")
    await _read_skill(client, "kid-1", "tdd")
    await _read_skill(client, "kid-2", "tdd")

    (row,) = (await _detail(client, "factory-abc"))["assets"]
    assert (row["uses"], row["via_children"]) == (2, 2)


async def test_a_runs_own_uses_are_told_apart_from_its_childrens(
    client: AsyncClient,
) -> None:
    await _ingest(
        client,
        session_id="factory-abc",
        event_type="phase_start",
        cwd=REPO,
        payload={"event": "phase_start", "phase": "build", "agent": "build"},
    )
    await _read_skill(client, "factory-abc", "tdd")
    await _child_of(client, "factory-abc", "kid")
    await _read_skill(client, "kid", "tdd")

    rows = (await _detail(client, "factory-abc"))["assets"]
    own = next(r for r in rows if r["lane"] is not None)
    assert (own["lane"], own["uses"], own["via_children"]) == ("main", 1, 0)
    folded = next(r for r in rows if r["lane"] is None)
    assert (folded["uses"], folded["via_children"]) == (1, 1)


async def test_a_run_with_no_children_is_unchanged(client: AsyncClient) -> None:
    await _read_skill(client, "solo", "tdd")
    assert (await _detail(client, "solo"))["assets"] == [
        {
            "kind": "skill",
            "name": "tdd",
            "asset_id": "claude:skill:tdd",
            "lane": "main",
            "uses": 1,
            "via_children": 0,
        }
    ]


async def test_the_rollup_does_not_double_count_in_the_cross_run_view(
    client: AsyncClient,
) -> None:
    """The child is already a run of its own in `/coding-assets`, so folding
    there as well would count one call twice."""
    await _ingest(
        client,
        session_id="factory-abc",
        event_type="phase_start",
        cwd=REPO,
        payload={"event": "phase_start", "phase": "build", "agent": "build"},
    )
    await _child_of(client, "factory-abc", "kid")
    await _read_skill(client, "kid", "tdd")

    r = await client.get("/api/v1/coding-assets")
    assert [(row["name"], row["uses"], row["sessions"]) for row in r.json()] == [("tdd", 1, 1)]


async def test_analytics_leave_out_child_runs_by_default(client: AsyncClient) -> None:
    """A stage child is the inside view of a stage already counted on its
    parent; its own chat turns would show up as a `main` role that did the
    pipeline's work twice."""
    await _ingest(
        client,
        session_id="factory-abc",
        event_type="phase_start",
        cwd=REPO,
        payload={"event": "phase_start", "phase": "build", "agent": "build"},
    )
    await _child_of(client, "factory-abc", "kid")
    await _ingest(
        client, session_id="kid", event_type="UserPromptSubmit", payload={"prompt": "build it"}
    )

    assert [r["role"] for r in await _get(client, "roles")] == ["build"]
    assert [r["session_id"] for r in await _get(client, "runs")] == ["factory-abc"]

    with_children = await _get(client, "roles", include_children="true")
    assert sorted(r["role"] for r in with_children) == ["build", "main"]
    assert sorted(r["session_id"] for r in await _get(client, "runs", include_children="true")) == [
        "factory-abc",
        "kid",
    ]
