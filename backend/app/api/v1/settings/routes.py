"""App settings endpoints: read and update the projects_root config."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.v1.settings import service
from app.api.v1.settings.schemas import AppSettings, AppSettingsUpdateRequest

router = APIRouter(tags=["settings"])


@router.get("/settings", response_model=AppSettings, operation_id="getSettings")
async def get_settings(db: AsyncSession = Depends(get_db)) -> AppSettings:
    return await service.read_settings(db)


@router.patch("/settings", response_model=AppSettings, operation_id="updateSettings")
async def update_settings(
    body: AppSettingsUpdateRequest, db: AsyncSession = Depends(get_db)
) -> AppSettings:
    return await service.update_settings(db, body)
