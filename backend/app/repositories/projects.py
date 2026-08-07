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
