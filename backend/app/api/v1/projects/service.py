"""Project business logic: CRUD with partial-update semantics."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.projects import schemas, serializers
from app.core.exceptions import ProjectNotFoundError
from app.db.models.project import Project
from app.repositories import projects as project_repo


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _parse_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        return None


async def get_project_or_404(db: AsyncSession, project_id: str) -> Project:
    parsed = _parse_uuid(project_id)
    project = await project_repo.get_project(db, parsed) if parsed is not None else None
    if project is None:
        raise ProjectNotFoundError(f"unknown project: {project_id}")
    return project


async def list_projects(db: AsyncSession) -> list[schemas.Project]:
    projects = await project_repo.list_projects(db)
    return [serializers.project_to_schema(p) for p in projects]


async def create_project(db: AsyncSession, body: schemas.ProjectCreateRequest) -> schemas.Project:
    project = await project_repo.create_project(db, name=body.name, goal=body.goal)
    await db.commit()
    return serializers.project_to_schema(project)


async def get_project(db: AsyncSession, project_id: str) -> schemas.Project:
    project = await get_project_or_404(db, project_id)
    return serializers.project_to_schema(project)


async def update_project(
    db: AsyncSession, project_id: str, body: schemas.ProjectUpdateRequest
) -> schemas.Project:
    project = await get_project_or_404(db, project_id)
    fields = body.model_fields_set

    # Partial update: only touch provided fields. name/goal/asset_ids are not
    # nullable, so a provided null is ignored; flow_mermaid IS nullable, so a
    # provided null clears the diagram.
    if "name" in fields and body.name is not None:
        project.name = body.name
    if "goal" in fields and body.goal is not None:
        project.goal = body.goal
    if "flow_mermaid" in fields:
        project.flow_mermaid = body.flow_mermaid
    if "asset_ids" in fields and body.asset_ids is not None:
        project.asset_ids = list(body.asset_ids)
    if "scenario" in fields and body.scenario is not None:
        project.scenario = body.scenario

    # Set updated_at explicitly: avoids the async lazy refresh the server-side
    # onupdate would otherwise force on serialization, and bumps ordering.
    project.updated_at = _utcnow()
    await db.commit()
    return serializers.project_to_schema(project)


async def delete_project(db: AsyncSession, project_id: str) -> None:
    project = await get_project_or_404(db, project_id)
    await project_repo.delete_project(db, project)
    await db.commit()
