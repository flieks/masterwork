"""GET/PATCH /api/v1/settings against the real endpoint and test database."""

from __future__ import annotations

from pathlib import Path

from httpx import AsyncClient

from app.api.deps import get_agent_bins
from app.main import app
from app.services.agent_cli import AgentId


async def test_get_settings_returns_an_expanded_absolute_default(client: AsyncClient) -> None:
    r = await client.get("/api/v1/settings")
    assert r.status_code == 200
    assert Path(r.json()["projects_root"]).is_absolute()


async def test_patch_persists_and_a_later_get_reads_it_back(
    client: AsyncClient, tmp_path: Path
) -> None:
    r = await client.patch("/api/v1/settings", json={"projects_root": str(tmp_path)})
    assert r.status_code == 200
    assert r.json()["projects_root"] == str(tmp_path)

    r = await client.get("/api/v1/settings")
    assert r.json()["projects_root"] == str(tmp_path)


async def test_patch_with_an_empty_body_leaves_it_unchanged(
    client: AsyncClient, tmp_path: Path
) -> None:
    await client.patch("/api/v1/settings", json={"projects_root": str(tmp_path)})
    r = await client.patch("/api/v1/settings", json={})
    assert r.status_code == 200
    assert r.json()["projects_root"] == str(tmp_path)


async def test_patch_rejects_a_relative_path(client: AsyncClient) -> None:
    r = await client.patch("/api/v1/settings", json={"projects_root": "relative/path"})
    assert r.status_code == 400


async def test_patch_rejects_a_path_that_does_not_exist(
    client: AsyncClient, tmp_path: Path
) -> None:
    r = await client.patch("/api/v1/settings", json={"projects_root": str(tmp_path / "nope")})
    assert r.status_code == 400


# --- the assistant's agent ----------------------------------------------------

_CLAUDE = "/usr/local/bin/claude"
_CODEX = "/Applications/ChatGPT.app/Contents/Resources/codex"


def _installed(claude: str | None, codex: str | None) -> None:
    app.dependency_overrides[get_agent_bins] = lambda: {
        AgentId.CLAUDE: claude,
        AgentId.CODEX: codex,
    }


async def test_get_lists_both_agents_with_their_install_state(client: AsyncClient) -> None:
    _installed(None, _CODEX)

    body = (await client.get("/api/v1/settings")).json()

    assert body["agents"] == [
        {"id": "claude", "label": "Claude Code", "installed": False, "bin_path": None},
        {"id": "codex", "label": "Codex", "installed": True, "bin_path": _CODEX},
    ]
    # Nothing picked: the first installed agent is the effective one.
    assert body["assistant_agent"] == "codex"


async def test_unset_prefers_claude_when_both_are_installed(client: AsyncClient) -> None:
    _installed(_CLAUDE, _CODEX)
    assert (await client.get("/api/v1/settings")).json()["assistant_agent"] == "claude"


async def test_unset_with_nothing_installed_is_claude(client: AsyncClient) -> None:
    _installed(None, None)
    assert (await client.get("/api/v1/settings")).json()["assistant_agent"] == "claude"


async def test_picking_an_installed_agent_persists(client: AsyncClient) -> None:
    _installed(_CLAUDE, _CODEX)

    r = await client.patch("/api/v1/settings", json={"assistant_agent": "codex"})
    assert r.status_code == 200
    assert r.json()["assistant_agent"] == "codex"
    assert (await client.get("/api/v1/settings")).json()["assistant_agent"] == "codex"

    # A projects_root-only patch leaves the pick alone.
    await client.patch("/api/v1/settings", json={})
    assert (await client.get("/api/v1/settings")).json()["assistant_agent"] == "codex"


async def test_picking_an_agent_that_is_not_installed_is_refused(
    client: AsyncClient, tmp_path: Path
) -> None:
    _installed(_CLAUDE, None)

    r = await client.patch(
        "/api/v1/settings", json={"assistant_agent": "codex", "projects_root": str(tmp_path)}
    )
    assert r.status_code == 400
    assert "Codex is not installed" in r.json()["detail"]
    # Nothing in a refused patch is written, not even the valid field.
    body = (await client.get("/api/v1/settings")).json()
    assert body["assistant_agent"] == "claude"
    assert body["projects_root"] != str(tmp_path)


async def test_an_unknown_agent_id_is_422(client: AsyncClient) -> None:
    r = await client.patch("/api/v1/settings", json={"assistant_agent": "cursor"})
    assert r.status_code == 422


async def test_a_picked_agent_that_disappears_stays_picked(client: AsyncClient) -> None:
    _installed(_CLAUDE, _CODEX)
    await client.patch("/api/v1/settings", json={"assistant_agent": "codex"})

    _installed(_CLAUDE, None)
    body = (await client.get("/api/v1/settings")).json()
    assert body["assistant_agent"] == "codex"
    assert body["agents"][1]["installed"] is False
