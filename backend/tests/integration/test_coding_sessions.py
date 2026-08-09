"""Hook ingest and the Sessions read endpoints, against the real test database."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models.coding import CodingSession


async def _ingest(client: AsyncClient, **body: Any) -> None:
    r = await client.post("/api/v1/hooks/events", json=body)
    assert r.status_code == 204, r.text


async def _go_quiet(factory: async_sessionmaker, session_id: str) -> None:
    """Age a session past IDLE_WINDOW so it counts as finished without a SessionEnd."""
    async with factory() as db:
        await db.execute(
            update(CodingSession)
            .where(CodingSession.id == session_id)
            .values(last_event_at=datetime.now(tz=UTC) - timedelta(minutes=10))
        )
        await db.commit()


async def test_ingest_creates_session_and_event(client: AsyncClient) -> None:
    await _ingest(
        client,
        session_id="s1",
        event_type="SessionStart",
        cwd="/tmp/some-repo",
        model="opus",
        payload={"source": "startup"},
    )

    session = (await client.get("/api/v1/coding-sessions/s1")).json()
    assert session["id"] == "s1"
    assert session["cwd"] == "/tmp/some-repo"
    assert session["model"] == "opus"
    assert session["source"] == "claude-code"
    assert session["ended_at"] is None
    assert session["event_count"] == 1
    assert session["tool_call_count"] == 0

    events = (await client.get("/api/v1/coding-sessions/s1/events")).json()
    assert [e["event_type"] for e in events] == ["SessionStart"]
    assert events[0]["payload"] == {"source": "startup"}
    assert events[0]["session_id"] == "s1"


async def test_unknown_event_type_stored_as_is(client: AsyncClient) -> None:
    await _ingest(client, session_id="s1", event_type="SomeFutureHook")
    events = (await client.get("/api/v1/coding-sessions/s1/events")).json()
    assert events[0]["event_type"] == "SomeFutureHook"


async def test_second_event_upserts_and_bumps_last_event_at(client: AsyncClient) -> None:
    await _ingest(client, session_id="s1", event_type="SessionStart", cwd="/tmp/a")
    first = (await client.get("/api/v1/coding-sessions/s1")).json()

    await _ingest(client, session_id="s1", event_type="PostToolUse", tool_name="Bash")
    second = (await client.get("/api/v1/coding-sessions/s1")).json()

    assert second["started_at"] == first["started_at"]
    assert second["last_event_at"] > first["last_event_at"]
    assert second["cwd"] == "/tmp/a"  # first-seen cwd is kept
    assert second["event_count"] == 2
    assert second["tool_call_count"] == 1
    assert second["duration_seconds"] > 0
    assert len((await client.get("/api/v1/coding-sessions")).json()) == 1


async def test_latest_model_wins(client: AsyncClient) -> None:
    await _ingest(client, session_id="s1", event_type="SessionStart", model="opus")
    await _ingest(client, session_id="s1", event_type="UserPromptSubmit", model="haiku")
    assert (await client.get("/api/v1/coding-sessions/s1")).json()["model"] == "haiku"


async def test_stats_shallow_merge(client: AsyncClient) -> None:
    await _ingest(
        client, session_id="s1", event_type="Stop", stats={"turns": 1, "tokens": {"in": 10}}
    )
    await _ingest(client, session_id="s1", event_type="Stop", stats={"turns": 2, "cost_usd": 0.5})

    stats = (await client.get("/api/v1/coding-sessions/s1")).json()["stats"]
    assert stats == {"turns": 2, "tokens": {"in": 10}, "cost_usd": 0.5}


async def test_ended_sets_ended_at_and_freezes_duration(client: AsyncClient) -> None:
    await _ingest(client, session_id="s1", event_type="SessionStart")
    await _ingest(client, session_id="s1", event_type="SessionEnd", ended=True)

    session = (await client.get("/api/v1/coding-sessions/s1")).json()
    assert session["ended_at"] is not None
    assert session["ended_at"] == session["last_event_at"]


async def test_git_repo_derived_from_cwd(client: AsyncClient, tmp_path: Path) -> None:
    repo = tmp_path / "my-repo"
    (repo / ".git").mkdir(parents=True)
    nested = repo / "backend" / "app"
    nested.mkdir(parents=True)

    await _ingest(client, session_id="s1", event_type="SessionStart", cwd=str(nested))
    assert (await client.get("/api/v1/coding-sessions/s1")).json()["git_repo"] == "my-repo"


async def test_git_repo_null_outside_a_repo(client: AsyncClient, tmp_path: Path) -> None:
    await _ingest(client, session_id="s1", event_type="SessionStart", cwd=str(tmp_path))
    assert (await client.get("/api/v1/coding-sessions/s1")).json()["git_repo"] is None


async def test_oversized_payload_is_truncated(client: AsyncClient) -> None:
    await _ingest(client, session_id="s1", event_type="PostToolUse", payload={"blob": "x" * 40_000})

    payload = (await client.get("/api/v1/coding-sessions/s1/events")).json()[0]["payload"]
    assert payload["_truncated"] is True
    assert payload["_chars"] > 32 * 1024
    assert len(payload["_preview"]) == 2_000


async def test_list_orders_by_last_event_desc_with_counts(client: AsyncClient) -> None:
    await _ingest(client, session_id="old", event_type="SessionStart")
    await _ingest(client, session_id="new", event_type="SessionStart")
    await _ingest(client, session_id="new", event_type="PostToolUse", tool_name="Read")
    await _ingest(client, session_id="old", event_type="PostToolUse", tool_name="Edit")

    sessions = (await client.get("/api/v1/coding-sessions")).json()
    assert [s["id"] for s in sessions] == ["old", "new"]  # "old" got the latest event
    assert all(s["event_count"] == 2 and s["tool_call_count"] == 1 for s in sessions)


async def test_list_limit_and_offset(client: AsyncClient) -> None:
    for i in range(3):
        await _ingest(client, session_id=f"s{i}", event_type="SessionStart")

    page = (await client.get("/api/v1/coding-sessions?limit=2")).json()
    assert [s["id"] for s in page] == ["s2", "s1"]
    rest = (await client.get("/api/v1/coding-sessions?limit=2&offset=2")).json()
    assert [s["id"] for s in rest] == ["s0"]


async def _ingest_empty_session(client: AsyncClient, session_id: str) -> None:
    """A discarded desktop-app startup process: starts, ends, runs nothing."""
    await _ingest(client, session_id=session_id, event_type="SessionStart")
    await _ingest(client, session_id=session_id, event_type="SessionEnd", ended=True)


async def test_list_hides_sessions_that_ended_without_running_a_tool(
    client: AsyncClient,
) -> None:
    await _ingest_empty_session(client, "ghost")
    await _ingest(client, session_id="real", event_type="PostToolUse", tool_name="Read")

    sessions = (await client.get("/api/v1/coding-sessions")).json()
    assert [s["id"] for s in sessions] == ["real"]
    # Hidden from the list, not deleted — the detail route still serves it.
    assert (await client.get("/api/v1/coding-sessions/ghost")).status_code == 200


async def test_include_empty_reveals_them(client: AsyncClient) -> None:
    await _ingest_empty_session(client, "ghost")

    assert (await client.get("/api/v1/coding-sessions")).json() == []
    revealed = (await client.get("/api/v1/coding-sessions?include_empty=true")).json()
    assert [s["id"] for s in revealed] == ["ghost"]


async def test_open_and_tool_running_sessions_are_never_hidden(client: AsyncClient) -> None:
    # Just started, nothing done yet — could still become a real session.
    await _ingest(client, session_id="open", event_type="SessionStart")
    # Ended, but it did work.
    await _ingest(client, session_id="worked", event_type="PostToolUse", tool_name="Bash")
    await _ingest(client, session_id="worked", event_type="SessionEnd", ended=True)

    sessions = (await client.get("/api/v1/coding-sessions")).json()
    assert sorted(s["id"] for s in sessions) == ["open", "worked"]


async def test_ghost_that_never_sent_session_end_is_hidden_once_quiet(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """The async SessionEnd hook dies with the process, so many ghosts stay open."""
    await _ingest(client, session_id="ghost", event_type="SessionStart")
    assert len((await client.get("/api/v1/coding-sessions")).json()) == 1

    await _go_quiet(session_factory, "ghost")
    assert (await client.get("/api/v1/coding-sessions")).json() == []


async def test_a_prompted_session_survives_going_quiet(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """Someone typed at it, so a long silent answer must not make it disappear."""
    await _ingest(client, session_id="asked", event_type="SessionStart")
    await _ingest(
        client, session_id="asked", event_type="UserPromptSubmit", payload={"prompt": "hi"}
    )

    await _go_quiet(session_factory, "asked")
    sessions = (await client.get("/api/v1/coding-sessions")).json()
    assert [s["id"] for s in sessions] == ["asked"]


async def test_other_producers_are_never_mistaken_for_ghosts(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """The factory runner posts its own event names — no prompts, no PostToolUse."""
    await _ingest(client, session_id="factory-run", event_type="phase_start")
    await _ingest(client, session_id="factory-run", event_type="agent_turn")
    await _ingest(client, session_id="factory-run", event_type="run_end", ended=True)

    await _go_quiet(session_factory, "factory-run")
    sessions = (await client.get("/api/v1/coding-sessions")).json()
    assert [s["id"] for s in sessions] == ["factory-run"]


DESKTOP_CHAIN = [
    "42 /Applications/Claude.app/.../MacOS/claude --output-format stream-json --verbose",
    "7 /Applications/Claude.app/Contents/MacOS/Claude",
]
SCRIPT_CHAIN = [
    "62667 /Users/me/.local/bin/claude -p …",
    "61430 /opt/homebrew/.../Python /Users/me/Projects/ideaater/.venv/bin/ideaater run",
]


async def _start(client: AsyncClient, session_id: str, chain: list[str]) -> None:
    await _ingest(
        client,
        session_id=session_id,
        event_type="SessionStart",
        payload={"source": "startup", "launched_by": chain},
    )
    await _ingest(client, session_id=session_id, event_type="PostToolUse", tool_name="Read")


async def test_launch_mode_derived_from_the_launcher_chain(client: AsyncClient) -> None:
    await _start(client, "typed", DESKTOP_CHAIN)
    await _start(client, "scripted", SCRIPT_CHAIN)

    assert (await client.get("/api/v1/coding-sessions/typed")).json()[
        "launch_mode"
    ] == "interactive"
    assert (await client.get("/api/v1/coding-sessions/scripted")).json()[
        "launch_mode"
    ] == "automated"


async def test_launch_mode_is_null_without_a_chain(client: AsyncClient) -> None:
    await _ingest(client, session_id="s1", event_type="SessionStart", payload={"source": "startup"})
    assert (await client.get("/api/v1/coding-sessions/s1")).json()["launch_mode"] is None


async def test_list_hides_automated_sessions_unless_asked(client: AsyncClient) -> None:
    await _start(client, "typed", DESKTOP_CHAIN)
    await _start(client, "scripted", SCRIPT_CHAIN)
    # No chain recorded at all: unclassified must not disappear.
    await _ingest(client, session_id="legacy", event_type="PostToolUse", tool_name="Bash")

    listed = (await client.get("/api/v1/coding-sessions")).json()
    assert sorted(s["id"] for s in listed) == ["legacy", "typed"]

    everything = (await client.get("/api/v1/coding-sessions?include_automated=true")).json()
    assert sorted(s["id"] for s in everything) == ["legacy", "scripted", "typed"]


async def test_events_cursor_pagination(client: AsyncClient) -> None:
    for i in range(5):
        await _ingest(client, session_id="s1", event_type=f"E{i}")

    first = (await client.get("/api/v1/coding-sessions/s1/events?limit=2")).json()
    assert [e["event_type"] for e in first] == ["E0", "E1"]

    cursor = first[-1]["id"]
    rest = (await client.get(f"/api/v1/coding-sessions/s1/events?after={cursor}")).json()
    assert [e["event_type"] for e in rest] == ["E2", "E3", "E4"]
    assert all(e["id"] > cursor for e in rest)

    # Polling past the end returns nothing until a new event lands.
    tail = rest[-1]["id"]
    assert (await client.get(f"/api/v1/coding-sessions/s1/events?after={tail}")).json() == []
    await _ingest(client, session_id="s1", event_type="E5")
    live = (await client.get(f"/api/v1/coding-sessions/s1/events?after={tail}")).json()
    assert [e["event_type"] for e in live] == ["E5"]


async def test_events_of_other_sessions_are_excluded(client: AsyncClient) -> None:
    await _ingest(client, session_id="a", event_type="E-a")
    await _ingest(client, session_id="b", event_type="E-b")

    events = (await client.get("/api/v1/coding-sessions/a/events")).json()
    assert [e["event_type"] for e in events] == ["E-a"]


async def test_unknown_session_404(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/coding-sessions/nope")).status_code == 404
    assert (await client.get("/api/v1/coding-sessions/nope/events")).status_code == 404


async def test_empty_list_when_nothing_ingested(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/coding-sessions")).json() == []


async def test_ingest_requires_session_and_type(client: AsyncClient) -> None:
    assert (
        await client.post("/api/v1/hooks/events", json={"event_type": "Stop"})
    ).status_code == 422
    assert (await client.post("/api/v1/hooks/events", json={"session_id": "s1"})).status_code == 422
