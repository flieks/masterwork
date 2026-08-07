"""Project endpoints: CRUD with partial-update PATCH semantics."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_light_runner, get_providers, get_simulation_runner
from app.api.v1.projects import (
    cross_changes_service,
    generality_service,
    links_service,
    schemas,
    service,
    summary_service,
    trigger_service,
)
from app.providers.base import Provider
from app.services.claude_runner import ClaudeRunner

router = APIRouter(tags=["projects"])


@router.get("/projects", response_model=list[schemas.Project], operation_id="listProjects")
async def list_projects(db: AsyncSession = Depends(get_db)) -> list[schemas.Project]:
    return await service.list_projects(db)


@router.post(
    "/projects",
    response_model=schemas.Project,
    status_code=status.HTTP_201_CREATED,
    operation_id="createProject",
)
async def create_project(
    body: schemas.ProjectCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> schemas.Project:
    return await service.create_project(db, body)


@router.get("/projects/{project_id}", response_model=schemas.Project, operation_id="getProject")
async def get_project(
    project_id: str,
    db: AsyncSession = Depends(get_db),
) -> schemas.Project:
    return await service.get_project(db, project_id)


@router.patch(
    "/projects/{project_id}",
    response_model=schemas.Project,
    operation_id="updateProject",
)
async def update_project(
    project_id: str,
    body: schemas.ProjectUpdateRequest,
    db: AsyncSession = Depends(get_db),
) -> schemas.Project:
    return await service.update_project(db, project_id, body)


@router.post(
    "/projects/{project_id}/summary",
    response_model=schemas.ProjectSummaryResponse,
    operation_id="generateProjectSummary",
)
async def generate_summary(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    runner: ClaudeRunner = Depends(get_light_runner),
) -> schemas.ProjectSummaryResponse:
    return await summary_service.generate_summary(db, runner, project_id)


@router.get(
    "/projects/{project_id}/cross-changes",
    response_model=schemas.ProjectCrossChangesResponse,
    operation_id="listProjectCrossChanges",
)
async def list_cross_changes(
    project_id: str,
    db: AsyncSession = Depends(get_db),
) -> schemas.ProjectCrossChangesResponse:
    return await cross_changes_service.list_cross_changes(db, project_id)


@router.post(
    "/projects/{project_id}/suggest-links",
    response_model=schemas.ProjectSuggestLinksResponse,
    operation_id="suggestProjectLinks",
)
async def suggest_links(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    providers: list[Provider] = Depends(get_providers),
    # Simulation runner: the model reads shortlisted asset files first.
    runner: ClaudeRunner = Depends(get_simulation_runner),
) -> schemas.ProjectSuggestLinksResponse:
    return await links_service.suggest_links(db, providers, runner, project_id)


@router.post(
    "/projects/{project_id}/trigger",
    response_model=schemas.ProjectTriggerResponse,
    operation_id="generateProjectTrigger",
)
async def generate_trigger(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    providers: list[Provider] = Depends(get_providers),
    # Simulation runner: the model reads every linked asset file first.
    runner: ClaudeRunner = Depends(get_simulation_runner),
) -> schemas.ProjectTriggerResponse:
    return await trigger_service.generate_trigger_guide(db, providers, runner, project_id)


@router.post(
    "/projects/{project_id}/generality-audit",
    response_model=schemas.ProjectGeneralityResponse,
    operation_id="auditProjectGenerality",
)
async def audit_generality(
    project_id: str,
    db: AsyncSession = Depends(get_db),
    providers: list[Provider] = Depends(get_providers),
    # Simulation runner: the model reads every linked asset file first.
    runner: ClaudeRunner = Depends(get_simulation_runner),
) -> schemas.ProjectGeneralityResponse:
    return await generality_service.generate_generality_report(db, providers, runner, project_id)


@router.delete(
    "/projects/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteProject",
)
async def delete_project(
    project_id: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    await service.delete_project(db, project_id)
