"""v1.14: what a run honestly reports — status, working time, order, and name.

Against the real test database, like the rest of the coding suite. Ageing a
session means rewriting its timestamps directly: the derivations under test all
key off wall-clock distance, and no amount of ingesting can produce that.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.v1.coding import service
from app.db.models.coding import CodingEvent, CodingSession


async def _ingest(client: AsyncClient, **body: Any) -> None:
    r = await client.post("/api/v1/hooks/events", json=body)
    assert r.status_code == 204, r.text


async def _session(client: AsyncClient, session_id: str = "s1") -> dict[str, Any]:
    r = await client.get(f"/api/v1/coding-sessions/{session_id}")
    assert r.status_code == 200, r.text
    return dict(r.json())


async def _list(client: AsyncClient, **params: Any) -> list[dict[str, Any]]:
    r = await client.get("/api/v1/coding-sessions", params=params)
    assert r.status_code == 200, r.text
    return list(r.json())


async def _age_session(factory: async_sessionmaker, session_id: str, *, minutes: float) -> None:
    """Push a session's last event that far into the past."""
    moment = datetime.now(tz=UTC) - timedelta(minutes=minutes)
    async with factory() as db:
        await db.execute(
            update(CodingSession).where(CodingSession.id == session_id).values(last_event_at=moment)
        )
        await db.commit()


async def _stamp_events(factory: async_sessionmaker, session_id: str, offsets: list[float]) -> None:
    """Give the session's events these second-offsets from a fixed start, in id
    order, and move `started_at`/`last_event_at` to match."""
    start = datetime.now(tz=UTC) - timedelta(seconds=max(offsets) + 1)
    async with factory() as db:
        result = await db.execute(
            select(CodingEvent.id)
            .where(CodingEvent.session_id == session_id)
            .order_by(CodingEvent.id)
        )
        event_ids = list(result.scalars().all())
        assert len(event_ids) == len(offsets), f"{len(event_ids)} events, {len(offsets)} offsets"
        for event_id, offset in zip(event_ids, offsets, strict=True):
            await db.execute(
                update(CodingEvent)
                .where(CodingEvent.id == event_id)
                .values(created_at=start + timedelta(seconds=offset))
            )
        await db.execute(
            update(CodingSession)
            .where(CodingSession.id == session_id)
            .values(
                started_at=start + timedelta(seconds=offsets[0]),
                last_event_at=start + timedelta(seconds=offsets[-1]),
            )
        )
        await db.commit()


# --------------------------------------------------------- derived status ---


async def test_a_live_session_is_running(client: AsyncClient) -> None:
    await _ingest(client, session_id="s1", event_type="UserPromptSubmit", payload={"prompt": "hi"})
    assert (await _session(client))["status"] == "running"


