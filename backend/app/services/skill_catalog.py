"""Community skill catalog: search skills.sh + GitHub, resolve a license, and
fetch a skill folder's files for preview/install.

Client-free leaf — no FastAPI and no SQLAlchemy imports. Every outbound call
takes an optional `transport` (same idiom as app/providers/azuredevops.py) so
tests inject `httpx.MockTransport` and no test ever reaches the network.

skills.sh and GitHub repo search are run concurrently and merged; a source
that fails degrades to a `SourceError` on the result rather than failing the
whole search, so a rate-limited GitHub never hides working skills.sh results.
"""

from __future__ import annotations

import asyncio
import base64
import re
import time
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

import httpx

from app.config import settings
from app.core.exceptions import (
    GitHubRateLimitError,
    SkillCatalogError,
    SkillFetchError,
    SkillNotFoundError,
)

SKILLS_SH_SEARCH_URL = "https://www.skills.sh/api/search"
GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"
GITHUB_API_BASE = "https://api.github.com"
GITHUB_TREE_PATH = "git/trees/HEAD"
# Serves file bytes outside the API quota; HEAD resolves to the default branch.
RAW_BASE = "https://raw.githubusercontent.com"

_TIMEOUT = 8.0
_RETRIES = 1  # one retry on a transport error, a 5xx, or a 429

MAX_SKILL_BYTES = 5 * 1024 * 1024
MAX_SKILL_ENTRIES = 200
MAX_SKILL_DEPTH = 5


@dataclass(frozen=True)
class CatalogSkill:
    owner: str
    repo: str
    skill: str
    name: str
    description: str
    registry: str  # "skills_sh" | "github"
    installs: int | None
    license: str | None
    # False for a skills.sh hit (no license lookup at search time — see resolve_license).
    license_resolved: bool
    url: str


@dataclass(frozen=True)
class SourceError:
    registry: str
    message: str


@dataclass(frozen=True)
class CatalogResult:
    skills: list[CatalogSkill]
    errors: list[SourceError]


@dataclass(frozen=True)
class SkillFile:
    relative_path: str
    content: bytes


@dataclass(frozen=True)
class SkillHistory:
    created_at: datetime | None
    last_modified_at: datetime | None
    # First line of the most recent commit touching the folder — third-party text.
    last_change_summary: str | None


@dataclass(frozen=True)
class FetchedSkill:
    skill_md: str
    files: list[SkillFile]
    # Folder inside the repo the skill was read from; "" when it is the repo root.
    root_path: str


class _RetryableStatus(Exception):
    """Internal signal: a 5xx/429 response, retried once before propagating."""

    def __init__(self, response: httpx.Response) -> None:
        super().__init__(f"{response.status_code}: {response.text[:200]}")


def _github_headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    return headers


def _raise_if_rate_limited(response: httpx.Response) -> None:
    """A spent quota is not worth retrying, and the raw GitHub body is not worth
    showing — this names the remedy instead."""
    if response.status_code not in (403, 429):
        return
    if response.headers.get("x-ratelimit-remaining") != "0":
        return
    reset = response.headers.get("x-ratelimit-reset")
    when = ""
    if reset and reset.isdigit():
        minutes = max(0, round((int(reset) - time.time()) / 60))
        when = f" It resets in about {minutes} minute{'s' if minutes != 1 else ''}."
    limit = response.headers.get("x-ratelimit-limit", "60")
    raise GitHubRateLimitError(
        f"GitHub's hourly request limit ({limit}) is used up.{when}"
        " Set GITHUB_TOKEN in backend/.env to raise it to 5000 an hour."
    )


async def _request_with_retry(
    client: httpx.AsyncClient, method: str, url: str, **kwargs: Any
) -> httpx.Response:
    last_exc: Exception | None = None
    for _ in range(_RETRIES + 1):
        try:
            response = await client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            last_exc = exc
            continue
        _raise_if_rate_limited(response)
        if response.status_code >= 500 or response.status_code == 429:
            last_exc = _RetryableStatus(response)
            continue
        return response
    assert last_exc is not None
    raise last_exc


# --- search -----------------------------------------------------------


