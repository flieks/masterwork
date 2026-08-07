"""Asset diagram GET/POST with a fake one-shot runner (never the real CLI)."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from httpx import AsyncClient

from app.api.deps import get_light_runner, get_providers
from app.main import app
from tests.helpers import FakeRunner, providers_for

_FE = "claude:skill:frontend-dev"
_MERMAID_REPLY = 'Here is the diagram.\n\n```mermaid\nflowchart TD\n  A["start"]-->B["end"]\n```'


def _diagram_url(asset_id: str) -> str:
    return f"/api/v1/assets/{quote(asset_id, safe='')}/diagram"


def _use(tree: tuple[Path, Path], *, reply: str | None = None, error: str | None = None) -> None:
    app.dependency_overrides[get_providers] = lambda: providers_for(tree)
    app.dependency_overrides[get_light_runner] = lambda: FakeRunner(reply=reply, error=error)


async def test_get_before_generation_404(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    _use(claude_tree)
    r = await client.get(_diagram_url(_FE))
    assert r.status_code == 404


async def test_get_unknown_asset_404(client: AsyncClient, claude_tree: tuple[Path, Path]) -> None:
    _use(claude_tree)
    r = await client.get(_diagram_url("claude:skill:nope"))
    assert r.status_code == 404


async def test_generate_then_cache_hit(client: AsyncClient, claude_tree: tuple[Path, Path]) -> None:
    _use(claude_tree, reply=_MERMAID_REPLY)

    r = await client.post(_diagram_url(_FE))
    assert r.status_code == 200
    body = r.json()
    assert body["asset_id"] == _FE
    assert body["mermaid"] == 'flowchart TD\n  A["start"]-->B["end"]'  # fences stripped
    assert body["stale"] is False

    # GET returns the cached row, still fresh.
    r = await client.get(_diagram_url(_FE))
    assert r.status_code == 200
    assert r.json()["mermaid"] == 'flowchart TD\n  A["start"]-->B["end"]'
    assert r.json()["stale"] is False


async def test_stale_flag_flips_when_file_changes(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    _use(claude_tree, reply=_MERMAID_REPLY)
    await client.post(_diagram_url(_FE))

    # Mutate the asset file after generation → the stored hash no longer matches.
    (claude_tree[0] / "frontend-dev" / "SKILL.md").write_text(
        "---\nname: frontend-dev\ndescription: changed.\n---\n\nDifferent body.\n",
        encoding="utf-8",
    )

    r = await client.get(_diagram_url(_FE))
    assert r.status_code == 200
    assert r.json()["stale"] is True


async def test_regenerate_overwrites_and_clears_stale(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    _use(claude_tree, reply=_MERMAID_REPLY)
    await client.post(_diagram_url(_FE))
    (claude_tree[0] / "frontend-dev" / "SKILL.md").write_text("changed", encoding="utf-8")

    app.dependency_overrides[get_light_runner] = lambda: FakeRunner(
        reply="```mermaid\nflowchart LR\n  X-->Y\n```"
    )
    r = await client.post(_diagram_url(_FE))
    assert r.json()["mermaid"] == "flowchart LR\n  X-->Y"
    assert r.json()["stale"] is False


async def test_generate_missing_mermaid_block_502(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    _use(claude_tree, reply="Sorry, I could not build a diagram.")
    r = await client.post(_diagram_url(_FE))
    assert r.status_code == 502
    assert "mermaid" in r.json()["detail"]


async def test_generate_cli_failure_502(
    client: AsyncClient, claude_tree: tuple[Path, Path]
) -> None:
    _use(claude_tree, error="claude timed out after 300s")
    r = await client.post(_diagram_url(_FE))
    assert r.status_code == 502
