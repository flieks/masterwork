"""Session launcher business logic: project listing/creation under
projects_root, and spawning a detached factory run.

Every path that reaches the filesystem goes through `resolve_within_roots`
(app/providers/base.py) — the same helper the asset write path uses — so a
traversal name or an out-of-root `project_path` can never escape.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import LaunchSpawner
from app.api.v1.launcher import schemas
from app.api.v1.settings.service import read_settings
from app.config import settings as app_settings
from app.core.exceptions import (
    InvalidProjectNameError,
    LaunchFailedError,
    ProjectCreationError,
    ProjectExistsError,
    ProjectPathOutsideRootError,
)
from app.providers.base import resolve_within_roots
from app.repositories import launcher as launcher_repo

_MAX_NAME_LEN = 100


def _validate_project_name(name: str) -> str:
    trimmed = name.strip()
    if not trimmed:
        raise InvalidProjectNameError("project name must not be empty")
    if len(trimmed) > _MAX_NAME_LEN:
        raise InvalidProjectNameError(f"project name must be at most {_MAX_NAME_LEN} characters")
    if "/" in trimmed or "\\" in trimmed or "\x00" in trimmed:
        raise InvalidProjectNameError("project name must not contain a path separator")
    if trimmed in (".", ".."):
        raise InvalidProjectNameError(f"project name must not be '{trimmed}'")
    if trimmed.startswith("."):
        raise InvalidProjectNameError("project name must not start with '.'")
    return trimmed


async def _projects_root(db: AsyncSession) -> Path:
    return Path((await read_settings(db)).projects_root)


async def list_projects(db: AsyncSession) -> list[schemas.LauncherProject]:
    root = await _projects_root(db)
    if not root.is_dir():
        return []
    return [
        schemas.LauncherProject(
            name=child.name, path=str(child), is_git_repo=(child / ".git").exists()
        )
        for child in sorted(root.iterdir(), key=lambda p: p.name)
        if child.is_dir() and not child.name.startswith(".")
    ]


async def _git_init(path: Path) -> bool:
    """`git init -q` in `path`, async so it never blocks the event loop —
    mirrors app/services/asset_history.py's git pattern. Returns success."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "init",
            "-q",
            cwd=str(path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        return await proc.wait() == 0
    except OSError:
        return False


async def create_project(db: AsyncSession, name: str) -> schemas.LauncherProject:
    validated_name = _validate_project_name(name)
    root = await _projects_root(db)
    candidate = root / validated_name
    resolved = resolve_within_roots(candidate, [root])
    if resolved is None:
        raise InvalidProjectNameError(f"project name escapes projects_root: {name}")
    if resolved.exists():
        raise ProjectExistsError(f"a project named '{validated_name}' already exists")
    resolved.mkdir(parents=False)
    if not await _git_init(resolved):
        resolved.rmdir()  # leave projects_root clean for a retry
        raise ProjectCreationError(f"git init failed for {resolved}")
    return schemas.LauncherProject(name=resolved.name, path=str(resolved), is_git_repo=True)


async def launch(
    db: AsyncSession, body: schemas.LaunchRequest, spawner: LaunchSpawner
) -> schemas.SessionLaunchRead:
    root = await _projects_root(db)
    resolved = resolve_within_roots(Path(body.project_path), [root])
    if resolved is None or not resolved.is_dir():
        raise ProjectPathOutsideRootError(
            f"project_path must be a directory under {root}, got: {body.project_path}"
        )
    if not (resolved / ".git").exists():
        # factory/run.py exits 2 on a non-repo, and a detached process has
        # nowhere to surface that — caught here, synchronously, instead.
        raise ProjectPathOutsideRootError(f"project_path is not a git repository: {resolved}")

    launch_row = await launcher_repo.create_launch(
        db, project_path=str(resolved), request_text=body.request_text, mode=body.mode.value
    )
    log_path = app_settings.masterwork_home / "launches" / f"{launch_row.id}.log"
    try:
        pid = spawner(resolved, body.request_text, log_path)
    except OSError as exc:
        await db.rollback()
        raise LaunchFailedError(f"could not start the factory run: {exc}") from exc

    await launcher_repo.set_pid(db, launch_row, pid)
    await db.commit()
    return schemas.SessionLaunchRead(
        id=launch_row.id,
        project_path=launch_row.project_path,
        request_text=launch_row.request_text,
        mode=schemas.LaunchMode(launch_row.mode),
        launched_at=launch_row.launched_at,
        pid=pid,
        launched=True,
    )