async def search_catalog(
    query: str, limit: int, *, transport: httpx.AsyncBaseTransport | None = None
) -> CatalogResult:
    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=transport) as client:
        skills_sh_result, github_result = await asyncio.gather(
            _search_skills_sh(client, query),
            _search_github(client, query),
            return_exceptions=True,
        )

    skills_sh_skills, skills_sh_errors = _collect(skills_sh_result, "skills_sh")
    github_skills, github_errors = _collect(github_result, "github")
    errors = skills_sh_errors + github_errors

    if len(errors) == 2:  # both sources failed — nothing usable to return
        raise SkillCatalogError(
            "skill catalog search failed: " + "; ".join(e.message for e in errors)
        )

    merged = _merge(skills_sh_skills, github_skills)
    ordered = sorted(merged, key=lambda s: (-(s.installs or 0), s.name.casefold()))
    return CatalogResult(skills=ordered[:limit], errors=errors)


def _collect(
    result: list[CatalogSkill] | BaseException, registry: str
) -> tuple[list[CatalogSkill], list[SourceError]]:
    if isinstance(result, BaseException):
        return [], [SourceError(registry=registry, message=str(result))]
    return result, []


def _dedupe_key(skill: CatalogSkill) -> tuple[str, str, str]:
    return (skill.owner.casefold(), skill.repo.casefold(), skill.skill.casefold())


def _merge(skills_sh: list[CatalogSkill], github: list[CatalogSkill]) -> list[CatalogSkill]:
    """Dedupe on (owner, repo, skill); skills.sh wins on conflict (it carries
    install counts), but a license GitHub resolved is carried over onto it."""
    by_key: dict[tuple[str, str, str], CatalogSkill] = {}
    for skill in github:
        by_key[_dedupe_key(skill)] = skill
    for skill in skills_sh:
        key = _dedupe_key(skill)
        existing = by_key.get(key)
        if existing is not None and existing.license is not None and skill.license is None:
            skill = replace(skill, license=existing.license, license_resolved=True)
        by_key[key] = skill
    return list(by_key.values())


async def _search_skills_sh(client: httpx.AsyncClient, query: str) -> list[CatalogSkill]:
    response = await _request_with_retry(client, "GET", SKILLS_SH_SEARCH_URL, params={"q": query})
    if response.status_code >= 300:
        raise SkillFetchError(f"skills.sh search {response.status_code}: {response.text[:300]}")
    try:
        data = response.json()
    except ValueError as exc:
        raise SkillFetchError("skills.sh returned a non-JSON body") from exc

    skills: list[CatalogSkill] = []
    for record in _skills_sh_records(data):
        skill = _parse_skills_sh_record(record)
        if skill is not None:
            skills.append(skill)
    return skills


def _skills_sh_records(data: Any) -> list[dict[str, Any]]:
    """skills.sh has no published contract — accept a bare list or a dict
    wrapping one under any of these keys. Any other shape yields no records."""
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in ("results", "skills", "data", "items"):
            value = data.get(key)
            if isinstance(value, list):
                return [r for r in value if isinstance(r, dict)]
    return []


def _parse_skills_sh_record(record: dict[str, Any]) -> CatalogSkill | None:
    name = record.get("name")
    source = record.get("source")
    if not isinstance(name, str) or not name or not isinstance(source, str):
        return None
    owner, _, repo = source.partition("/")
    if not owner or not repo or "/" in repo:
        return None
    skill_id = record.get("skillId") or record.get("id")
    skill = str(skill_id) if isinstance(skill_id, (str, int)) and str(skill_id) else name
    installs = record.get("installs")
    return CatalogSkill(
        owner=owner,
        repo=repo,
        skill=skill,
        name=name,
        description=str(record.get("description") or ""),
        registry="skills_sh",
        installs=installs if isinstance(installs, int) else None,
        license=None,
        license_resolved=False,
        url=f"https://github.com/{owner}/{repo}",
    )


