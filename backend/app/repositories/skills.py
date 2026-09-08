"""Data access for masterwork-installed community skills."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.skills import InstalledSkill


async def get_installed(db: AsyncSession, name: str) -> InstalledSkill | None:
    result = await db.execute(select(InstalledSkill).where(InstalledSkill.name == name))
    return result.scalar_one_or_none()


async def list_installed(db: AsyncSession) -> list[InstalledSkill]:
    result = await db.execute(select(InstalledSkill).order_by(InstalledSkill.installed_at.desc()))
    return list(result.scalars().all())


async def upsert_installed(
    db: AsyncSession,
    *,
    name: str,
    owner: str,
    repo: str,
    license: str | None,
    registry: str,
    installed_sha: str | None,
    installed_tree_hash: str | None,
    root_path: str | None,
) -> InstalledSkill:
    """Insert on first install, update in place on a re-install with overwrite.
    Either way the drift baseline is this install's, and the cached check is
    cleared: it described a copy that no longer exists."""
    existing = await get_installed(db, name)
    if existing is None:
        existing = InstalledSkill(
            name=name, owner=owner, repo=repo, license=license, source_registry=registry
        )
        db.add(existing)

    existing.owner = owner
    existing.repo = repo
    existing.license = license
    existing.source_registry = registry
    existing.installed_sha = installed_sha
    existing.installed_tree_hash = installed_tree_hash
    existing.root_path = root_path
    existing.last_checked_at = None
    existing.upstream_sha = None
    existing.drift_status = None
    await db.flush()
    await db.refresh(existing)
    return existing


async def record_check(
    db: AsyncSession,
    row: InstalledSkill,
    *,
    checked_at: datetime,
    upstream_sha: str | None,
    status: str,
) -> None:
    row.last_checked_at = checked_at
    row.upstream_sha = upstream_sha
    row.drift_status = status
    await db.flush()


async def delete_installed(db: AsyncSession, name: str) -> None:
    existing = await get_installed(db, name)
    if existing is not None:
        await db.delete(existing)
        await db.flush()
