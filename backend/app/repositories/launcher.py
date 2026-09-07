"""Data access for session_launches."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.launcher import DismissedRun, SessionLaunch

DEFAULT_LIST_LIMIT = 20


async def create_launch(
    db: AsyncSession,
    *,
    project_path: str,
    request_text: str,
    mode: str,
    run_id: str | None = None,
    checks_run_id: str | None = None,
) -> SessionLaunch:
    launch = SessionLaunch(
        project_path=project_path,
        request_text=request_text,
        mode=mode,
        run_id=run_id,
        checks_run_id=checks_run_id,
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


async def list_dismissed_runs(db: AsyncSession) -> set[tuple[str, str]]:
    """Every (project_path, run_id) the user has waved away."""
    result = await db.execute(select(DismissedRun.project_path, DismissedRun.run_id))
    return {(row[0], row[1]) for row in result.all()}


async def dismiss_run(db: AsyncSession, *, project_path: str, run_id: str) -> None:
    """Idempotent: dismissing an already-dismissed run changes nothing."""
    existing = await db.execute(
        select(DismissedRun).where(
            DismissedRun.project_path == project_path, DismissedRun.run_id == run_id
        )
    )
    if existing.scalar_one_or_none() is None:
        db.add(DismissedRun(project_path=project_path, run_id=run_id))
        await db.commit()


async def restore_run(db: AsyncSession, *, project_path: str, run_id: str) -> None:
    await db.execute(
        delete(DismissedRun).where(
            DismissedRun.project_path == project_path, DismissedRun.run_id == run_id
        )
    )
    await db.commit()


async def list_checks(db: AsyncSession) -> dict[tuple[str, str], str]:
    """(project_path, checked run id) -> the newest check run started for it."""
    result = await db.execute(
        select(SessionLaunch)
        .where(SessionLaunch.checks_run_id.is_not(None), SessionLaunch.run_id.is_not(None))
        .order_by(SessionLaunch.launched_at.asc())
    )
    checks: dict[tuple[str, str], str] = {}
    for row in result.scalars().all():
        assert row.checks_run_id is not None and row.run_id is not None
        checks[(row.project_path, row.checks_run_id)] = row.run_id  # last wins = newest
    return checks