async def _search_github(client: httpx.AsyncClient, query: str) -> list[CatalogSkill]:
    response = await _request_with_retry(
        client,
        "GET",
        GITHUB_SEARCH_URL,
        params={"q": f"{query} topic:claude-skills"},
        headers=_github_headers(),
    )
    if response.status_code >= 300:
        raise SkillFetchError(f"GitHub search {response.status_code}: {response.text[:300]}")
    try:
        data = response.json()
    except ValueError as exc:
        raise SkillFetchError("GitHub search returned a non-JSON body") from exc

    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    skills: list[CatalogSkill] = []
    for item in items:
        skill = _parse_github_record(item)
        if skill is not None:
            skills.append(skill)
    return skills


def _parse_github_record(item: Any) -> CatalogSkill | None:
    """A repo-search hit names a repository, not a skill folder — its `skill`
    is the repo name; fetch_skill resolves the real folder via candidates."""
    if not isinstance(item, dict):
        return None
    full_name = item.get("full_name")
    if not isinstance(full_name, str) or "/" not in full_name:
        return None
    owner, _, repo = full_name.partition("/")
    if not owner or not repo:
        return None
    license_info = item.get("license")
    spdx_id = license_info.get("spdx_id") if isinstance(license_info, dict) else None
    license_id = (
        spdx_id if isinstance(spdx_id, str) and spdx_id not in ("", "NOASSERTION") else None
    )
    return CatalogSkill(
        owner=owner,
        repo=repo,
        skill=repo,
        name=repo,
        description=str(item.get("description") or ""),
        registry="github",
        installs=None,
        license=license_id,
        license_resolved="license" in item,  # GitHub told us something, incl. an explicit null
        url=str(item.get("html_url") or f"https://github.com/{owner}/{repo}"),
    )


# --- license ------------------------------------------------------------


async def resolve_license(
    owner: str, repo: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> str | None:
    """The repo's SPDX id, or None. None means all rights reserved (GitHub
    reported `license: null` or `NOASSERTION`) — not "not looked up"."""
    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=transport) as client:
        try:
            response = await _request_with_retry(
                client, "GET", f"{GITHUB_API_BASE}/repos/{owner}/{repo}", headers=_github_headers()
            )
        except (httpx.HTTPError, _RetryableStatus) as exc:
            raise SkillFetchError(f"{owner}/{repo}: GitHub repo lookup failed: {exc}") from exc

    if response.status_code >= 300:
        raise SkillFetchError(f"GitHub repo lookup {response.status_code}: {response.text[:300]}")
    try:
        data = response.json()
    except ValueError as exc:
        raise SkillFetchError(
            f"{owner}/{repo}: GitHub repo lookup returned a non-JSON body"
        ) from exc

    license_info = data.get("license") if isinstance(data, dict) else None
    if not isinstance(license_info, dict):
        return None
    spdx_id = license_info.get("spdx_id")
    return spdx_id if isinstance(spdx_id, str) and spdx_id not in ("", "NOASSERTION") else None


_LAST_PAGE_RE = re.compile(r'<([^>]+)>;\s*rel="last"')


def _commit_date(commit: Any) -> datetime | None:
    raw = (((commit or {}).get("commit") or {}).get("committer") or {}).get("date")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


async def fetch_history(
    owner: str, repo: str, path: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> SkillHistory:
    """When the skill folder first appeared and when it last changed.

    Two extra API requests: the newest commit carries a Link header naming the
    last page, and that page holds the oldest. Dates are a nice-to-have, so any
    failure here degrades to empty rather than failing the whole preview.
    """
    empty = SkillHistory(created_at=None, last_modified_at=None, last_change_summary=None)
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/commits"
    params = {"path": path, "per_page": "1"} if path else {"per_page": "1"}
    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=transport) as client:
        try:
            newest = await _request_with_retry(
                client, "GET", url, params=params, headers=_github_headers()
            )
        except (httpx.HTTPError, _RetryableStatus, GitHubRateLimitError):
            return empty
        if newest.status_code >= 300:
            return empty
        try:
            newest_body = newest.json()
        except ValueError:
            return empty
        if not isinstance(newest_body, list) or not newest_body:
            return empty

        last_modified_at = _commit_date(newest_body[0])
        message = ((newest_body[0].get("commit") or {}).get("message") or "").strip()
        summary = message.splitlines()[0][:200] if message else None

        # No Link header means a single page, so the newest commit is also the oldest.
        match = _LAST_PAGE_RE.search(newest.headers.get("link", ""))
        created_at = last_modified_at
        if match:
            try:
                oldest = await _request_with_retry(
                    client, "GET", match.group(1), headers=_github_headers()
                )
            except (httpx.HTTPError, _RetryableStatus, GitHubRateLimitError):
                return SkillHistory(None, last_modified_at, summary)
            if oldest.status_code < 300:
                try:
                    oldest_body = oldest.json()
                except ValueError:
                    oldest_body = None
                created_at = (
                    _commit_date(oldest_body[0])
                    if isinstance(oldest_body, list) and oldest_body
                    else None
                )
            else:
                created_at = None

    return SkillHistory(
        created_at=created_at, last_modified_at=last_modified_at, last_change_summary=summary
    )


