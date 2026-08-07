"""Proposal accept/reject: backend applies changes with path validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from httpx import AsyncClient

from app.api.deps import get_claude_runner, get_providers
from app.main import app
from tests.helpers import FakeRunner, providers_for


def _reply(summary: str, changes: list[dict[str, Any]]) -> str:
    return f"ok\n\n```proposal\n{json.dumps({'summary': summary, 'changes': changes})}\n```"


async def _seed_proposal(
    client: AsyncClient, tree: tuple[Path, Path], summary: str, changes: list[dict[str, Any]]
) -> str:
    app.dependency_overrides[get_claude_runner] = lambda: FakeRunner(
        reply=_reply(summary, changes), session_id="cli"
    )
    app.dependency_overrides[get_providers] = lambda: providers_for(tree)

    r = await client.post("/api/v1/chat/sessions", json={})
    sid = r.json()["id"]
    r = await client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "please edit"})
    return r.json()["assistant_message"]["proposal"]["id"]


async def test_accept_applies_update(client: AsyncClient, claude_tree: tuple[Path, Path]) -> None:
    skills_root = claude_tree[0]
    target = skills_root / "frontend-dev" / "SKILL.md"
    pid = await _seed_proposal(
        client,
        claude_tree,
        "rewrite",
        [
            {
                "path": str(target),
                "action": "update",
                "new_content": "TOTALLY NEW",
                "description": "rewrite",
            }
        ],
    )

    r = await client.post(f"/api/v1/proposals/{pid}/accept")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "applied"
    assert body["error"] is None
    assert target.read_text(encoding="utf-8") == "TOTALLY NEW"


async def test_accept_creates_file(client: AsyncClient, claude_tree: tuple[Path, Path]) -> None:
    skills_root = claude_tree[0]
    target = skills_root / "brand-new" / "SKILL.md"
    pid = await _seed_proposal(
        client,
        claude_tree,
        "add skill",
        [
            {
                "path": str(target),
                "action": "create",
                "new_content": "---\nname: brand-new\n---\nbody",
                "description": "new skill",
            }
        ],
    )

    r = await client.post(f"/api/v1/proposals/{pid}/accept")
    assert r.status_code == 200
    assert r.json()["status"] == "applied"
    assert target.exists()
    assert target.read_text(encoding="utf-8").endswith("body")


async def test_accept_deletes_file(client: AsyncClient, claude_tree: tuple[Path, Path]) -> None:
    skills_root = claude_tree[0]
    target = skills_root / "backend-dev" / "SKILL.md"
    pid = await _seed_proposal(
        client,
        claude_tree,
        "remove",
        [
            {
                "path": str(target),
                "action": "delete",
                "new_content": None,
                "description": "drop it",
            }
        ],
    )

    r = await client.post(f"/api/v1/proposals/{pid}/accept")
    assert r.status_code == 200
    assert r.json()["status"] == "applied"
    assert not target.exists()


async def test_accept_rejects_path_outside_roots(
    client: AsyncClient, claude_tree: tuple[Path, Path], tmp_path: Path
) -> None:
    outside = claude_tree[1].parent / "escape.md"  # tmp_path, outside both roots
    pid = await _seed_proposal(
        client,
        claude_tree,
        "escape",
        [
            {
                "path": str(outside),
                "action": "update",
                "new_content": "pwned",
                "description": "escape",
            }
        ],
    )

    r = await client.post(f"/api/v1/proposals/{pid}/accept")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "failed"
    assert "outside allowed roots" in body["error"]
    assert not outside.exists()


async def test_accept_non_pending_returns_409(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    target = claude_tree[0] / "frontend-dev" / "SKILL.md"
    pid = await _seed_proposal(
        client,
        claude_tree,
        "rewrite",
        [
            {
                "path": str(target),
                "action": "update",
                "new_content": "x",
                "description": "d",
            }
        ],
    )
    first = await client.post(f"/api/v1/proposals/{pid}/accept")
    assert first.json()["status"] == "applied"

    second = await client.post(f"/api/v1/proposals/{pid}/accept")
    assert second.status_code == 409

    rejected = await client.post(f"/api/v1/proposals/{pid}/reject")
    assert rejected.status_code == 409


async def test_failed_accept_can_be_retried(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    target = claude_tree[0] / "frontend-dev" / "SKILL.md"
    pid = await _seed_proposal(
        client,
        claude_tree,
        "rewrite",
        [
            {
                "path": str(target),
                "action": "update",
                "new_content": "RETRIED",
                "description": "d",
            }
        ],
    )

    target.chmod(0o400)  # transient failure: file not writable
    try:
        r = await client.post(f"/api/v1/proposals/{pid}/accept")
        assert r.status_code == 200
        assert r.json()["status"] == "failed"
    finally:
        target.chmod(0o644)

    r = await client.post(f"/api/v1/proposals/{pid}/accept")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "applied"
    assert body["error"] is None
    assert target.read_text(encoding="utf-8") == "RETRIED"


async def test_null_content_update_is_failed_at_creation(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    target = claude_tree[0] / "frontend-dev" / "SKILL.md"
    original = target.read_text(encoding="utf-8")
    app.dependency_overrides[get_claude_runner] = lambda: FakeRunner(
        reply=_reply(
            "rewrite",
            [{"path": str(target), "action": "update", "new_content": None, "description": "d"}],
        ),
        session_id="cli",
    )
    app.dependency_overrides[get_providers] = lambda: providers_for(claude_tree)

    r = await client.post("/api/v1/chat/sessions", json={})
    sid = r.json()["id"]
    r = await client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "please edit"})
    proposal = r.json()["assistant_message"]["proposal"]
    assert proposal["status"] == "failed"
    assert "new_content" in proposal["error"]

    # Retrying is allowed (no 409) but fails again — content never existed.
    r = await client.post(f"/api/v1/proposals/{proposal['id']}/accept")
    assert r.status_code == 200
    assert r.json()["status"] == "failed"
    assert target.read_text(encoding="utf-8") == original


async def test_reject_sets_status(client: AsyncClient, claude_tree: tuple[Path, Path]) -> None:
    target = claude_tree[0] / "frontend-dev" / "SKILL.md"
    original = target.read_text(encoding="utf-8")
    pid = await _seed_proposal(
        client,
        claude_tree,
        "rewrite",
        [
            {
                "path": str(target),
                "action": "update",
                "new_content": "should not be written",
                "description": "d",
            }
        ],
    )

    r = await client.post(f"/api/v1/proposals/{pid}/reject")
    assert r.status_code == 200
    assert r.json()["status"] == "rejected"
    assert target.read_text(encoding="utf-8") == original  # untouched


async def test_accept_unknown_proposal_404(client: AsyncClient) -> None:
    r = await client.post("/api/v1/proposals/00000000-0000-0000-0000-000000000000/accept")
    assert r.status_code == 404