async def test_an_open_session_gone_quiet_is_abandoned(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """SessionEnd rides an async hook the dying process outruns, so `running`
    without a recent event means nobody ever closed the run."""
    await _ingest(client, session_id="s1", event_type="UserPromptSubmit", payload={"prompt": "hi"})
    await _age_session(session_factory, "s1", minutes=90)

    assert (await _session(client))["status"] == "abandoned"
    assert [s["status"] for s in await _list(client)] == ["abandoned"]


async def test_a_closed_session_keeps_its_outcome_however_old(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    await _ingest(client, session_id="s1", event_type="UserPromptSubmit", payload={"prompt": "hi"})
    await _ingest(client, session_id="s1", event_type="SessionEnd", ended=True)
    await _age_session(session_factory, "s1", minutes=90)

    assert (await _session(client))["status"] == "success"


async def test_a_reported_failure_is_not_overwritten_by_silence(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """Only the absence of an outcome is filled in from silence."""
    await _ingest(client, session_id="s1", event_type="stage", status="failed")
    await _age_session(session_factory, "s1", minutes=90)

    assert (await _session(client))["status"] == "failed"


async def test_status_filter_matches_the_derived_status(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """The filter has to see what the reader sees, or the list contradicts the
    payloads in it."""
    await _ingest(client, session_id="live", event_type="UserPromptSubmit", payload={"prompt": "a"})
    await _ingest(client, session_id="old", event_type="UserPromptSubmit", payload={"prompt": "b"})
    await _age_session(session_factory, "old", minutes=90)

    assert [s["id"] for s in await _list(client, status="running")] == ["live"]
    assert [s["id"] for s in await _list(client, status="abandoned")] == ["old"]


# ----------------------------------------------------- active vs wall time ---


async def test_active_ms_discards_the_long_gap(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """A closed laptop between two turns is not work: four events 5 s, 3 600 s
    and 10 s apart count as 15 s of work over an hour of wall clock."""
    for _ in range(4):
        await _ingest(client, session_id="s1", event_type="PostToolUse", tool_name="Bash")
    await _stamp_events(session_factory, "s1", [0, 5, 3_605, 3_615])

    session = await _session(client)
    assert session["active_ms"] == 15_000
    assert session["wall_ms"] == 3_615_000
    assert session["duration_seconds"] == 3_615.0


async def test_active_ms_is_zero_for_a_single_event(client: AsyncClient) -> None:
    await _ingest(client, session_id="s1", event_type="SessionStart")
    assert (await _session(client))["active_ms"] == 0


async def test_a_factory_run_prefers_its_measured_stage_durations(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """The runner times its own stages; that beats anything inferred from the
    spacing of its telemetry posts."""
    await _ingest(
        client,
        session_id="s1",
        event_type="phase_end",
        workflow="factory",
        phase={"name": "build", "seq": 1, "status": "passed", "duration_ms": 42_000},
    )
    await _ingest(
        client,
        session_id="s1",
        event_type="phase_end",
        workflow="factory",
        phase={"name": "review", "seq": 2, "status": "passed", "duration_ms": 8_000},
    )
    await _stamp_events(session_factory, "s1", [0, 2])

    session = await _session(client)
    assert session["active_ms"] == 50_000
    assert session["wall_ms"] == 2_000


async def test_a_chat_run_does_not_borrow_the_factory_rule(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    await _ingest(client, session_id="s1", event_type="UserPromptSubmit", payload={"prompt": "hi"})
    await _ingest(client, session_id="s1", event_type="Stop")
    await _stamp_events(session_factory, "s1", [0, 4])

    assert (await _session(client))["active_ms"] == 4_000


# --------------------------------------------------------------- ordering ---


async def test_live_runs_outrank_stale_ones(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """`last_event_at DESC` alone buries a run that is working right now under
    one that spoke a minute later and then died."""
    await _ingest(client, session_id="live", event_type="UserPromptSubmit", payload={"prompt": "a"})
    await _ingest(
        client, session_id="stale", event_type="UserPromptSubmit", payload={"prompt": "b"}
    )
    # `stale` spoke most recently of the two, but not recently enough to be live.
    await _age_session(session_factory, "live", minutes=10)
    await _age_session(session_factory, "stale", minutes=5)
    async with session_factory() as db:
        await db.execute(
            update(CodingSession)
            .where(CodingSession.id == "live")
            .values(last_event_at=datetime.now(tz=UTC))
        )
        await db.commit()

    assert [s["id"] for s in await _list(client)] == ["live", "stale"]


async def test_stale_runs_stay_most_recent_first(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    await _ingest(client, session_id="old", event_type="UserPromptSubmit", payload={"prompt": "a"})
    await _ingest(client, session_id="new", event_type="UserPromptSubmit", payload={"prompt": "b"})
    await _age_session(session_factory, "old", minutes=90)
    await _age_session(session_factory, "new", minutes=30)

    assert [s["id"] for s in await _list(client)] == ["new", "old"]


# ---------------------------------------------- titles and their provenance ---


async def test_a_prompt_still_titles_a_chat_run(client: AsyncClient) -> None:
    await _ingest(
        client, session_id="s1", event_type="UserPromptSubmit", payload={"prompt": "fix the tests"}
    )
    session = await _session(client)
    assert (session["title"], session["title_source"]) == ("fix the tests", "prompt")


async def test_a_factory_run_envelope_titles_the_run(client: AsyncClient) -> None:
    await _ingest(
        client,
        session_id="factory-abc",
        event_type="phase_start",
        payload={"event": "phase_start", "phase": "run", "detail": "Add a titlecase() helper"},
    )
    session = await _session(client, "factory-abc")
    assert (session["title"], session["title_source"]) == ("Add a titlecase() helper", "factory")


async def test_a_prompt_less_headless_run_is_named_for_its_provenance(
    client: AsyncClient,
) -> None:
    await _ingest(
        client,
        session_id="s1",
        event_type="SessionStart",
        cwd="/tmp/some-repo",
        payload={
            "launched_by": [
                "100 /Users/me/.local/bin/claude -p …",
                "99 /bin/zsh -c ./nightly.sh",
            ]
        },
    )
    session = await _session(client)
    assert (session["title"], session["title_source"]) == (
        "headless run · some-repo",
        "provenance",
    )
    assert session["launch_mode"] == "automated"


async def test_a_prompt_less_interactive_run_falls_back_to_where_it_ran(
    client: AsyncClient,
) -> None:
    await _ingest(
        client, session_id="s1", event_type="PostToolUse", tool_name="Bash", cwd="/a/proj"
    )
    session = await _session(client)
    assert (session["title"], session["title_source"]) == ("proj", "cwd")


async def test_a_run_with_no_prompt_and_no_cwd_has_no_title(client: AsyncClient) -> None:
    await _ingest(client, session_id="s1", event_type="SessionStart")
    session = await _session(client)
    assert (session["title"], session["title_source"]) == (None, None)


async def test_the_first_prompt_wins_over_later_ones(client: AsyncClient) -> None:
    await _ingest(client, session_id="s1", event_type="UserPromptSubmit", payload={"prompt": "one"})
    await _ingest(client, session_id="s1", event_type="UserPromptSubmit", payload={"prompt": "two"})
    assert (await _session(client))["title"] == "one"


# ----------------------------------------------------- parents and children ---


async def _factory_run(client: AsyncClient, run_id: str, repo: str, stage: str) -> None:
    """Start a pipeline run and open one stage of it."""
    await _ingest(
        client,
        session_id=run_id,
        event_type="phase_start",
        cwd=repo,
        payload={"event": "phase_start", "phase": "run", "detail": "the request"},
    )
    await _ingest(
        client,
        session_id=run_id,
        event_type="phase_start",
        cwd=repo,
        payload={"event": "phase_start", "phase": stage, "agent": stage},
    )


async def _stage_child(client: AsyncClient, session_id: str, repo: str) -> None:
    """A headless `claude -p` the pipeline runner spawned inside `repo`."""
    await _ingest(
        client,
        session_id=session_id,
        event_type="SessionStart",
        cwd=repo,
        payload={
            # The shape the hook really records: "<pid> <argv>", with the
            # runner reached through its interpreter and its prompt redacted.
            "launched_by": [
                "24196 /Users/me/.local/bin/claude -p …",
                f"21540 /usr/bin/python factory/run.py --repo {repo} the request",
                "5050 /Applications/Claude.app/Contents/MacOS/Claude",
            ]
        },
    )


async def test_a_factory_stage_child_is_titled_and_linked(client: AsyncClient) -> None:
    await _factory_run(client, "factory-abc", "/repo", "build")
    await _stage_child(client, "child-1", "/repo")

    child = await _session(client, "child-1")
    assert (child["title"], child["title_source"]) == ("build stage · factory-abc", "provenance")
    assert child["parent_session_id"] == "factory-abc"
    assert (await _session(client, "factory-abc"))["child_count"] == 1


async def test_the_stage_boilerplate_prompt_does_not_overwrite_the_provenance_title(
    client: AsyncClient,
) -> None:
    """Every stage child is prompted with the same wall of instructions; the
    name that says which stage it is has to survive it."""
    await _factory_run(client, "factory-abc", "/repo", "review")
    await _stage_child(client, "child-1", "/repo")
    await _ingest(
        client,
        session_id="child-1",
        event_type="UserPromptSubmit",
        payload={"prompt": "You are the REVIEW stage of a deterministic, unattended pipeline."},
    )

    assert (await _session(client, "child-1"))["title"] == "review stage · factory-abc"


async def test_a_child_takes_the_stage_that_was_open_when_it_started(
    client: AsyncClient,
) -> None:
    await _factory_run(client, "factory-abc", "/repo", "plan")
    await _stage_child(client, "child-1", "/repo")
    await _ingest(
        client,
        session_id="factory-abc",
        event_type="phase_start",
        cwd="/repo",
        payload={"event": "phase_start", "phase": "build", "agent": "build"},
    )
    await _stage_child(client, "child-2", "/repo")

    assert (await _session(client, "child-1"))["title"] == "plan stage · factory-abc"
    assert (await _session(client, "child-2"))["title"] == "build stage · factory-abc"
    assert (await _session(client, "factory-abc"))["child_count"] == 2


async def test_a_headless_run_in_an_unrelated_repo_is_not_adopted(client: AsyncClient) -> None:
    await _factory_run(client, "factory-abc", "/repo", "build")
    await _stage_child(client, "child-1", "/somewhere-else")

    assert (await _session(client, "child-1"))["parent_session_id"] is None
    assert (await _session(client, "factory-abc"))["child_count"] == 0


async def test_roots_only_hides_the_children(client: AsyncClient) -> None:
    await _factory_run(client, "factory-abc", "/repo", "build")
    await _stage_child(client, "child-1", "/repo")
    await _ingest(client, session_id="child-1", event_type="PostToolUse", tool_name="Bash")

    listed = await _list(client, include_automated=True)
    assert sorted(s["id"] for s in listed) == ["child-1", "factory-abc"]
    roots = await _list(client, include_automated=True, roots_only=True)
    assert [s["id"] for s in roots] == ["factory-abc"]


async def test_the_parent_filter_returns_exactly_that_runs_children(
    client: AsyncClient,
) -> None:
    await _factory_run(client, "factory-abc", "/repo", "build")
    await _factory_run(client, "factory-xyz", "/other", "build")
    await _stage_child(client, "child-1", "/repo")
    await _stage_child(client, "child-2", "/repo")
    await _stage_child(client, "elsewhere", "/other")

    listed = await _list(client, parent_session_id="factory-abc")
    assert sorted(s["id"] for s in listed) == ["child-1", "child-2"]
    assert len(listed) == (await _session(client, "factory-abc"))["child_count"]


async def test_the_parent_filter_answers_child_count_without_include_automated(
    client: AsyncClient,
) -> None:
    """Every stage child is a `claude -p` one-shot with only a SessionStart to
    its name, so both grid-hygiene defaults would hide it — and the endpoint
    would report no children for a card that says two."""
    await _factory_run(client, "factory-abc", "/repo", "build")
    await _stage_child(client, "child-1", "/repo")
    await _stage_child(client, "child-2", "/repo")

    assert [s["id"] for s in await _list(client)] == ["factory-abc"]  # children hidden
    assert len(await _list(client, parent_session_id="factory-abc")) == 2


async def test_the_parent_filter_composes_with_the_other_filters(
    client: AsyncClient,
) -> None:
    await _factory_run(client, "factory-abc", "/repo", "build")
    await _stage_child(client, "child-1", "/repo")
    await _ingest(client, session_id="child-1", event_type="SessionEnd", ended=True)
    await _stage_child(client, "child-2", "/repo")

    finished = await _list(client, parent_session_id="factory-abc", status="success")
    assert [s["id"] for s in finished] == ["child-1"]
    # roots_only is the complement of this filter; asking for both is a
    # contradiction, and answering it with anything but nothing would be a lie.
    assert await _list(client, parent_session_id="factory-abc", roots_only=True) == []


async def test_an_unknown_parent_has_no_children(client: AsyncClient) -> None:
    await _factory_run(client, "factory-abc", "/repo", "build")
    await _stage_child(client, "child-1", "/repo")

    assert await _list(client, parent_session_id="factory-nope") == []


# ------------------------------------------------------------- interrupted ---


async def test_a_producer_reported_interruption_is_kept(client: AsyncClient) -> None:
    """`interrupted` is accepted from whoever knows it aborted; masterwork never
    infers it. Nothing writes it today — the factory calls an aborted run
    `failed` — so this is the contract for a producer that starts to."""
    await _ingest(client, session_id="s1", event_type="run_end", status="interrupted", ended=True)
    assert (await _session(client))["status"] == "interrupted"


async def test_a_reported_interruption_survives_a_rebuild(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """The status arrives on the hook body and only `payload` is stored, so a
    replay cannot re-derive it — without keeping it, a rebuild would quietly
    turn an aborted run into a successful one."""
    await _ingest(client, session_id="s1", event_type="run_end", status="interrupted", ended=True)
    async with session_factory() as db:
        await service.backfill_session(db, "s1")

    assert (await _session(client))["status"] == "interrupted"


async def test_silence_is_abandoned_and_never_interrupted(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """A killed run and a lost SessionEnd hook leave identical evidence, so
    silence gets the word that claims less."""
    await _ingest(client, session_id="s1", event_type="UserPromptSubmit", payload={"prompt": "hi"})
    await _age_session(session_factory, "s1", minutes=10)

    assert (await _session(client))["status"] == "abandoned"
    assert await _list(client, status="interrupted") == []


async def test_the_interrupted_filter_matches_only_reported_ones(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    await _ingest(client, session_id="stopped", event_type="run_end", status="interrupted")
    await _ingest(client, session_id="broke", event_type="run_end", status="failed")
    await _ingest(client, session_id="quiet", event_type="UserPromptSubmit", payload={"p": "hi"})
    await _age_session(session_factory, "quiet", minutes=10)

    assert [s["id"] for s in await _list(client, status="interrupted")] == ["stopped"]
    assert [s["id"] for s in await _list(client, status="abandoned")] == ["quiet"]
