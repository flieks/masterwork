"""PUT /assets/{id}/enabled against temp Claude, Codex, generic and plugin trees."""

from __future__ import annotations

import subprocess
from pathlib import Path
from urllib.parse import quote

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.api.deps import get_providers
from app.main import app
from tests.helpers import providers_for


@pytest.fixture
def trees(tmp_path: Path, claude_tree: tuple[Path, Path]) -> dict[str, Path]:
    generic = tmp_path / "agents" / "skills"
    codex = tmp_path / "codex" / "skills"
    codex.mkdir(parents=True)
    shared = generic / "shared"
    shared.mkdir(parents=True)
    (shared / "SKILL.md").write_text("---\nname: shared\n---\nshared body\n", encoding="utf-8")
    (claude_tree[0] / "shared").symlink_to(shared, target_is_directory=True)
    (codex / "shared").symlink_to(shared, target_is_directory=True)
    return {"claude": claude_tree[0], "generic": generic, "codex": codex}


@pytest_asyncio.fixture
async def toggle_client(
    client: AsyncClient,
    claude_tree: tuple[Path, Path],
    trees: dict[str, Path],
    plugin_tree: Path,
) -> AsyncClient:
    app.dependency_overrides[get_providers] = lambda: providers_for(
        claude_tree,
        plugins_root=plugin_tree,
        generic_root=trees["generic"],
        codex_root=trees["codex"],
    )
    return client


def _url(asset_id: str) -> str:
    return f"/api/v1/assets/{quote(asset_id, safe='')}"


async def _set(client: AsyncClient, asset_id: str, enabled: bool) -> object:
    return await client.put(f"{_url(asset_id)}/enabled", json={"enabled": enabled})


async def test_claude_skill_round_trips(toggle_client: AsyncClient, trees: dict[str, Path]) -> None:
    r = await _set(toggle_client, "claude:skill:frontend-dev", False)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == "claude:skill:frontend-dev"  # the id survives the move
    assert body["disabled"] is True
    assert body["agents"] == []
    assert body["path"] == str(trees["claude"] / ".disabled" / "frontend-dev" / "SKILL.md")
    assert "Use React and Vite." in body["content"]
    assert not (trees["claude"] / "frontend-dev").exists()

    # Still listed, still findable by search, still readable and editable.
    listed = await toggle_client.get("/api/v1/assets", params={"q": "vite"})
    assert [a["id"] for a in listed.json()] == ["claude:skill:frontend-dev"]
    assert listed.json()[0]["disabled"] is True
    detail = await toggle_client.get(_url("claude:skill:frontend-dev"))
    assert detail.status_code == 200 and detail.json()["disabled"] is True
    edited = await toggle_client.put(
        _url("claude:skill:frontend-dev"), json={"content": "---\nname: frontend-dev\n---\nnew\n"}
    )
    assert edited.status_code == 200, edited.text
    parked = trees["claude"] / ".disabled" / "frontend-dev" / "SKILL.md"
    assert parked.read_text().endswith("new\n")

    r = await _set(toggle_client, "claude:skill:frontend-dev", True)
    assert r.status_code == 200, r.text
    assert r.json()["disabled"] is False
    assert r.json()["agents"] == ["claude"]
    assert r.json()["path"] == str(trees["claude"] / "frontend-dev" / "SKILL.md")
    assert not (trees["claude"] / ".disabled").exists()


