"""Skill catalog HTTP endpoints against the real test database + a
MockTransport catalog client — zero live skills.sh/GitHub calls."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from httpx import AsyncClient

from app.api.deps import get_skill_catalog_transport
from app.config import settings
from app.main import app

Handler = Callable[[httpx.Request], httpx.Response]

_OWNER = "acme"
_REPO = "widgets"
_SKILL = "frontend-dev"
_SKILL_MD = "# Frontend Dev\n"


def _catalog_handler(
    *,
    skills_sh_status: int = 200,
    github_search_status: int = 200,
    license_spdx: str | None = "MIT",
    skill_md: str = _SKILL_MD,
    commits_status: int = 200,
) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        path = request.url.path

        if host == "www.skills.sh":
            if skills_sh_status != 200:
                return httpx.Response(skills_sh_status, text="boom")
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 1,
                        "skillId": _SKILL,
                        "name": "Frontend Dev",
                        "source": f"{_OWNER}/{_REPO}",
                        "installs": 7,
                        "description": "React frontend guidelines.",
                    }
                ],
            )

        if host == "raw.githubusercontent.com":
            if path == f"/{_OWNER}/{_REPO}/HEAD/{_SKILL}/SKILL.md":
                return httpx.Response(200, content=skill_md.encode())
            return httpx.Response(404, text="not found")

        if host != "api.github.com":
            raise AssertionError(f"unexpected host: {host}")

        if path == "/search/repositories":
            if github_search_status != 200:
                return httpx.Response(github_search_status, text="rate limited")
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "full_name": "other/beta-tools",
                            "description": "Beta tools",
                            "html_url": "https://github.com/other/beta-tools",
                        }
                    ]
                },
            )

        if path == f"/repos/{_OWNER}/{_REPO}":
            license_body = {"spdx_id": license_spdx} if license_spdx else None
            return httpx.Response(200, json={"license": license_body})
        if path == "/repos/anthropics/skills":
            return httpx.Response(200, json={"license": None})

        if path == f"/repos/{_OWNER}/{_REPO}/git/trees/HEAD":
            # One tree read resolves the folder and lists it; only the known
            # skill is present, so anything else 404s.
            return httpx.Response(
                200,
                json={
                    "truncated": False,
                    "tree": [
                        {"path": "README.md", "type": "blob", "size": 10},
                        {"path": f"{_SKILL}/SKILL.md", "type": "blob", "size": 20},
                    ],
                },
            )
        if path == f"/repos/{_OWNER}/{_REPO}/commits":
            if commits_status != 200:
                return httpx.Response(commits_status, text="no history for you")
            page = request.url.params.get("page")
            date = "2026-04-28T09:17:37Z" if page else "2026-08-15T20:48:40Z"
            headers = (
                {}
                if page
                else {
                    "link": (
                        f"<https://api.github.com/repos/{_OWNER}/{_REPO}/commits"
                        f'?path={_SKILL}&per_page=1&page=9>; rel="last"'
                    )
                }
            )
            return httpx.Response(
                200,
                headers=headers,
                json=[
                    {
                        "commit": {
                            "committer": {"date": date},
                            "message": "Clarify the steps\n\nbody ignored",
                        }
                    }
                ],
            )
        if path.startswith(f"/repos/{_OWNER}/{_REPO}/contents"):
            return httpx.Response(404, json={"message": "Not Found"})

        raise AssertionError(f"unexpected GitHub path: {path}")

    return handler


def _use_transport(handler: Handler) -> None:
    app.dependency_overrides[get_skill_catalog_transport] = lambda: httpx.MockTransport(handler)


@pytest.fixture
def skills_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "skills"
    monkeypatch.setattr(settings, "claude_skills_root", root)
    return root


async def test_search_catalog_merges_both_sources(client: AsyncClient, skills_root: Path) -> None:
    _use_transport(_catalog_handler())

    r = await client.get("/api/v1/skills/catalog", params={"q": "react"})

    assert r.status_code == 200
    body = r.json()
    assert body["errors"] == []
    assert {s["skill"] for s in body["skills"]} == {_SKILL, "beta-tools"}


async def test_search_catalog_degrades_when_github_fails(
    client: AsyncClient, skills_root: Path
) -> None:
    _use_transport(_catalog_handler(github_search_status=503))

    r = await client.get("/api/v1/skills/catalog", params={"q": "react"})

    assert r.status_code == 200
    body = r.json()
    assert len(body["skills"]) == 1
    assert len(body["errors"]) == 1
    assert body["errors"][0]["registry"] == "github"


async def test_get_catalog_skill_returns_the_skill_md_and_license(
    client: AsyncClient, skills_root: Path
) -> None:
    _use_transport(_catalog_handler(license_spdx="MIT"))

    r = await client.get(f"/api/v1/skills/catalog/{_OWNER}/{_REPO}/{_SKILL}")

    assert r.status_code == 200
    body = r.json()
    assert "Frontend Dev" in body["skill_md"]
    assert body["license"] == "MIT"
    assert body["all_rights_reserved"] is False
    assert body["files"] == []


async def test_get_catalog_skill_unknown_skill_404s(client: AsyncClient, skills_root: Path) -> None:
    _use_transport(_catalog_handler())

    r = await client.get(f"/api/v1/skills/catalog/{_OWNER}/{_REPO}/does-not-exist")

    assert r.status_code == 404


async def test_install_skill_writes_to_disk_and_records_the_row(
    client: AsyncClient, skills_root: Path
) -> None:
    _use_transport(_catalog_handler())

    r = await client.post(
        "/api/v1/skills/install", json={"owner": _OWNER, "repo": _REPO, "skill": _SKILL}
    )

    assert r.status_code == 200
    body = r.json()
    assert body["asset_id"] == f"claude:skill:{_SKILL}"
    assert (skills_root / _SKILL / "SKILL.md").is_file()

    # The install row makes the re-install a conflict rather than a silent overwrite.
    r2 = await client.post(
        "/api/v1/skills/install", json={"owner": _OWNER, "repo": _REPO, "skill": _SKILL}
    )
    assert r2.status_code == 409


async def test_install_refuses_the_anthropics_document_skills(
    client: AsyncClient, skills_root: Path
) -> None:
    _use_transport(_catalog_handler())

    r = await client.post(
        "/api/v1/skills/install", json={"owner": "anthropics", "repo": "skills", "skill": "pdf"}
    )

    assert r.status_code == 403
    assert "license" in r.json()["detail"]
    assert not (skills_root / "pdf").exists()


async def test_uninstall_removes_the_directory(client: AsyncClient, skills_root: Path) -> None:
    _use_transport(_catalog_handler())
    await client.post(
        "/api/v1/skills/install", json={"owner": _OWNER, "repo": _REPO, "skill": _SKILL}
    )

    r = await client.delete(f"/api/v1/skills/installed/{_SKILL}")

    assert r.status_code == 204
    assert not (skills_root / _SKILL).exists()


async def test_uninstall_refuses_a_directory_masterwork_did_not_install(
    client: AsyncClient, skills_root: Path
) -> None:
    hand_written = skills_root / "hand-written"
    hand_written.mkdir(parents=True)
    (hand_written / "SKILL.md").write_text("# Hand written\n", encoding="utf-8")

    r = await client.delete("/api/v1/skills/installed/hand-written")

    assert r.status_code == 404
    assert hand_written.is_dir()  # the guard that matters


async def test_the_detail_links_to_the_skill_folder_not_just_the_repo(
    client: AsyncClient, skills_root: Path
) -> None:
    _use_transport(_catalog_handler())

    body = (await client.get(f"/api/v1/skills/catalog/{_OWNER}/{_REPO}/{_SKILL}")).json()

    assert body["url"] == f"https://github.com/{_OWNER}/{_REPO}/tree/HEAD/{_SKILL}"


async def test_a_disk_copy_that_drifted_is_reported_as_differing(
    client: AsyncClient, skills_root: Path
) -> None:
    """Most skills declare no version, so 'is mine current?' is answered by
    comparing the text, not by a version field."""
    installed = skills_root / _SKILL
    installed.mkdir(parents=True)
    (installed / "SKILL.md").write_text("# Frontend Dev\n\nan older, edited copy\n")
    _use_transport(_catalog_handler())

    body = (await client.get(f"/api/v1/skills/catalog/{_OWNER}/{_REPO}/{_SKILL}")).json()

    assert body["installed"] is True
    assert body["differs_from_installed"] is True


async def test_an_identical_disk_copy_is_not_reported_as_differing(
    client: AsyncClient, skills_root: Path
) -> None:
    installed = skills_root / _SKILL
    installed.mkdir(parents=True)
    (installed / "SKILL.md").write_text(_SKILL_MD)
    _use_transport(_catalog_handler())

    body = (await client.get(f"/api/v1/skills/catalog/{_OWNER}/{_REPO}/{_SKILL}")).json()

    assert body["differs_from_installed"] is False


async def test_nothing_installed_leaves_the_comparison_null(
    client: AsyncClient, skills_root: Path
) -> None:
    _use_transport(_catalog_handler())

    body = (await client.get(f"/api/v1/skills/catalog/{_OWNER}/{_REPO}/{_SKILL}")).json()

    assert body["differs_from_installed"] is None


async def test_a_declared_version_is_surfaced_from_both_copies(
    client: AsyncClient, skills_root: Path
) -> None:
    """NVIDIA nests it under metadata.version; there is no standard key."""
    installed = skills_root / _SKILL
    installed.mkdir(parents=True)
    (installed / "SKILL.md").write_text(
        '---\nname: frontend-dev\nmetadata:\n  version: "0.1.0"\n---\nold\n'
    )
    _use_transport(_catalog_handler(skill_md='---\nname: frontend-dev\nversion: "2.0"\n---\nnew\n'))

    body = (await client.get(f"/api/v1/skills/catalog/{_OWNER}/{_REPO}/{_SKILL}")).json()

    assert body["version"] == "2.0"
    assert body["installed_version"] == "0.1.0"


async def test_the_detail_reports_when_the_skill_appeared_and_last_changed(
    client: AsyncClient, skills_root: Path
) -> None:
    _use_transport(_catalog_handler())

    body = (await client.get(f"/api/v1/skills/catalog/{_OWNER}/{_REPO}/{_SKILL}")).json()

    assert body["created_at"].startswith("2026-04-28")
    assert body["last_modified_at"].startswith("2026-08-15")
    # Only the subject line, never the whole commit body.
    assert body["last_change_summary"] == "Clarify the steps"


async def test_an_unavailable_history_does_not_break_the_preview(
    client: AsyncClient, skills_root: Path
) -> None:
    """Dates are a nice-to-have; losing them must not cost you the SKILL.md."""
    _use_transport(_catalog_handler(commits_status=403))

    r = await client.get(f"/api/v1/skills/catalog/{_OWNER}/{_REPO}/{_SKILL}")

    assert r.status_code == 200
    body = r.json()
    assert "Frontend Dev" in body["skill_md"]
    assert body["created_at"] is None
    assert body["last_modified_at"] is None
