"""Asset endpoints: browse, search, read, edit, and diagram installed skills/agents."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_light_runner, get_providers
from app.api.v1.assets import diagram_service, service
from app.api.v1.assets.schemas import (
    AssetDetail,
    AssetDiagram,
    AssetEnabledRequest,
    AssetKind,
    AssetMigrateRequest,
    AssetMigrationResult,
    AssetSummary,
    AssetUpdateRequest,
)
from app.providers.base import Provider
from app.services.claude_runner import ClaudeRunner

router = APIRouter(tags=["assets"])


@router.get("/assets", response_model=list[AssetSummary], operation_id="listAssets")
async def list_assets(
    kind: AssetKind | None = Query(None, description="Filter by asset kind."),
    q: str | None = Query(
        None,
        description="Case-insensitive search over name, title, description, and content.",
    ),
    providers: list[Provider] = Depends(get_providers),
) -> list[AssetSummary]:
    return service.list_assets(providers, kind=kind, q=q)


@router.get("/assets/{asset_id}", response_model=AssetDetail, operation_id="getAsset")
async def get_asset(
    asset_id: str,
    providers: list[Provider] = Depends(get_providers),
) -> AssetDetail:
    return service.get_asset(providers, asset_id)


@router.put("/assets/{asset_id}", response_model=AssetDetail, operation_id="updateAsset")
async def update_asset(
    asset_id: str,
    body: AssetUpdateRequest,
    providers: list[Provider] = Depends(get_providers),
) -> AssetDetail:
    return await service.update_asset(providers, asset_id, body.content)


@router.put(
    "/assets/{asset_id}/enabled",
    response_model=AssetDetail,
    operation_id="setAssetEnabled",
)
async def set_asset_enabled(
    asset_id: str,
    body: AssetEnabledRequest,
    providers: list[Provider] = Depends(get_providers),
) -> AssetDetail:
    return await service.set_asset_enabled(providers, asset_id, enabled=body.enabled)


@router.post(
    "/assets/{asset_id}/migrate",
    response_model=AssetMigrationResult,
    operation_id="migrateAssetToGeneric",
)
async def migrate_asset_to_generic(
    asset_id: str,
    body: AssetMigrateRequest | None = None,
    db: AsyncSession = Depends(get_db),
    providers: list[Provider] = Depends(get_providers),
) -> AssetMigrationResult:
    return await service.migrate_asset(
        db, providers, asset_id, replace_generic=bool(body and body.replace_generic)
    )


@router.get(
    "/assets/{asset_id}/diagram",
    response_model=AssetDiagram,
    operation_id="getAssetDiagram",
)
async def get_asset_diagram(
    asset_id: str,
    db: AsyncSession = Depends(get_db),
    providers: list[Provider] = Depends(get_providers),
) -> AssetDiagram:
    return await diagram_service.get_diagram(db, providers, asset_id)


@router.post(
    "/assets/{asset_id}/diagram",
    response_model=AssetDiagram,
    operation_id="generateAssetDiagram",
)
async def generate_asset_diagram(
    asset_id: str,
    db: AsyncSession = Depends(get_db),
    providers: list[Provider] = Depends(get_providers),
    runner: ClaudeRunner = Depends(get_light_runner),
) -> AssetDiagram:
    return await diagram_service.generate_diagram(db, providers, runner, asset_id)
