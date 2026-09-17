"""v1.49 catalog installs across the three skills folders.

Install picks a folder, detection looks in all three, and update/uninstall act on
wherever the copy lives now — including a skill made generic after install,
whose agent folders hold only links to ~/.agents/skills.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from app.config import settings
from app.providers.base import DISABLED_DIR
from tests.integration.test_skill_drift import _OWNER, _REPO, _SHA_2, _SKILL, _upstream


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    found = {
        "claude": tmp_path / "claude" / "skills",
        "codex": tmp_path / "codex" / "skills",
        "generic": tmp_path / "agents" / "skills",
    }
    monkeypatch.setattr(settings, "claude_skills_root", found["claude"])
    monkeypatch.setattr(settings, "codex_skills_root", found["codex"])
    monkeypatch.setattr(settings, "generic_skills_root", found["generic"])
    return found


async def _install(client: AsyncClient, **extra: object) -> dict[str, object]:
    body = {"owner": _OWNER, "repo": _REPO, "skill": _SKILL, **extra}
    r = await client.post("/api/v1/skills/install", json=body)
    assert r.status_code == 200, r.text
    return dict(r.json())


def _migrate_by_hand(roots: dict[str, Path], *, parked: bool = False) -> Path:
    """What a pre-v1.50 migrateAssetToGeneric left: the real folder generic, a link per agent."""
    sub = Path(DISABLED_DIR) if parked else Path()
    real = roots["generic"] / sub / _SKILL
    real.parent.mkdir(parents=True, exist_ok=True)
    (roots["claude"] / _SKILL).rename(real)
    for agent in ("claude", "codex"):
        link = roots[agent] / sub / _SKILL
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(real, target_is_directory=True)
    return real


async def test_default_target_is_still_claude(client: AsyncClient, roots: dict[str, Path]) -> None:
    _upstream()
    body = await _install(client)
    assert (roots["claude"] / _SKILL / "SKILL.md").is_file()
    assert body["target"] == "claude"
    assert body["location"] == "claude"
    assert body["asset_id"] == f"claude:skill:{_SKILL}"


async def test_install_into_codex(client: AsyncClient, roots: dict[str, Path]) -> None:
    _upstream()
    body = await _install(client, target="codex")
    assert (roots["codex"] / _SKILL / "SKILL.md").is_file()
    assert not (roots["claude"] / _SKILL).exists()
    assert body["asset_id"] == f"codex:skill:{_SKILL}"
    assert body["location"] == "codex"


async def test_install_generic_links_it_into_claude_only(
    client: AsyncClient, roots: dict[str, Path]
) -> None:
    _upstream()
    body = await _install(client, target="generic")
    real = roots["generic"] / _SKILL
    assert (real / "SKILL.md").is_file() and not real.is_symlink()
    link = roots["claude"] / _SKILL
    assert link.is_symlink() and link.resolve() == real.resolve()
    # Codex loads ~/.agents/skills itself.
    assert not (roots["codex"] / _SKILL).exists() and not (roots["codex"] / _SKILL).is_symlink()
    assert body["asset_id"] == f"generic:skill:{_SKILL}"


async def test_an_unknown_target_is_a_422(client: AsyncClient, roots: dict[str, Path]) -> None:
    _upstream()
    r = await client.post(
        "/api/v1/skills/install",
        json={"owner": _OWNER, "repo": _REPO, "skill": _SKILL, "target": "cursor"},
    )
    assert r.status_code == 422


async def test_a_copy_in_any_folder_blocks_a_second_install(
    client: AsyncClient, roots: dict[str, Path]
) -> None:
    _upstream()
    hand_made = roots["codex"] / _SKILL
    hand_made.mkdir(parents=True)
    (hand_made / "SKILL.md").write_text("# mine\n", encoding="utf-8")

    r = await client.post(
        "/api/v1/skills/install", json={"owner": _OWNER, "repo": _REPO, "skill": _SKILL}
    )
    assert r.status_code == 409
    assert str(hand_made) in r.json()["detail"]
    assert not (roots["claude"] / _SKILL).exists()  # no twin written elsewhere


async def test_overwrite_replaces_the_copy_where_it_lives(
    client: AsyncClient, roots: dict[str, Path]
) -> None:
    _upstream()
    existing = roots["codex"] / _SKILL
    existing.mkdir(parents=True)
    (existing / "SKILL.md").write_text("# old\n", encoding="utf-8")

    body = await _install(client, overwrite=True)  # target defaults to claude

    assert not (roots["claude"] / _SKILL).exists()
    assert (existing / "SKILL.md").read_text(encoding="utf-8") != "# old\n"
    assert body["target"] == "codex"


async def test_detection_reports_every_folder(client: AsyncClient, roots: dict[str, Path]) -> None:
    _upstream()
    await _install(client, target="generic")
    r = await client.get(f"/api/v1/skills/catalog/{_OWNER}/{_REPO}/{_SKILL}")
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["installed"] is True
    # The agent folders only link to the generic copy, so they are not copies.
    assert detail["installed_in"] == ["generic"]
    assert detail["differs_from_installed"] is False


async def test_update_of_a_migrated_skill_rewrites_the_real_folder_and_keeps_the_links(
    client: AsyncClient, roots: dict[str, Path]
) -> None:
    _upstream()
    await _install(client)
    real = _migrate_by_hand(roots)

    _upstream(skill_md="# Frontend Dev\n\nUse Vite too.\n", sha=_SHA_2)
    r = await client.post(f"/api/v1/skills/installed/{_SKILL}/update", json={})
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["location"] == "generic"
    assert body["asset_id"] == f"generic:skill:{_SKILL}"
    assert (real / "SKILL.md").read_text(encoding="utf-8") == "# Frontend Dev\n\nUse Vite too.\n"
    for agent in ("claude", "codex"):
        link = roots[agent] / _SKILL
        assert link.is_symlink(), f"{agent} lost its link"
        # Codex reads the same fresh copy, not a stale one.
        assert (link / "SKILL.md").read_text(encoding="utf-8").endswith("Use Vite too.\n")
    assert not list(roots["generic"].glob(".masterwork-*"))


async def test_update_of_a_parked_migrated_skill_stays_parked(
    client: AsyncClient, roots: dict[str, Path]
) -> None:
    _upstream()
    await _install(client)
    real = _migrate_by_hand(roots, parked=True)

    _upstream(skill_md="# Frontend Dev\n\nParked but current.\n", sha=_SHA_2)
    r = await client.post(f"/api/v1/skills/installed/{_SKILL}/update", json={})
    assert r.status_code == 200, r.text
    assert (real / "SKILL.md").read_text(encoding="utf-8").endswith("Parked but current.\n")
    assert (roots["claude"] / DISABLED_DIR / _SKILL).is_symlink()


async def test_uninstall_of_a_migrated_skill_removes_the_links_and_the_folder(
    client: AsyncClient, roots: dict[str, Path]
) -> None:
    _upstream()
    await _install(client)
    real = _migrate_by_hand(roots)
    unrelated = roots["codex"] / "other"
    unrelated.mkdir()
    (unrelated / "SKILL.md").write_text("# other\n", encoding="utf-8")

    r = await client.delete(f"/api/v1/skills/installed/{_SKILL}")
    assert r.status_code == 204, r.text

    assert not real.exists()
    for agent in ("claude", "codex"):
        assert not (roots[agent] / _SKILL).is_symlink()
        assert not (roots[agent] / _SKILL).exists()
    assert (unrelated / "SKILL.md").is_file()
    assert (await client.get("/api/v1/skills/installed")).json() == []


async def test_uninstall_leaves_a_same_named_copy_in_another_folder(
    client: AsyncClient, roots: dict[str, Path]
) -> None:
    _upstream()
    await _install(client)
    twin = roots["codex"] / _SKILL
    twin.mkdir(parents=True)
    (twin / "SKILL.md").write_text("# hand-made twin\n", encoding="utf-8")

    r = await client.delete(f"/api/v1/skills/installed/{_SKILL}")
    assert r.status_code == 204
    assert not (roots["claude"] / _SKILL).exists()
    assert (twin / "SKILL.md").is_file()


async def test_a_link_outside_the_managed_folders_is_refused(
    client: AsyncClient, roots: dict[str, Path], tmp_path: Path
) -> None:
    _upstream()
    await _install(client)
    elsewhere = tmp_path / "my-repo" / _SKILL
    elsewhere.parent.mkdir()
    (roots["claude"] / _SKILL).rename(elsewhere)
    (roots["claude"] / _SKILL).symlink_to(elsewhere, target_is_directory=True)

    r = await client.delete(f"/api/v1/skills/installed/{_SKILL}")
    assert r.status_code == 409
    assert str(elsewhere) in r.json()["detail"]
    assert (elsewhere / "SKILL.md").is_file()

    r = await client.post(f"/api/v1/skills/installed/{_SKILL}/update", json={"force": True})
    assert r.status_code == 409
    assert (roots["claude"] / _SKILL).is_symlink()
