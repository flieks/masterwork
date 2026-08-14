"""Session launcher endpoints: project listing/creation, and launching a
detached factory run."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import LaunchSpawner, get_db, get_launch_spawner
from app.api.v1.launcher import schemas, service

router = APIRouter(tags=["launcher"])


@router.get(
    "/launcher/projects",
    response_model=list[schemas.LauncherProject],
    operation_id="listLauncherProjects",
)
async def list_launcher_projects(
    db: AsyncSession = Depends(get_db),
) -> list[schemas.LauncherProject]:
    return await service.list_projects(db)


@router.post(
    "/launcher/projects",
    response_model=schemas.LauncherProject,
    status_code=status.HTTP_201_CREATED,
    operation_id="createLauncherProject",
)
async def create_launcher_project(
    body: schemas.LauncherProjectCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> schemas.LauncherProject:
    return await service.create_project(db, body.name)


@router.post(
    "/launcher/launch",
    response_model=schemas.SessionLaunchRead,
    operation_id="launchSession",
)
async def launch_session(
    body: schemas.LaunchRequest,
    db: AsyncSession = Depends(get_db),
    spawner: LaunchSpawner = Depends(get_launch_spawner),
) -> schemas.SessionLaunchRead:
    return await service.launch(db, body, spawner)
