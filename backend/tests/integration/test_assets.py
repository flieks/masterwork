"""Asset HTTP endpoints against a temp provider tree."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import pytest
import pytest_asyncio
from httpx import AsyncClient

from app.api.deps import get_providers
from app.main import app
from tests.helpers import providers_for


@pytest_asyncio.fixture
async def assets_client(client: AsyncClient, claude_tree: tuple[Path, Path]) -> AsyncClient:
    app.dependency_overrides[get_providers] = lambda: providers_for(claude_tree)
    return client


def _url(asset_id: str) -> str:
    return f"/api/v1/assets/{quote(asset_id, safe='')}"


async def test_list_assets(assets_client: AsyncClient) -> None:
    r = await assets_client.get("/api/v1/assets")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 3
    assert {a["id"] for a in body} == {
        "claude:skill:frontend-dev",
        "claude:skill:backend-dev",
        "claude:agent:architect",
    }


async def test_list_assets_filter_kind(assets_client: AsyncClient) -> None:
    r = await assets_client.get("/api/v1/assets", params={"kind": "agent"})
    assert r.status_code == 200
    body = r.json()
    assert {a["name"] for a in body} == {"architect"}


async def test_list_assets_search_content(assets_client: AsyncClient) -> None:
    r = await assets_client.get("/api/v1/assets", params={"q": "vite"})
    assert r.status_code == 200
    assert {a["name"] for a in r.json()} == {"frontend-dev"}


async def test_get_asset_detail(assets_client: AsyncClient) -> None:
    r = await assets_client.get(_url("claude:skill:frontend-dev"))
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "claude:skill:frontend-dev"
    assert body["kind"] == "skill"
    assert "Use React and Vite." in body["content"]


async def test_get_asset_unknown_404(assets_client: AsyncClient) -> None:
    r = await assets_client.get(_url("claude:skill:does-not-exist"))
    assert r.status_code == 404


async def test_get_asset_malformed_400(assets_client: AsyncClient) -> None:
    r = await assets_client.get(_url("totally-bogus"))
    assert r.status_code == 400


async def test_update_asset(assets_client: AsyncClient, claude_tree: tuple[Path, Path]) -> None:
    new = "---\nname: frontend-dev\ndescription: Rewritten.\n---\n\nFresh body.\n"
    r = await assets_client.put(_url("claude:skill:frontend-dev"), json={"content": new})
    assert r.status_code == 200
    assert r.json()["description"] == "Rewritten."

    on_disk = (claude_tree[0] / "frontend-dev" / "SKILL.md").read_text(encoding="utf-8")
    assert "Fresh body." in on_disk


@pytest.mark.parametrize("kind", ["skill", "agent"])
async def test_kind_filter_is_enum(assets_client: AsyncClient, kind: str) -> None:
    r = await assets_client.get("/api/v1/assets", params={"kind": kind})
    assert r.status_code == 200