# --- fetch ----------------------------------------------------------------


def _skill_path_candidates(skill_path: str) -> list[str]:
    """A skills.sh record's `skill` is already the real folder — tried first.
    A GitHub record's `skill` is only the repo name, so the fallbacks try the
    conventional `skills/<name>` layout, then the repo root. A repo that nests
    its skills under category folders is handled by _find_skill_dir_in_tree."""
    raw = [skill_path, f"skills/{skill_path}", ""]
    seen: set[str] = set()
    candidates = []
    for candidate in raw:
        if candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)
    return candidates


async def _get_json(client: httpx.AsyncClient, owner: str, repo: str, path: str) -> Any:
    suffix = f"/{path}" if path else ""
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents{suffix}"
    try:
        response = await _request_with_retry(client, "GET", url, headers=_github_headers())
    except (httpx.HTTPError, _RetryableStatus) as exc:
        raise SkillFetchError(f"{owner}/{repo}: GitHub contents request failed: {exc}") from exc
    if response.status_code == 404:
        return None
    if response.status_code >= 300:
        raise SkillFetchError(f"GitHub contents {response.status_code}: {response.text[:300]}")
    try:
        return response.json()
    except ValueError as exc:
        raise SkillFetchError(f"{owner}/{repo}: GitHub returned a non-JSON body") from exc


async def _list_contents(
    client: httpx.AsyncClient, owner: str, repo: str, path: str
) -> list[dict[str, Any]] | None:
    """The directory listing at `path`, or None on a 404 (candidate miss)."""
    data = await _get_json(client, owner, repo, path)
    if data is None:
        return None
    if not isinstance(data, list):
        return None  # path resolved to a file, not a directory — not a candidate match
    return [e for e in data if isinstance(e, dict)]


async def _download_file(client: httpx.AsyncClient, owner: str, repo: str, path: str) -> bytes:
    data = await _get_json(client, owner, repo, path)
    if not isinstance(data, dict):
        raise SkillFetchError(f"{owner}/{repo}: {path} did not resolve to a single file")
    if data.get("encoding") != "base64" or not isinstance(data.get("content"), str):
        raise SkillFetchError(f"{owner}/{repo}: {path} has no fetchable content")
    try:
        return base64.b64decode(data["content"], validate=False)
    except Exception as exc:  # noqa: BLE001 — any malformed base64 is a fetch refusal
        raise SkillFetchError(f"{owner}/{repo}: {path} content is not valid base64") from exc


def _relative_within(root_path: str, path: str) -> str | None:
    """`path` relative to `root_path`, or None when it escapes the skill
    folder (`..`, an absolute path, or simply not under the root)."""
    if path.startswith("/"):
        return None
    if root_path:
        prefix = f"{root_path}/"
        if not path.startswith(prefix):
            return None
        relative = path[len(prefix) :]
    else:
        relative = path
    if not relative or relative.startswith("/") or ".." in relative.split("/"):
        return None
    return relative


@dataclass(frozen=True)
class _TreeEntry:
    path: str
    is_blob: bool
    size: int


