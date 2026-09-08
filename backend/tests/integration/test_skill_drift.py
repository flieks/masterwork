"""Upstream drift check + update against the real test database and a
MockTransport GitHub. The transport is rebuilt between calls to move upstream
on (new sha, new content, new files) — the same way it moves in the world."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.deps import get_skill_catalog_transport
from app.config import settings
from app.db.models.skills import InstalledSkill
from app.main import app
from app.repositories import skills as skills_repo
from app.services import skill_install

Handler = Callable[[httpx.Request], httpx.Response]

_OWNER = "acme"
_REPO = "widgets"
_SKILL = "frontend-dev"
_SKILL_MD = "# Frontend Dev\n\nUse React.\n"
_SHA_1 = "1111111111111111111111111111111111111111"
_SHA_2 = "2222222222222222222222222222222222222222"


def _github(
    *,
    skill_md: str = _SKILL_MD,
    sha: str | None = _SHA_1,
    companions: dict[str, bytes] | None = None,
    present: bool = True,
    raw_status: int = 200,
) -> Handler:
    """GitHub as of one upstream state. `sha=None` makes the commits API fail,
    `present=False` removes the folder, `raw_status` breaks the byte download."""
    files = {f"{_SKILL}/SKILL.md": skill_md.encode()}
    for name, content in (companions or {}).items():
        files[f"{_SKILL}/{name}"] = content

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path

        if host == "raw.githubusercontent.com":
            prefix = f"/{_OWNER}/{_REPO}/HEAD/"
            if raw_status != 200:
                return httpx.Response(raw_status, text="raw is down")
            if path.startswith(prefix) and path[len(prefix) :] in files:
                return httpx.Response(200, content=files[path[len(prefix) :]])
            return httpx.Response(404, text="not found")

        assert host == "api.github.com", f"unexpected host: {host}"

        if path == f"/repos/{_OWNER}/{_REPO}":
            return httpx.Response(200, json={"license": {"spdx_id": "MIT"}})
        if path == f"/repos/{_OWNER}/{_REPO}/git/trees/HEAD":
            tree = [{"path": "README.md", "type": "blob", "size": 10}]
            if present:
                tree += [{"path": p, "type": "blob", "size": len(c)} for p, c in files.items()]
            return httpx.Response(200, json={"truncated": False, "tree": tree})
        if path == f"/repos/{_OWNER}/{_REPO}/commits":
            if sha is None:
                return httpx.Response(500, text="commits unavailable")
            return httpx.Response(
                200,
                json=[
                    {
                        "sha": sha,
                        "commit": {
                            "committer": {"date": "2026-08-15T20:48:40Z"},
                            "message": "Clarify the steps\n\nbody ignored",
                        },
                    }
                ],
            )
        if path.startswith(f"/repos/{_OWNER}/{_REPO}/contents"):
            return httpx.Response(404, json={"message": "Not Found"})
        raise AssertionError(f"unexpected GitHub path: {path}")

    return handler


def _upstream(**kwargs: object) -> None:
    handler = _github(**kwargs)  # type: ignore[arg-type]
    app.dependency_overrides[get_skill_catalog_transport] = lambda: httpx.MockTransport(handler)


@pytest.fixture
def skills_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "skills"
    monkeypatch.setattr(settings, "claude_skills_root", root)
    return root


async def _install(client: AsyncClient) -> dict[str, object]:
    r = await client.post(
        "/api/v1/skills/install", json={"owner": _OWNER, "repo": _REPO, "skill": _SKILL}
    )
    assert r.status_code == 200, r.text
    return dict(r.json())


async def _check(client: AsyncClient, name: str = _SKILL) -> dict[str, object]:
    r = await client.post(f"/api/v1/skills/installed/{name}/check")
    assert r.status_code == 200, r.text
    return dict(r.json())


async def _row(session_factory: async_sessionmaker, name: str = _SKILL) -> InstalledSkill:
    async with session_factory() as session:
        row = await skills_repo.get_installed(session, name)
        assert row is not None
        return row


# --- install records the baseline -------------------------------------------


async def test_install_records_sha_tree_hash_and_root_path(
    client: AsyncClient, skills_root: Path, session_factory: async_sessionmaker
) -> None:
    _upstream()

    body = await _install(client)

    assert body["installed_sha"] == _SHA_1
    assert body["root_path"] == _SKILL
    assert body["source_url"] == f"https://github.com/{_OWNER}/{_REPO}/tree/HEAD/{_SKILL}"
    assert body["drift_status"] is None
    assert body["last_checked_at"] is None
    row = await _row(session_factory)
    on_disk = skill_install.read_installed_files(_SKILL, skills_root=skills_root)
    assert on_disk is not None
    assert row.installed_tree_hash == skill_install.tree_hash(on_disk)


async def test_install_still_succeeds_when_the_commit_lookup_fails(
    client: AsyncClient, skills_root: Path
) -> None:
    _upstream(sha=None)

    body = await _install(client)

    assert body["installed_sha"] is None
    assert (skills_root / _SKILL / "SKILL.md").is_file()


async def test_the_installed_list_carries_the_cached_check(
    client: AsyncClient, skills_root: Path
) -> None:
    _upstream()
    await _install(client)
    r = await client.get("/api/v1/skills/installed")
    assert [s["drift_status"] for s in r.json()] == [None]

    _upstream(skill_md="# Frontend Dev\n\nUse Vite too.\n", sha=_SHA_2)
    await _check(client)

    r = await client.get("/api/v1/skills/installed")
    assert r.status_code == 200
    (row,) = r.json()
    assert row["drift_status"] == "upstream_changed"
    assert row["upstream_sha"] == _SHA_2
    assert row["last_checked_at"] is not None


# --- the five statuses ------------------------------------------------------


async def test_check_reports_current_when_nothing_moved(
    client: AsyncClient, skills_root: Path
) -> None:
    _upstream()
    await _install(client)

    body = await _check(client)

    assert body["status"] == "current"
    assert body["upstream_sha"] == _SHA_1
    assert body["installed_sha"] == _SHA_1
    assert body["skill_md_diff"] == ""
    assert body["other_changes"] == []
    assert body["upstream_last_change_summary"] == "Clarify the steps"
    assert str(body["upstream_last_modified_at"]).startswith("2026-08-15")


async def test_check_reports_a_local_edit_without_an_upstream_change(
    client: AsyncClient, skills_root: Path
) -> None:
    _upstream()
    await _install(client)
    (skills_root / _SKILL / "SKILL.md").write_text("# Frontend Dev\n\nMy own notes.\n")

    body = await _check(client)

    assert body["status"] == "edited_locally"
    diff = str(body["skill_md_diff"])
    assert "-My own notes." in diff
    assert "+Use React." in diff


async def test_check_reports_an_upstream_change_with_the_diff_and_new_files(
    client: AsyncClient, skills_root: Path
) -> None:
    _upstream()
    await _install(client)
    _upstream(
        skill_md="# Frontend Dev\n\nUse React and Vite.\n",
        sha=_SHA_2,
        companions={"reference.md": b"details"},
    )

    body = await _check(client)

    assert body["status"] == "upstream_changed"
    assert body["upstream_sha"] == _SHA_2
    assert "+Use React and Vite." in str(body["skill_md_diff"])
    assert body["other_changes"] == [{"path": "reference.md", "change": "added"}]


async def test_check_reports_diverged_when_both_sides_moved(
    client: AsyncClient, skills_root: Path
) -> None:
    _upstream()
    await _install(client)
    (skills_root / _SKILL / "SKILL.md").write_text("# Frontend Dev\n\nMy own notes.\n")
    _upstream(skill_md="# Frontend Dev\n\nUse React and Vite.\n", sha=_SHA_2)

    body = await _check(client)

    assert body["status"] == "diverged"


async def test_check_reports_unknown_origin_for_a_skill_masterwork_did_not_install(
    client: AsyncClient, skills_root: Path
) -> None:
    _upstream()

    body = await _check(client, "hand-written")

    assert body["status"] == "unknown_origin"
    assert body["source_url"] is None


async def test_check_reports_unknown_origin_when_upstream_dropped_the_folder(
    client: AsyncClient, skills_root: Path
) -> None:
    _upstream()
    await _install(client)
    _upstream(present=False)

    body = await _check(client)

    assert body["status"] == "unknown_origin"
    assert body["source_url"] == f"https://github.com/{_OWNER}/{_REPO}/tree/HEAD/{_SKILL}"
    r = await client.get("/api/v1/skills/installed")
    assert r.json()[0]["drift_status"] == "unknown_origin"


async def test_a_row_without_a_baseline_is_checked_by_content(
    client: AsyncClient, skills_root: Path, session_factory: async_sessionmaker
) -> None:
    """Installed before the columns existed: identical copies are current, and
    a difference is diverged since nothing says whose change it is."""
    _upstream()
    await _install(client)
    async with session_factory() as session:
        row = await skills_repo.get_installed(session, _SKILL)
        assert row is not None
        row.installed_sha = None
        row.installed_tree_hash = None
        row.root_path = None
        await session.commit()

    assert (await _check(client))["status"] == "current"

    _upstream(skill_md="# Frontend Dev\n\nUse React and Vite.\n", sha=_SHA_2)
    assert (await _check(client))["status"] == "diverged"


# --- update -------------------------------------------------------------------


async def test_update_replaces_the_copy_and_resets_the_baseline(
    client: AsyncClient, skills_root: Path, session_factory: async_sessionmaker
) -> None:
    _upstream()
    await _install(client)
    _upstream(skill_md="# Frontend Dev\n\nUse React and Vite.\n", sha=_SHA_2)

    r = await client.post(f"/api/v1/skills/installed/{_SKILL}/update", json={"force": False})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["installed_sha"] == _SHA_2
    assert body["drift_status"] == "current"
    assert (
        skills_root / _SKILL / "SKILL.md"
    ).read_text() == "# Frontend Dev\n\nUse React and Vite.\n"
    row = await _row(session_factory)
    on_disk = skill_install.read_installed_files(_SKILL, skills_root=skills_root)
    assert on_disk is not None
    assert row.installed_tree_hash == skill_install.tree_hash(on_disk)
    assert (await _check(client))["status"] == "current"


async def test_update_refuses_to_overwrite_local_edits_without_force(
    client: AsyncClient, skills_root: Path
) -> None:
    _upstream()
    await _install(client)
    (skills_root / _SKILL / "SKILL.md").write_text("# Frontend Dev\n\nMy own notes.\n")
    _upstream(skill_md="# Frontend Dev\n\nUse React and Vite.\n", sha=_SHA_2)

    r = await client.post(f"/api/v1/skills/installed/{_SKILL}/update", json={"force": False})

    assert r.status_code == 409
    assert "edited locally" in r.json()["detail"]
    assert (skills_root / _SKILL / "SKILL.md").read_text() == "# Frontend Dev\n\nMy own notes.\n"


async def test_update_with_force_overwrites_local_edits(
    client: AsyncClient, skills_root: Path
) -> None:
    _upstream()
    await _install(client)
    (skills_root / _SKILL / "SKILL.md").write_text("# Frontend Dev\n\nMy own notes.\n")
    (skills_root / _SKILL / "mine.md").write_text("kept?\n")

    r = await client.post(f"/api/v1/skills/installed/{_SKILL}/update", json={"force": True})

    assert r.status_code == 200, r.text
    assert (skills_root / _SKILL / "SKILL.md").read_text() == _SKILL_MD
    assert not (skills_root / _SKILL / "mine.md").exists()  # a reinstall, not a merge
    assert (await _check(client))["status"] == "current"


async def test_update_404s_for_a_skill_masterwork_did_not_install(
    client: AsyncClient, skills_root: Path
) -> None:
    _upstream()

    r = await client.post("/api/v1/skills/installed/hand-written/update", json={"force": True})

    assert r.status_code == 404


async def test_a_failed_fetch_leaves_the_old_copy_and_row_intact(
    client: AsyncClient, skills_root: Path
) -> None:
    _upstream()
    await _install(client)
    _upstream(sha=_SHA_2, raw_status=503)

    r = await client.post(f"/api/v1/skills/installed/{_SKILL}/update", json={"force": True})

    assert r.status_code == 502
    assert (skills_root / _SKILL / "SKILL.md").read_text() == _SKILL_MD
    assert sorted(p.name for p in skills_root.iterdir()) == [_SKILL]  # no staging leftovers
    r = await client.get("/api/v1/skills/installed")
    assert r.json()[0]["installed_sha"] == _SHA_1


async def test_a_failed_write_restores_the_old_copy(
    client: AsyncClient, skills_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The staging swap: the old folder is moved aside, not deleted, until the
    new tree is fully written, and comes back when the write dies halfway."""
    _upstream()
    await _install(client)
    _upstream(
        skill_md="# Frontend Dev\n\nUse React and Vite.\n",
        sha=_SHA_2,
        companions={"reference.md": b"details"},
    )
    real_write = skill_install._write_within

    def failing_write(root: Path, relative_path: str, content: bytes) -> None:
        if relative_path == "reference.md":
            raise OSError("disk full")
        real_write(root, relative_path, content)

    monkeypatch.setattr(skill_install, "_write_within", failing_write)

    # ASGITransport re-raises unhandled app exceptions rather than answering 500.
    with pytest.raises(OSError, match="disk full"):
        await client.post(f"/api/v1/skills/installed/{_SKILL}/update", json={"force": False})

    assert (skills_root / _SKILL / "SKILL.md").read_text() == _SKILL_MD
    assert sorted(p.name for p in skills_root.iterdir()) == [_SKILL]
