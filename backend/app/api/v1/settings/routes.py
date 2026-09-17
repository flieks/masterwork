"""App settings endpoints: the projects root and the assistant's agent."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_agent_bins, get_db
from app.api.v1.settings import service
from app.api.v1.settings.schemas import AppSettings, AppSettingsUpdateRequest
from app.services.agent_cli import AgentBins

router = APIRouter(tags=["settings"])


@router.get("/settings", response_model=AppSettings, operation_id="getSettings")
async def get_settings(
    db: AsyncSession = Depends(get_db), bins: AgentBins = Depends(get_agent_bins)
) -> AppSettings:
    return await service.read_settings(db, bins)


@router.patch("/settings", response_model=AppSettings, operation_id="updateSettings")
async def update_settings(
    body: AppSettingsUpdateRequest,
    db: AsyncSession = Depends(get_db),
    bins: AgentBins = Depends(get_agent_bins),
) -> AppSettings:
    return await service.update_settings(db, body, bins)
