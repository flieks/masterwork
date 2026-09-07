"""Data access for projects."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.project import Project


async def create_project(db: AsyncSession, *, name: str, goal: str) -> Project:
    project = Project(name=name, goal=goal, asset_ids=[])
    db.add(project)
    await db.flush()
    await db.refresh(project)
    return project


async def list_projects(db: AsyncSession) -> list[Project]:
    result = await db.execute(select(Project).order_by(Project.updated_at.desc()))
    return list(result.scalars().all())


async def get_project(db: AsyncSession, project_id: uuid.UUID) -> Project | None:
    return await db.get(Project, project_id)


async def delete_project(db: AsyncSession, project: Project) -> None:
    await db.delete(project)
    await db.flush()


async def replace_asset_id(db: AsyncSession, old_id: str, new_id: str) -> int:
    """Re-point every project link from `old_id` to `new_id`; -> projects changed.
    An asset that moves folders changes id, and a link must follow it."""
    changed = 0
    for project in await list_projects(db):
        if old_id not in project.asset_ids:
            continue
        project.asset_ids = [new_id if a == old_id else a for a in project.asset_ids]
        changed += 1
    if changed:
        await db.flush()
    return changed
