"""Asset-scoped chat: session filtering, asset_id, and the asset context prompt."""

from __future__ import annotations

from pathlib import Path

from httpx import AsyncClient

from app.api.deps import get_claude_runner, get_providers
from app.main import app
from tests.helpers import FakeRunner, providers_for

AGENT_ID = "claude:agent:architect"


def _use(runner: FakeRunner, tree: tuple[Path, Path]) -> None:
    app.dependency_overrides[get_claude_runner] = lambda: runner
    app.dependency_overrides[get_providers] = lambda: providers_for(tree)


# --- session creation + filtering ------------------------------------------


async def test_create_session_with_asset(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    _use(FakeRunner(reply="ok"), claude_tree)
    r = await client.post("/api/v1/chat/sessions", json={"asset_id": AGENT_ID})
    assert r.status_code == 201
    assert r.json()["asset_id"] == AGENT_ID


async def test_create_session_unknown_asset_404(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    _use(FakeRunner(reply="ok"), claude_tree)
    r = await client.post("/api/v1/chat/sessions", json={"asset_id": "claude:agent:nope"})
    assert r.status_code == 404


async def test_asset_sessions_are_not_in_the_global_list(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    _use(FakeRunner(reply="ok"), claude_tree)
    asset_session = (
        await client.post("/api/v1/chat/sessions", json={"asset_id": AGENT_ID})
    ).json()["id"]
    global_session = (await client.post("/api/v1/chat/sessions", json={})).json()["id"]

    asset_ids = {
        s["id"]
        for s in (await client.get("/api/v1/chat/sessions", params={"asset_id": AGENT_ID})).json()
    }
    assert asset_ids == {asset_session}

    global_ids = {
        s["id"]
        for s in (await client.get("/api/v1/chat/sessions", params={"project_id": "none"})).json()
    }
    assert global_ids == {global_session}


# --- asset-scoped message exchange -----------------------------------------


async def test_first_message_carries_asset_context(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    runner = FakeRunner(reply="It plans features.", session_id="cli-a1")
    _use(runner, claude_tree)
    sid = (await client.post("/api/v1/chat/sessions", json={"asset_id": AGENT_ID})).json()["id"]

    r = await client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "what is it?"})
    assert r.status_code == 200

    system_prompt = runner.calls[0]["system_prompt"]
    assert system_prompt is not None
    assert "This chat is scoped to ONE asset" in system_prompt
    assert AGENT_ID in system_prompt
    assert "Design first." in system_prompt  # the file content itself
    assert runner.calls[0]["prompt"].startswith(f"[current asset: id={AGENT_ID};")


async def test_followup_resumes_without_system_prompt(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    runner = FakeRunner(reply="ack", session_id="cli-a2")
    _use(runner, claude_tree)
    sid = (await client.post("/api/v1/chat/sessions", json={"asset_id": AGENT_ID})).json()["id"]

    await client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "first"})
    await client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "second"})

    assert runner.calls[1]["system_prompt"] is None
    assert runner.calls[1]["resume"] == "cli-a2"
    # The state line is re-sent every turn, since --resume reuses the old system prompt.
    assert runner.calls[1]["prompt"].startswith(f"[current asset: id={AGENT_ID};")


async def test_deleted_asset_falls_back_to_an_unscoped_exchange(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    runner = FakeRunner(reply="still here", session_id="cli-a3")
    _use(runner, claude_tree)
    sid = (await client.post("/api/v1/chat/sessions", json={"asset_id": AGENT_ID})).json()["id"]

    (claude_tree[1] / "architect.md").unlink()

    r = await client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "hello"})
    assert r.status_code == 200
    assert r.json()["assistant_message"]["content"] == "still here"
    assert runner.calls[0]["prompt"] == "hello"
