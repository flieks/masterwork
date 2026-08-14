"""Azure DevOps work-item endpoints: sources, items, sync, session-start prompt."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DevOpsClientFactory, get_db, get_devops_client_factory
from app.api.v1.work import schemas, service

router = APIRouter(tags=["work"])


@router.get(
    "/work/sources", response_model=list[schemas.WorkSource], operation_id="listWorkSources"
)
async def list_work_sources(db: AsyncSession = Depends(get_db)) -> list[schemas.WorkSource]:
    return await service.list_sources(db)


@router.post(
    "/work/sources",
    response_model=schemas.WorkSource,
    status_code=status.HTTP_201_CREATED,
    operation_id="createWorkSource",
)
async def create_work_source(
    body: schemas.WorkSourceCreateRequest,
    db: AsyncSession = Depends(get_db),
) -> schemas.WorkSource:
    return await service.create_source(db, body)


@router.get("/work/items", response_model=list[schemas.WorkItem], operation_id="listWorkItems")
async def list_work_items(
    source_id: str | None = Query(None, description="Scope to one registered source."),
    state: str | None = Query(None, description="Scope to one DevOps state, e.g. Active."),
    db: AsyncSession = Depends(get_db),
) -> list[schemas.WorkItem]:
    return await service.list_items(db, source_id=source_id, state=state)


@router.post(
    "/work/sources/{source_id}/sync",
    response_model=schemas.WorkSyncResult,
    operation_id="syncWorkSource",
)
async def sync_work_source(
    source_id: str,
    db: AsyncSession = Depends(get_db),
    client_factory: DevOpsClientFactory = Depends(get_devops_client_factory),
) -> schemas.WorkSyncResult:
    return await service.sync_source(db, source_id, client_factory)


@router.post(
    "/work/items/{item_id}/start",
    response_model=schemas.WorkItemStartResponse,
    operation_id="startWorkItem",
)
async def start_work_item(
    item_id: int,
    db: AsyncSession = Depends(get_db),
) -> schemas.WorkItemStartResponse:
    return await service.start_work_item(db, item_id)
