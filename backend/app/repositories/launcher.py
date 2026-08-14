"""Data access for session_launches."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.launcher import SessionLaunch


async def create_launch(
    db: AsyncSession, *, project_path: str, request_text: str, mode: str
) -> SessionLaunch:
    launch = SessionLaunch(project_path=project_path, request_text=request_text, mode=mode)
    db.add(launch)
    await db.flush()
    await db.refresh(launch)
    return launch


async def set_pid(db: AsyncSession, launch: SessionLaunch, pid: int) -> None:
    launch.pid = pid
    await db.flush()