async def _read_tree(
    client: httpx.AsyncClient, owner: str, repo: str
) -> tuple[list[_TreeEntry], bool] | None:
    """Every path in the repo in one request, with `truncated` alongside.

    This is what makes a fetch cost two requests instead of eight: it resolves
    any layout, lists the folder, and reports every file's size, so nothing
    below needs another contents call.
    """
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/{GITHUB_TREE_PATH}"
    try:
        response = await _request_with_retry(
            client, "GET", url, params={"recursive": "1"}, headers=_github_headers()
        )
    except (httpx.HTTPError, _RetryableStatus):
        return None  # fall back to the contents walk
    # GitHubRateLimitError deliberately propagates: a spent quota is not a miss.
    if response.status_code >= 300:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("tree"), list):
        return None
    entries = [
        _TreeEntry(
            path=entry["path"],
            is_blob=entry.get("type") == "blob",
            size=entry["size"] if isinstance(entry.get("size"), int) else 0,
        )
        for entry in data["tree"]
        if isinstance(entry, dict) and isinstance(entry.get("path"), str)
    ]
    return entries, bool(data.get("truncated"))


def _locate_skill_dir(entries: list[_TreeEntry], slug: str) -> str | None:
    """The folder holding SKILL.md, preferring the conventional layouts and
    otherwise taking the shallowest `**/<slug>/SKILL.md` so a slug that appears
    twice still resolves deterministically."""
    holders = {e.path[: -len("SKILL.md")].rstrip("/") for e in entries if _is_skill_md(e)}
    for preferred in (slug, f"skills/{slug}", ""):
        if preferred in holders:
            return preferred
    nested = [h for h in holders if h.rsplit("/", 1)[-1] == slug]
    if not nested:
        return None
    return min(nested, key=lambda path: (path.count("/"), path))


def _is_skill_md(entry: _TreeEntry) -> bool:
    return entry.is_blob and (entry.path == "SKILL.md" or entry.path.endswith("/SKILL.md"))


async def _download_raw(client: httpx.AsyncClient, owner: str, repo: str, path: str) -> bytes:
    """raw.githubusercontent.com serves file bytes without touching the API
    quota, and `HEAD` resolves to the default branch so no extra lookup is
    needed to learn its name."""
    headers = {}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    url = f"{RAW_BASE}/{owner}/{repo}/HEAD/{path}"
    try:
        response = await _request_with_retry(client, "GET", url, headers=headers)
    except (httpx.HTTPError, _RetryableStatus) as exc:
        raise SkillFetchError(f"{owner}/{repo}: could not read {path}: {exc}") from exc
    if response.status_code >= 300:
        raise SkillFetchError(f"{owner}/{repo}: could not read {path} ({response.status_code})")
    return response.content


async def _fetch_from_tree(
    client: httpx.AsyncClient,
    owner: str,
    repo: str,
    root_path: str,
    entries: list[_TreeEntry],
) -> FetchedSkill:
    """Build the skill from the tree listing, downloading bytes over raw.

    Every guard runs against the tree's own sizes and paths, so an oversized or
    escaping skill is refused before a single byte is downloaded.
    """
    prefix = f"{root_path}/" if root_path else ""
    blobs = [e for e in entries if e.is_blob and e.path.startswith(prefix)]

    if len(blobs) > MAX_SKILL_ENTRIES:
        raise SkillFetchError(
            f"{owner}/{repo}: skill folder has more than {MAX_SKILL_ENTRIES} entries"
        )

    wanted: list[tuple[str, str]] = []  # (relative, full path)
    total_bytes = 0
    for blob in blobs:
        relative = _relative_within(root_path, blob.path)
        if relative is None:
            raise SkillFetchError(f"{owner}/{repo}: entry {blob.path!r} escapes the skill folder")
        if relative.count("/") > MAX_SKILL_DEPTH:
            raise SkillFetchError(
                f"{owner}/{repo}: skill folder is deeper than {MAX_SKILL_DEPTH} levels"
            )
        total_bytes += blob.size
        if total_bytes > MAX_SKILL_BYTES:
            raise SkillFetchError(f"{owner}/{repo}: skill folder exceeds {MAX_SKILL_BYTES} bytes")
        wanted.append((relative, blob.path))

    downloads = await asyncio.gather(
        *(_download_raw(client, owner, repo, path) for _, path in wanted)
    )

    skill_md: str | None = None
    files: list[SkillFile] = []
    for (relative, _), content in zip(wanted, downloads, strict=True):
        if relative == "SKILL.md":
            skill_md = content.decode("utf-8", errors="replace")
        else:
            files.append(SkillFile(relative_path=relative, content=content))

    if skill_md is None:
        raise SkillNotFoundError(f"no SKILL.md found for {owner}/{repo}:{root_path}")
    return FetchedSkill(skill_md=skill_md, files=files, root_path=root_path)


