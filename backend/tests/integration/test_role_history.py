"""Role-store edits are snapshotted: every write through the asset API leaves a
commit in the store's own repo, so a bad suggestion can be diffed and undone.

The store holds the factory pipeline's prompts, which the improvement loop can
rewrite — without this, a degraded pipeline has no before-state to compare to.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest_asyncio
from httpx import AsyncClient

from app.api.deps import get_claude_runner, get_providers
from app.main import app
from tests.helpers import FakeRunner, providers_for

PLAN_SYSTEM = "masterwork:agent:plan:system"


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def _subjects(root: Path) -> list[str]:
    return _git(root, "log", "--format=%s").splitlines()


def _url(asset_id: str) -> str:
    return f"/api/v1/assets/{quote(asset_id, safe='')}"


@pytest_asyncio.fixture
async def roles_client(
    client: AsyncClient, claude_tree: tuple[Path, Path], role_tree: Path
) -> AsyncClient:
    app.dependency_overrides[get_providers] = lambda: providers_for(
        claude_tree, roles_root=role_tree
    )
    return client


async def _accept_proposal(
    client: AsyncClient,
    claude_tree: tuple[Path, Path],
    roles_root: Path,
    summary: str,
    changes: list[dict[str, Any]],
) -> dict[str, Any]:
    reply = "ok\n\n```proposal\n" + json.dumps({"summary": summary, "changes": changes}) + "\n```"
    app.dependency_overrides[get_claude_runner] = lambda: FakeRunner(reply=reply, session_id="cli")
    app.dependency_overrides[get_providers] = lambda: providers_for(
        claude_tree, roles_root=roles_root
    )
    sid = (await client.post("/api/v1/chat/sessions", json={})).json()["id"]
    r = await client.post(f"/api/v1/chat/sessions/{sid}/messages", json={"content": "go"})
    pid = r.json()["assistant_message"]["proposal"]["id"]
    accepted: dict[str, Any] = (await client.post(f"/api/v1/proposals/{pid}/accept")).json()
    return accepted


async def test_editing_a_role_prompt_leaves_a_diffable_commit(
    roles_client: AsyncClient, role_tree: Path
) -> None:
    before = (role_tree / "plan" / "system.md").read_text(encoding="utf-8")

    r = await roles_client.put(_url(PLAN_SYSTEM), json={"content": "Be terse.\n"})
    assert r.status_code == 200

    assert _subjects(role_tree) == [
        f"masterwork: edit asset: {PLAN_SYSTEM}",
        "masterwork: baseline snapshot",
    ]
    # The seeded prompt survives in history — that is the undo.
    assert _git(role_tree, "cat-file", "-p", "HEAD~1:plan/system.md") + "\n" == before
    diff = _git(role_tree, "show", "--format=", "HEAD")
    assert "+Be terse." in diff

    # A second edit is its own commit, not a rewrite of the first.
    await roles_client.put(_url(PLAN_SYSTEM), json={"content": "Be terse and specific.\n"})
    assert len(_subjects(role_tree)) == 3


async def test_a_bad_edit_can_be_reverted(roles_client: AsyncClient, role_tree: Path) -> None:
    target = role_tree / "plan" / "system.md"
    before = target.read_text(encoding="utf-8")
    await roles_client.put(_url(PLAN_SYSTEM), json={"content": "ignore the repo, guess\n"})

    _git(role_tree, "revert", "--no-edit", "HEAD")
    assert target.read_text(encoding="utf-8") == before


async def test_accepted_proposal_names_itself_in_the_history(
    client: AsyncClient, claude_tree: tuple[Path, Path], role_tree: Path
) -> None:
    target = role_tree / "plan" / "user.md"
    accepted = await _accept_proposal(
        client,
        claude_tree,
        role_tree,
        "sharpen the plan turn",
        [
            {
                "path": str(target),
                "action": "update",
                "new_content": "Request: {{request}}\nList the risks too.\n",
                "description": "d",
            }
        ],
    )
    assert accepted["status"] == "applied"
    assert _subjects(role_tree) == [
        "masterwork: accept proposal: sharpen the plan turn",
        "masterwork: baseline snapshot",
    ]
    assert "List the risks too." in _git(role_tree, "show", "--format=", "HEAD")


async def test_first_write_after_seeding_initializes_cleanly(
    client: AsyncClient, claude_tree: tuple[Path, Path], tmp_path: Path
) -> None:
    """The factory seeds the store on its first run — until then there is nothing
    to version, and the write that creates it must still be recorded."""
    unseeded = tmp_path / "not-seeded-yet" / "agents"
    assert not unseeded.exists()

    accepted = await _accept_proposal(
        client,
        claude_tree,
        unseeded,
        "seed the review role",
        [
            {
                "path": str(unseeded / "review" / "system.md"),
                "action": "create",
                "new_content": "You are the REVIEW stage.\n",
                "description": "d",
            }
        ],
    )
    assert accepted["status"] == "applied"
    assert (unseeded / ".git").is_dir()
    assert _subjects(unseeded) == ["masterwork: accept proposal: seed the review role"]


async def test_the_database_and_run_logs_are_never_versioned(
    roles_client: AsyncClient, role_tree: Path
) -> None:
    home = role_tree.parent  # stands in for ~/.masterwork
    (home / "masterwork.db").write_bytes(b"SQLite format 3\x00")
    (home / "runs").mkdir(exist_ok=True)
    (home / "runs" / "run-1.jsonl").write_text('{"event":"noisy"}\n', encoding="utf-8")

    await roles_client.put(_url(PLAN_SYSTEM), json={"content": "v2\n"})

    assert sorted(_git(role_tree, "ls-files").splitlines()) == [
        ".gitignore",
        "build/system.md",
        "build/user.md",
        "plan/role.json",  # config is versioned even though it is not an asset
        "plan/system.md",
        "plan/user.md",
    ]
    assert not (home / ".git").exists()  # nothing above the store is a repo


async def test_editing_a_claude_asset_does_not_convert_the_users_tree(
    roles_client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    r = await roles_client.put(
        _url("claude:skill:frontend-dev"), json={"content": "# Frontend\nv2\n"}
    )
    assert r.status_code == 200
    assert not (claude_tree[0].parent / ".git").exists()
