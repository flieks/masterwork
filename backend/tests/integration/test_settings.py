"""GET/PATCH /api/v1/settings against the real endpoint and test database."""

from __future__ import annotations

from pathlib import Path

from httpx import AsyncClient


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
