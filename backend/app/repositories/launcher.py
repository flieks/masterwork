"""Data access for session_launches."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.launcher import SessionLaunch

DEFAULT_LIST_LIMIT = 20


async def create_launch(
    db: AsyncSession,
    *,
    project_path: str,
    request_text: str,
    mode: str,
    run_id: str | None = None,
) -> SessionLaunch:
    launch = SessionLaunch(
        project_path=project_path, request_text=request_text, mode=mode, run_id=run_id
    )
    db.add(launch)
    await db.flush()
    await db.refresh(launch)
    return launch


async def set_pid(db: AsyncSession, launch: SessionLaunch, pid: int) -> None:
    launch.pid = pid
    await db.flush()


async def get_launch(db: AsyncSession, launch_id: int) -> SessionLaunch | None:
    return await db.get(SessionLaunch, launch_id)


async def list_launches(db: AsyncSession, limit: int = DEFAULT_LIST_LIMIT) -> list[SessionLaunch]:
    """Newest first; `id` as tiebreak so same-instant rows still order deterministically."""
    stmt = (
        select(SessionLaunch)
        .order_by(SessionLaunch.launched_at.desc(), SessionLaunch.id.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())