async def test_generic_skill_moves_every_agent_link_and_back(
    toggle_client: AsyncClient, trees: dict[str, Path]
) -> None:
    generic, claude, codex = trees["generic"], trees["claude"], trees["codex"]
    before = (await toggle_client.get(_url("generic:skill:shared"))).json()
    assert sorted(before["agents"]) == ["claude", "codex"]

    r = await _set(toggle_client, "generic:skill:shared", False)
    assert r.status_code == 200, r.text
    assert r.json()["disabled"] is True
    assert r.json()["agents"] == []
    parked = generic / ".disabled" / "shared"
    assert r.json()["path"] == str(parked / "SKILL.md")
    for root in (claude, codex):
        assert not (root / "shared").exists()
        assert (root / ".disabled" / "shared").is_symlink()
        assert (root / ".disabled" / "shared").resolve() == parked.resolve()
    # The parked links are not listed as skills of their own.
    ids = [a["id"] for a in (await toggle_client.get("/api/v1/assets")).json()]
    assert ids.count("generic:skill:shared") == 1
    assert "claude:skill:shared" not in ids and "codex:skill:shared" not in ids

    r = await _set(toggle_client, "generic:skill:shared", True)
    assert r.status_code == 200, r.text
    assert r.json()["disabled"] is False
    assert sorted(r.json()["agents"]) == ["claude", "codex"]
    for root in (claude, codex):
        assert (root / "shared").is_symlink()
        assert (root / "shared").resolve() == (generic / "shared").resolve()
        assert not (root / ".disabled").exists()


async def test_enable_restores_only_the_links_that_were_parked(
    toggle_client: AsyncClient, trees: dict[str, Path]
) -> None:
    (trees["codex"] / "shared").unlink()  # Codex never linked this one
    assert (await _set(toggle_client, "generic:skill:shared", False)).status_code == 200
    r = await _set(toggle_client, "generic:skill:shared", True)
    assert r.status_code == 200, r.text
    assert r.json()["agents"] == ["claude"]
    assert not (trees["codex"] / "shared").exists()


async def test_enabling_an_enabled_skill_is_a_no_op(toggle_client: AsyncClient) -> None:
    before = (await toggle_client.get(_url("claude:skill:backend-dev"))).json()
    r = await _set(toggle_client, "claude:skill:backend-dev", True)
    assert r.status_code == 200, r.text
    assert r.json() == before
    # And twice off is once off.
    assert (await _set(toggle_client, "claude:skill:backend-dev", False)).status_code == 200
    again = await _set(toggle_client, "claude:skill:backend-dev", False)
    assert again.status_code == 200 and again.json()["disabled"] is True


async def test_refuses_plugin_assets_and_agents(toggle_client: AsyncClient) -> None:
    r = await _set(toggle_client, "claude-plugin:skill:vercel:bootstrap", False)
    assert r.status_code == 409
    assert "cannot be switched off" in r.json()["detail"]

    r = await _set(toggle_client, "claude:agent:architect", False)
    assert r.status_code == 409
    assert "only skills" in r.json()["detail"]


async def test_refuses_when_the_destination_exists(
    toggle_client: AsyncClient, trees: dict[str, Path]
) -> None:
    blocker = trees["claude"] / ".disabled" / "backend-dev"
    blocker.mkdir(parents=True)
    (blocker / "SKILL.md").write_text("---\nname: backend-dev\n---\nolder copy\n", encoding="utf-8")

    r = await _set(toggle_client, "claude:skill:backend-dev", False)
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"]
    assert (trees["claude"] / "backend-dev" / "SKILL.md").is_file()
    assert (blocker / "SKILL.md").read_text().endswith("older copy\n")


async def test_unknown_asset_is_404(toggle_client: AsyncClient) -> None:
    assert (await _set(toggle_client, "claude:skill:nope", False)).status_code == 404


async def test_toggle_is_recorded_where_the_tree_is_a_repo(
    toggle_client: AsyncClient, trees: dict[str, Path]
) -> None:
    home = trees["claude"].parent
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(home), *args], check=True, capture_output=True)

    assert (await _set(toggle_client, "claude:skill:frontend-dev", False)).status_code == 200
    log = subprocess.run(
        ["git", "-C", str(home), "log", "--format=%s"], check=True, capture_output=True, text=True
    ).stdout.splitlines()
    assert log[0] == "masterwork: disable skill: frontend-dev"
    # ~/.agents was never made a repo, and the toggle did not make it one.
    assert not (trees["generic"].parent / ".git").exists()
