"""Chat HTTP endpoints against the real test database + a fake runner."""

from __future__ import annotations

import json
from pathlib import Path

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.deps import get_claude_runner, get_providers
from app.db.models.chat import ChatMessage, Proposal
from app.main import app
from tests.helpers import FakeRunner, providers_for


async def _new_session(client: AsyncClient, title: str | None = None) -> str:
    r = await client.post("/api/v1/chat/sessions", json={"title": title})
    assert r.status_code == 201
    return r.json()["id"]


def _proposal_reply(skills_root: Path) -> str:
    target = str(skills_root / "frontend-dev" / "SKILL.md")
    block = {
        "summary": "tidy the intro",
        "changes": [
            {
                "path": target,
                "action": "update",
                "new_content": "updated content",
                "description": "shorten intro",
            }
        ],
    }
    return f"Here's a suggestion.\n\n```proposal\n{json.dumps(block)}\n```"


def _use(runner: FakeRunner, tree: tuple[Path, Path]) -> None:
    app.dependency_overrides[get_claude_runner] = lambda: runner
    app.dependency_overrides[get_providers] = lambda: providers_for(tree)


# --- session CRUD -----------------------------------------------------------


async def test_create_session_default_title(client: AsyncClient) -> None:
    r = await client.post("/api/v1/chat/sessions", json={})
    assert r.status_code == 201
    assert r.json()["title"] == "New chat"


async def test_create_session_custom_title(client: AsyncClient) -> None:
    r = await client.post("/api/v1/chat/sessions", json={"title": "Refactor ideas"})
    assert r.json()["title"] == "Refactor ideas"


async def test_list_sessions_newest_first(client: AsyncClient) -> None:
    a = await _new_session(client, "A")
    b = await _new_session(client, "B")
    # Bump A so it is the most-recently-updated.
    await client.patch(f"/api/v1/chat/sessions/{a}", json={"title": "A2"})

    r = await client.get("/api/v1/chat/sessions")
    ids = [s["id"] for s in r.json()]
    assert ids[0] == a
    assert set(ids) == {a, b}


async def test_update_session(client: AsyncClient) -> None:
    sid = await _new_session(client)
    r = await client.patch(f"/api/v1/chat/sessions/{sid}", json={"title": "Renamed"})
    assert r.status_code == 200
    assert r.json()["title"] == "Renamed"


async def test_update_unknown_session_404(client: AsyncClient) -> None:
    r = await client.patch(
        "/api/v1/chat/sessions/00000000-0000-0000-0000-000000000000",
        json={"title": "x"},
    )
    assert r.status_code == 404


async def test_delete_session_204(client: AsyncClient) -> None:
    sid = await _new_session(client)
    r = await client.delete(f"/api/v1/chat/sessions/{sid}")
    assert r.status_code == 204


async def test_delete_unknown_session_404(client: AsyncClient) -> None:
    r = await client.delete("/api/v1/chat/sessions/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404


# --- message exchange -------------------------------------------------------


async def test_create_message_retitles_and_strips_proposal(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    _use(FakeRunner(reply=_proposal_reply(claude_tree[0]), session_id="cli-1"), claude_tree)
    sid = await _new_session(client)

    r = await client.post(
        f"/api/v1/chat/sessions/{sid}/messages",
        json={"content": "Please tidy the frontend skill intro"},
    )
    assert r.status_code == 200
    body = r.json()

    assert body["user_message"]["role"] == "user"
    assistant = body["assistant_message"]
    assert assistant["role"] == "assistant"
    assert "```proposal" not in assistant["content"]
    assert assistant["content"] == "Here's a suggestion."

    proposal = assistant["proposal"]
    assert proposal is not None
    assert proposal["status"] == "pending"
    assert proposal["summary"] == "tidy the intro"
    assert proposal["changes"][0]["asset_id"] == "claude:skill:frontend-dev"

    # Session retitled from the first user message.
    sessions = (await client.get("/api/v1/chat/sessions")).json()
    title = next(s["title"] for s in sessions if s["id"] == sid)
    assert title == "Please tidy the frontend skill intro"


async def test_create_message_without_proposal(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    _use(FakeRunner(reply="Just an answer, no edits.", session_id="cli-2"), claude_tree)
    sid = await _new_session(client)

    r = await client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "hello"})
    assert r.json()["assistant_message"]["proposal"] is None


async def test_create_message_error_path(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    _use(FakeRunner(error="claude timed out after 300s"), claude_tree)
    sid = await _new_session(client)

    r = await client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "hi"})
    assert r.status_code == 200
    assistant = r.json()["assistant_message"]
    assert assistant["role"] == "error"
    assert "could not complete" in assistant["content"]


async def test_second_message_resumes_session(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    runner = FakeRunner(reply="ok", session_id="cli-3")
    _use(runner, claude_tree)
    sid = await _new_session(client)

    await client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "first"})
    await client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "second"})

    assert runner.calls[0]["resume"] is None
    assert runner.calls[0]["system_prompt"] is not None
    assert runner.calls[1]["resume"] == "cli-3"
    assert runner.calls[1]["system_prompt"] is None


async def test_list_messages_returns_asc_with_proposal(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    _use(FakeRunner(reply=_proposal_reply(claude_tree[0]), session_id="cli-4"), claude_tree)
    sid = await _new_session(client)
    await client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "edit pls"})

    r = await client.get(f"/api/v1/chat/sessions/{sid}/messages")
    msgs = r.json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["proposal"]["summary"] == "tidy the intro"


async def test_unknown_session_messages_404(client: AsyncClient) -> None:
    r = await client.get("/api/v1/chat/sessions/00000000-0000-0000-0000-000000000000/messages")
    assert r.status_code == 404


async def test_delete_session_cascades_messages_and_proposals(
    client: AsyncClient,
    claude_tree: tuple[Path, Path],
    session_factory: async_sessionmaker,
) -> None:
    _use(FakeRunner(reply=_proposal_reply(claude_tree[0]), session_id="cli-5"), claude_tree)
    sid = await _new_session(client)
    await client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "edit"})

    async with session_factory() as db:
        assert (await db.execute(select(func.count()).select_from(ChatMessage))).scalar() == 2
        assert (await db.execute(select(func.count()).select_from(Proposal))).scalar() == 1

    r = await client.delete(f"/api/v1/chat/sessions/{sid}")
    assert r.status_code == 204

    async with session_factory() as db:
        assert (await db.execute(select(func.count()).select_from(ChatMessage))).scalar() == 0
        assert (await db.execute(select(func.count()).select_from(Proposal))).scalar() == 0