async def fetch_skill(
    owner: str, repo: str, skill_path: str, *, transport: httpx.AsyncBaseTransport | None = None
) -> FetchedSkill:
    """The skill's SKILL.md plus its companion files.

    One recursive tree read resolves the folder and lists it, and the bytes come
    from raw.githubusercontent.com, which does not count against the API quota —
    two API requests total, whatever the layout or the file count. A repo too
    large for one tree response falls back to walking the contents API.
    """
    async with httpx.AsyncClient(timeout=_TIMEOUT, transport=transport) as client:
        tree = await _read_tree(client, owner, repo)
        if tree is not None:
            entries, truncated = tree
            if not truncated:
                root_path = _locate_skill_dir(entries, skill_path)
                if root_path is None:
                    raise SkillNotFoundError(f"no SKILL.md found for {owner}/{repo}:{skill_path}")
                return await _fetch_from_tree(client, owner, repo, root_path, entries)

        for candidate in _skill_path_candidates(skill_path):
            listing = await _list_contents(client, owner, repo, candidate)
            if listing is None:
                continue
            if not any(e.get("name") == "SKILL.md" and e.get("type") == "file" for e in listing):
                continue
            return await _walk_skill_folder(client, owner, repo, candidate, listing)
    raise SkillNotFoundError(f"no SKILL.md found for {owner}/{repo}:{skill_path}")


async def _walk_skill_folder(
    client: httpx.AsyncClient,
    owner: str,
    repo: str,
    root_path: str,
    root_entries: list[dict[str, Any]],
) -> FetchedSkill:
    skill_md: str | None = None
    files: list[SkillFile] = []
    total_bytes = 0
    entry_count = 0
    queue: list[tuple[list[dict[str, Any]], int]] = [(root_entries, 0)]

    while queue:
        entries, depth = queue.pop(0)
        if depth > MAX_SKILL_DEPTH:
            raise SkillFetchError(
                f"{owner}/{repo}: skill folder is deeper than {MAX_SKILL_DEPTH} levels"
            )

        for entry in entries:
            entry_count += 1
            if entry_count > MAX_SKILL_ENTRIES:
                raise SkillFetchError(
                    f"{owner}/{repo}: skill folder has more than {MAX_SKILL_ENTRIES} entries"
                )

            name = entry.get("name")
            path = entry.get("path")
            entry_type = entry.get("type")
            if not isinstance(name, str) or not isinstance(path, str):
                raise SkillFetchError(f"{owner}/{repo}: malformed contents entry")

            relative = _relative_within(root_path, path)
            if relative is None:
                raise SkillFetchError(f"{owner}/{repo}: entry {path!r} escapes the skill folder")

            if entry_type == "dir":
                sub_entries = await _list_contents(client, owner, repo, path)
                if sub_entries:
                    queue.append((sub_entries, depth + 1))
                continue
            if entry_type != "file":
                # symlink / submodule — refused rather than followed.
                raise SkillFetchError(
                    f"{owner}/{repo}: entry {path!r} is a {entry_type}, not a file"
                )

            size = entry.get("size")
            if isinstance(size, int):
                total_bytes += size
            if total_bytes > MAX_SKILL_BYTES:
                raise SkillFetchError(
                    f"{owner}/{repo}: skill folder exceeds {MAX_SKILL_BYTES} bytes"
                )

            content = await _download_file(client, owner, repo, path)
            if relative == "SKILL.md":
                skill_md = content.decode("utf-8", errors="replace")
            else:
                files.append(SkillFile(relative_path=relative, content=content))

    if skill_md is None:
        raise SkillNotFoundError(f"no SKILL.md found for {owner}/{repo}:{root_path}")
    return FetchedSkill(skill_md=skill_md, files=files, root_path=root_path)
