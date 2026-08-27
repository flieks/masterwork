"""Data access for masterwork-installed community skills."""

from __future__ import annotations

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
    db: AsyncSession, *, name: str, owner: str, repo: str, license: str | None, registry: str
) -> InstalledSkill:
    """Insert on first install, update in place on a re-install with overwrite."""
    existing = await get_installed(db, name)
    if existing is None:
        row = InstalledSkill(
            name=name, owner=owner, repo=repo, license=license, source_registry=registry
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return row

    existing.owner = owner
    existing.repo = repo
    existing.license = license
    existing.source_registry = registry
    await db.flush()
    return existing


async def delete_installed(db: AsyncSession, name: str) -> None:
    existing = await get_installed(db, name)
    if existing is not None:
        await db.delete(existing)
        await db.flush()
