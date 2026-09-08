"""Skill catalog endpoints: search, preview, install, uninstall, and the
upstream drift check + update for what was installed."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_providers, get_skill_catalog_transport
from app.api.v1.skills import schemas, service
from app.config import settings
from app.providers.base import Provider

router = APIRouter(tags=["skills"])


@router.get(
    "/skills/catalog",
    response_model=schemas.CatalogSearchResponse,
    operation_id="searchSkillCatalog",
)
async def search_skill_catalog(
    q: str = Query(..., min_length=1, description="Search text."),
    limit: int = Query(25, ge=1, le=100),
    transport: httpx.AsyncBaseTransport | None = Depends(get_skill_catalog_transport),
) -> schemas.CatalogSearchResponse:
    return await service.search_catalog(
        q, limit, skills_root=settings.claude_skills_root, transport=transport
    )


@router.get(
    "/skills/catalog/{owner}/{repo}/{skill}",
    response_model=schemas.CatalogSkillDetail,
    operation_id="getCatalogSkill",
)
async def get_catalog_skill(
    owner: str,
    repo: str,
    skill: str,
    db: AsyncSession = Depends(get_db),
    transport: httpx.AsyncBaseTransport | None = Depends(get_skill_catalog_transport),
) -> schemas.CatalogSkillDetail:
    return await service.get_catalog_skill(
        db, owner, repo, skill, skills_root=settings.claude_skills_root, transport=transport
    )


@router.post(
    "/skills/install",
    response_model=schemas.InstalledSkill,
    operation_id="installSkill",
)
async def install_skill(
    body: schemas.SkillInstallRequest,
    db: AsyncSession = Depends(get_db),
    transport: httpx.AsyncBaseTransport | None = Depends(get_skill_catalog_transport),
) -> schemas.InstalledSkill:
    return await service.install_skill(
        db,
        body.owner,
        body.repo,
        body.skill,
        overwrite=body.overwrite,
        skills_root=settings.claude_skills_root,
        transport=transport,
    )


@router.get(
    "/skills/installed",
    response_model=list[schemas.InstalledSkill],
    operation_id="listInstalledSkills",
)
async def list_installed_skills(db: AsyncSession = Depends(get_db)) -> list[schemas.InstalledSkill]:
    return await service.list_installed(db)


@router.post(
    "/skills/installed/{name}/check",
    response_model=schemas.UpstreamCheckResult,
    operation_id="checkSkillUpstream",
)
async def check_skill_upstream(
    name: str,
    db: AsyncSession = Depends(get_db),
    transport: httpx.AsyncBaseTransport | None = Depends(get_skill_catalog_transport),
) -> schemas.UpstreamCheckResult:
    return await service.check_upstream(
        db, name, skills_root=settings.claude_skills_root, transport=transport
    )


@router.post(
    "/skills/installed/{name}/update",
    response_model=schemas.InstalledSkill,
    operation_id="updateSkillFromUpstream",
)
async def update_skill_from_upstream(
    name: str,
    body: schemas.SkillUpdateRequest,
    db: AsyncSession = Depends(get_db),
    providers: list[Provider] = Depends(get_providers),
    transport: httpx.AsyncBaseTransport | None = Depends(get_skill_catalog_transport),
) -> schemas.InstalledSkill:
    return await service.update_from_upstream(
        db,
        providers,
        name,
        force=body.force,
        skills_root=settings.claude_skills_root,
        transport=transport,
    )


@router.delete(
    "/skills/installed/{name}",
    response_model=None,
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="uninstallSkill",
)
async def uninstall_skill(name: str, db: AsyncSession = Depends(get_db)) -> None:
    await service.uninstall_skill(db, name, skills_root=settings.claude_skills_root)
