"""Session launcher endpoints: project listing/creation, launching a detached
factory run, and the interview state (listing launches, reading a launch's
pending questions, submitting answers to resume it)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    LaunchSpawner,
    ResumeSpawner,
    get_db,
    get_launch_spawner,
    get_resume_spawner,
)
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


@router.get(
    "/launcher/launches",
    response_model=list[schemas.SessionLaunchListItem],
    operation_id="listSessionLaunches",
)
async def list_session_launches(
    db: AsyncSession = Depends(get_db),
) -> list[schemas.SessionLaunchListItem]:
    return await service.list_launches(db)


@router.get(
    "/launcher/launches/{launch_id}/interview",
    response_model=schemas.InterviewRead,
    operation_id="getLaunchInterview",
)
async def get_launch_interview(
    launch_id: int,
    db: AsyncSession = Depends(get_db),
) -> schemas.InterviewRead:
    return await service.read_interview(db, launch_id)


@router.post(
    "/launcher/launches/{launch_id}/answers",
    response_model=schemas.InterviewResumeRead,
    operation_id="submitInterviewAnswers",
)
async def submit_interview_answers(
    launch_id: int,
    body: schemas.InterviewAnswersRequest,
    db: AsyncSession = Depends(get_db),
    resume_spawner: ResumeSpawner = Depends(get_resume_spawner),
) -> schemas.InterviewResumeRead:
    return await service.submit_answers(db, launch_id, body, resume_spawner)
