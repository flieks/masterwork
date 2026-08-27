"""skill_catalog.py against httpx.MockTransport — no test ever reaches the
network. Handlers are routed on request.url.host, same idiom as
tests/unit/test_azuredevops_client.py."""

from __future__ import annotations

import base64
from collections.abc import Callable

import httpx
import pytest

from app.core.exceptions import (
    GitHubRateLimitError,
    SkillCatalogError,
    SkillFetchError,
    SkillNotFoundError,
)
from app.services import skill_catalog

Handler = Callable[[httpx.Request], httpx.Response]


def _route(
    skills_sh: Handler | None = None,
    github: Handler | None = None,
    raw: Handler | None = None,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.skills.sh":
            if skills_sh is None:
                raise AssertionError("unexpected skills.sh request")
            return skills_sh(request)
        if request.url.host == "api.github.com":
            if github is None:
                raise AssertionError("unexpected GitHub request")
            return github(request)
        if request.url.host == "raw.githubusercontent.com":
            if raw is None:
                raise AssertionError("unexpected raw.githubusercontent request")
            return raw(request)
        raise AssertionError(f"unexpected host: {request.url.host}")

    return httpx.MockTransport(handler)


def _skills_sh_ok(records: list[dict[str, object]]) -> Handler:
    return lambda request: httpx.Response(200, json=records)


def _github_search_ok(items: list[dict[str, object]]) -> Handler:
    return lambda request: httpx.Response(200, json={"items": items})


async def test_merges_disjoint_results_from_both_sources() -> None:
    transport = _route(
        skills_sh=_skills_sh_ok(
            [
                {
                    "id": 1,
                    "skillId": "alpha",
                    "name": "Alpha",
                    "source": "acme/widgets",
                    "installs": 5,
                }
            ]
        ),
        github=_github_search_ok(
            [
                {
                    "full_name": "other/beta-tools",
                    "description": "Beta",
                    "html_url": "https://github.com/other/beta-tools",
                }
            ]
        ),
    )

    result = await skill_catalog.search_catalog("x", 25, transport=transport)

    assert result.errors == []
    assert {s.registry for s in result.skills} == {"skills_sh", "github"}
    assert {s.skill for s in result.skills} == {"alpha", "beta-tools"}


async def test_dedupes_on_owner_repo_skill_skills_sh_wins_and_carries_license() -> None:
    transport = _route(
        skills_sh=_skills_sh_ok(
            [
                {
                    "id": 1,
                    "skillId": "frontend-dev",
                    "name": "Frontend Dev",
                    "source": "Acme/frontend-dev",
                    "installs": 42,
                }
            ]
        ),
        github=_github_search_ok(
            [
                {
                    "full_name": "acme/frontend-dev",
                    "description": "React guidelines",
                    "html_url": "https://github.com/acme/frontend-dev",
                    "license": {"spdx_id": "MIT"},
                }
            ]
        ),
    )

    result = await skill_catalog.search_catalog("x", 25, transport=transport)

    assert len(result.skills) == 1
    winner = result.skills[0]
    assert winner.registry == "skills_sh"
    assert winner.installs == 42
    assert winner.license == "MIT"  # carried over from the GitHub record it collided with


async def test_a_failing_source_degrades_to_a_partial_result() -> None:
    transport = _route(
        skills_sh=_skills_sh_ok(
            [
                {
                    "id": 1,
                    "skillId": "alpha",
                    "name": "Alpha",
                    "source": "acme/widgets",
                    "installs": 5,
                }
            ]
        ),
        github=lambda request: httpx.Response(403, text="rate limited"),
    )

    result = await skill_catalog.search_catalog("x", 25, transport=transport)

    assert len(result.skills) == 1
    assert len(result.errors) == 1
    assert result.errors[0].registry == "github"


async def test_both_sources_failing_raises() -> None:
    transport = _route(
        skills_sh=lambda request: httpx.Response(500, text="boom"),
        github=lambda request: httpx.Response(500, text="boom"),
    )

    with pytest.raises(SkillCatalogError):
        await skill_catalog.search_catalog("x", 25, transport=transport)


async def test_resolve_license_maps_null_and_noassertion_to_none() -> None:
    async def license_for(body: dict[str, object]) -> str | None:
        transport = _route(github=lambda request: httpx.Response(200, json=body))
        return await skill_catalog.resolve_license("acme", "widgets", transport=transport)

    assert await license_for({"license": None}) is None
    assert await license_for({"license": {"spdx_id": "MIT"}}) == "MIT"
    assert await license_for({"license": {"spdx_id": "NOASSERTION"}}) is None


async def test_one_retry_on_a_failed_attempt() -> None:
    calls = {"skills_sh": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["skills_sh"] += 1
        if calls["skills_sh"] == 1:
            return httpx.Response(500, text="boom")
        return httpx.Response(
            200, json=[{"id": 1, "skillId": "alpha", "name": "Alpha", "source": "acme/widgets"}]
        )

    transport = _route(skills_sh=flaky, github=_github_search_ok([]))

    result = await skill_catalog.search_catalog("x", 25, transport=transport)

    assert calls["skills_sh"] == 2
    assert len(result.skills) == 1


def _skill_md_content_response() -> httpx.Response:
    return httpx.Response(
        200, json={"content": base64.b64encode(b"# Skill").decode(), "encoding": "base64"}
    )


def _tree(paths: list[tuple[str, int]], *, truncated: bool = False) -> Handler:
    """A recursive-tree response listing (path, size) blobs."""
    return lambda request: httpx.Response(
        200,
        json={
            "truncated": truncated,
            "tree": [{"path": p, "type": "blob", "size": n} for p, n in paths],
        },
    )


def _raw_ok(body: bytes = b"# Skill") -> Handler:
    return lambda request: httpx.Response(200, content=body)


async def test_a_fetch_costs_two_api_requests_whatever_the_layout() -> None:
    """The whole point of reading the tree: one request resolves the folder and
    lists it, and the bytes come from raw, which is outside the API quota."""
    api_calls: list[str] = []
    raw_calls: list[str] = []

    def github(request: httpx.Request) -> httpx.Response:
        api_calls.append(request.url.path)
        return _tree(
            [
                ("README.md", 10),
                ("research/my-skill/SKILL.md", 7),
                ("research/my-skill/docs.md", 5),
                ("research/my-skill/refs/extra.md", 5),
            ]
        )(request)

    def raw(request: httpx.Request) -> httpx.Response:
        raw_calls.append(request.url.path)
        return _raw_ok()(request)

    transport = _route(github=github, raw=raw)

    fetched = await skill_catalog.fetch_skill("acme", "widgets", "my-skill", transport=transport)

    assert fetched.skill_md == "# Skill"
    assert sorted(f.relative_path for f in fetched.files) == ["docs.md", "refs/extra.md"]
    # One API call regardless of how many files the skill ships.
    assert api_calls == ["/repos/acme/widgets/git/trees/HEAD"]
    assert len(raw_calls) == 3
    assert all(c.startswith("/acme/widgets/HEAD/research/my-skill/") for c in raw_calls)


async def test_the_conventional_layout_wins_over_a_deeper_namesake() -> None:
    transport = _route(
        github=_tree([("my-skill/SKILL.md", 7), ("vendor/my-skill/SKILL.md", 7)]),
        raw=_raw_ok(),
    )

    fetched = await skill_catalog.fetch_skill("acme", "widgets", "my-skill", transport=transport)

    assert fetched.files == []


async def test_fetch_skill_refuses_an_entry_that_escapes_the_folder() -> None:
    transport = _route(
        github=_tree([("my-skill/SKILL.md", 7), ("my-skill/../evil.md", 5)]),
        raw=_raw_ok(),
    )

    with pytest.raises(SkillFetchError):
        await skill_catalog.fetch_skill("acme", "widgets", "my-skill", transport=transport)


async def test_fetch_skill_refuses_over_the_byte_cap_before_downloading() -> None:
    """The tree carries sizes, so an oversized skill is refused without
    downloading a single byte."""

    def raw(request: httpx.Request) -> httpx.Response:
        raise AssertionError("nothing should be downloaded once the cap is tripped")

    transport = _route(
        github=_tree(
            [("my-skill/SKILL.md", 7), ("my-skill/big.bin", skill_catalog.MAX_SKILL_BYTES + 1)]
        ),
        raw=raw,
    )

    with pytest.raises(SkillFetchError):
        await skill_catalog.fetch_skill("acme", "widgets", "my-skill", transport=transport)


async def test_a_truncated_tree_falls_back_to_the_contents_walk() -> None:
    """A repo too large for one tree response still resolves, the slow way."""

    def github(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/repos/acme/widgets/git/trees/HEAD":
            return _tree([("ignored/SKILL.md", 7)], truncated=True)(request)
        if path == "/repos/acme/widgets/contents/my-skill":
            return httpx.Response(
                200,
                json=[{"name": "SKILL.md", "path": "my-skill/SKILL.md", "type": "file", "size": 7}],
            )
        if path == "/repos/acme/widgets/contents/my-skill/SKILL.md":
            return _skill_md_content_response()
        return httpx.Response(404, json={"message": "Not Found"})

    transport = _route(github=github)

    fetched = await skill_catalog.fetch_skill("acme", "widgets", "my-skill", transport=transport)

    assert fetched.skill_md == "# Skill"


async def test_fetch_skill_still_raises_when_the_tree_has_no_match() -> None:
    def github(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/acme/widgets/git/trees/HEAD":
            return _tree([("README.md", 10)])(request)
        return httpx.Response(404, json={"message": "Not Found"})

    transport = _route(github=github)

    with pytest.raises(SkillNotFoundError):
        await skill_catalog.fetch_skill("acme", "widgets", "my-skill", transport=transport)


async def test_a_spent_github_quota_names_the_token_as_the_fix() -> None:
    """The raw GitHub body is unreadable in a dialog; the message must say what
    to do about it, and a spent quota must not be retried."""
    attempts = {"n": 0}

    def github(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(
            403,
            headers={
                "x-ratelimit-remaining": "0",
                "x-ratelimit-limit": "60",
                "x-ratelimit-reset": "0",
            },
            json={"message": "API rate limit exceeded for 187.13.23.247."},
        )

    transport = _route(github=github)

    with pytest.raises(GitHubRateLimitError) as excinfo:
        await skill_catalog.fetch_skill("acme", "widgets", "my-skill", transport=transport)

    assert "GITHUB_TOKEN" in str(excinfo.value)
    assert attempts["n"] == 1  # not retried — the quota will not refill in a second


async def test_a_plain_403_is_not_treated_as_a_rate_limit() -> None:
    def github(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "Forbidden"})

    transport = _route(github=github)

    with pytest.raises(SkillFetchError):
        await skill_catalog.fetch_skill("acme", "widgets", "my-skill", transport=transport)
