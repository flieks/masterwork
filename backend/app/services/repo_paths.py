"""Normalize a git remote URL to a comparison key, and resolve it to a local
checkout under projects_root.

Azure DevOps reports one repository under different URL forms depending on
the caller (ssh clone vs. https clone vs. the API's own `remoteUrl`/`webUrl`),
so ssh and https forms of the same repo must normalize to one key or the
stored-mapping lookup and the projects_root scan would both miss real matches.
"""

from __future__ import annotations

import configparser
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories import work as work_repo

# git@host:path or ssh://user@host/path — no "://" before the ':' means scp syntax.
_SCP_SYNTAX = re.compile(r"^[^/@\s]+@([^:/\s]+):(.+)$")

# Azure DevOps' ssh host names, and the v3/{org}/{project}/{repo} path they use.
_AZURE_SSH_HOST = re.compile(r"^(ssh\.dev\.azure\.com|vs-ssh\.[^.]+\.visualstudio\.com)$")
_AZURE_SSH_PATH = re.compile(r"^v3/([^/]+)/([^/]+)/([^/]+)$")


def normalize_remote_url(url: str) -> str:
    """Canonical comparison key for a git remote: userinfo/PAT dropped, ssh
    rewritten to its https-shaped equivalent, host lowercased, trailing
    `/`/`.git` dropped. Never raises — garbage input just normalizes oddly."""
    value = url.strip()
    if not value:
        return ""

    if "://" not in value:
        scp = _SCP_SYNTAX.match(value)
        if scp:
            host, path = scp.groups()
            value = f"ssh://{host}/{path}"

    try:
        parsed = urlsplit(value)
    except ValueError:
        return value

    host = (parsed.hostname or "").lower()
    path = parsed.path

    if _AZURE_SSH_HOST.match(host):
        segments = _AZURE_SSH_PATH.match(path.strip("/"))
        if segments:
            org, project, repo = segments.groups()
            host = "dev.azure.com"
            path = f"/{org}/{project}/_git/{repo}"

    path = path.rstrip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    path = path.rstrip("/")

    return f"https://{host}{path}"


def read_git_origin(repo: Path) -> str | None:
    """`repo/.git/config`'s `[remote "origin"]` url, parsed without shelling
    out to git. None on anything short of a clean read: missing/unreadable
    file, malformed config, no origin remote. A `.git` that is a file (a
    worktree/submodule pointer) is treated as "no origin" rather than
    followed — that would need real git plumbing."""
    config_path = repo / ".git" / "config"
    if not config_path.is_file():
        return None
    parser = configparser.ConfigParser(strict=False)
    try:
        parser.read(config_path, encoding="utf-8")
    except (OSError, UnicodeDecodeError, configparser.Error):
        return None
    section = 'remote "origin"'
    if not parser.has_section(section):
        return None
    try:
        url = parser.get(section, "url", fallback=None)
    except configparser.Error:
        return None
    return url.strip() if url and url.strip() else None


@dataclass(frozen=True)
class Resolution:
    local_path: Path | None
    matched_from: str  # "stored" | "scan" | "none"
    reason: str | None


async def resolve_local_path(db: AsyncSession, remote_url: str, projects_root: Path) -> Resolution:
    """Stored mapping -> scan of projects_root's immediate subfolders by
    their git origin -> unresolved, in that order. A scan hit is persisted
    before it is returned, so the next delegate for this remote is a
    stored-mapping hit. A stored path that no longer exists on disk is
    treated as a miss, so a moved checkout self-heals via the scan."""
    normalized = normalize_remote_url(remote_url)

    stored = await work_repo.get_repo_path(db, normalized)
    if stored is not None and Path(stored.local_path).is_dir():
        return Resolution(Path(stored.local_path), "stored", None)

    if projects_root.is_dir():
        for child in sorted(projects_root.iterdir(), key=lambda p: p.name):
            if not child.is_dir() or child.name.startswith("."):
                continue
            origin = read_git_origin(child)
            if origin is None:
                continue
            if normalize_remote_url(origin) == normalized:
                await work_repo.upsert_repo_path(db, remote_url=normalized, local_path=str(child))
                return Resolution(child, "scan", None)

    reason = (
        f"No folder under {projects_root} has {remote_url} as its git origin — "
        "pick the checkout for this repository."
    )
    return Resolution(None, "none", reason)
