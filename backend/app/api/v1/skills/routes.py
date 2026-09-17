"""Skill catalog endpoints: search, preview, install, uninstall, and the
upstream drift check + update for what was installed."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_db,
    get_light_runner,
    get_providers,
    get_skill_catalog_transport,
    get_skill_roots,
)
from app.api.v1.skills import match_service, schemas, service
from app.providers.base import Provider
from app.services.agent_runner import AgentRunner
from app.services.skill_install import SkillRoots

router = APIRouter(tags=["skills"])


@router.get(
    "/skills/catalog",
    response_model=schemas.CatalogSearchResponse,
    operation_id="searchSkillCatalog",
)
async def search_skill_catalog(
    q: str = Query(..., min_length=1, description="Search text."),
    limit: int = Query(25, ge=1, le=100),
    roots: SkillRoots = Depends(get_skill_roots),
    transport: httpx.AsyncBaseTransport | None = Depends(get_skill_catalog_transport),
) -> schemas.CatalogSearchResponse:
    return await service.search_catalog(q, limit, roots=roots, transport=transport)


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
    roots: SkillRoots = Depends(get_skill_roots),
    transport: httpx.AsyncBaseTransport | None = Depends(get_skill_catalog_transport),
) -> schemas.CatalogSkillDetail:
    return await service.get_catalog_skill(db, owner, repo, skill, roots=roots, transport=transport)


@router.post(
    "/skills/install",
    response_model=schemas.InstalledSkill,
    operation_id="installSkill",
)
async def install_skill(
    body: schemas.SkillInstallRequest,
    db: AsyncSession = Depends(get_db),
    roots: SkillRoots = Depends(get_skill_roots),
    transport: httpx.AsyncBaseTransport | None = Depends(get_skill_catalog_transport),
) -> schemas.InstalledSkill:
    return await service.install_skill(
        db,
        body.owner,
        body.repo,
        body.skill,
        overwrite=body.overwrite,
        target=body.target,
        roots=roots,
        transport=transport,
    )


@router.get(
    "/skills/installed",
    response_model=list[schemas.InstalledSkill],
    operation_id="listInstalledSkills",
)
async def list_installed_skills(
    db: AsyncSession = Depends(get_db), roots: SkillRoots = Depends(get_skill_roots)
) -> list[schemas.InstalledSkill]:
    return await service.list_installed(db, roots)


@router.post(
    "/skills/installed/match",
    response_model=schemas.SkillMatchResponse,
    operation_id="matchInstalledSkills",
)
async def match_installed_skills(
    body: schemas.SkillMatchRequest,
    providers: list[Provider] = Depends(get_providers),
    runner: AgentRunner = Depends(get_light_runner),
) -> schemas.SkillMatchResponse:
    return await match_service.match_installed(providers, runner, body.query)


@router.post(
    "/skills/installed/{name}/check",
    response_model=schemas.UpstreamCheckResult,
    operation_id="checkSkillUpstream",
)
async def check_skill_upstream(
    name: str,
    db: AsyncSession = Depends(get_db),
    roots: SkillRoots = Depends(get_skill_roots),
    transport: httpx.AsyncBaseTransport | None = Depends(get_skill_catalog_transport),
) -> schemas.UpstreamCheckResult:
    return await service.check_upstream(db, name, roots=roots, transport=transport)


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
    roots: SkillRoots = Depends(get_skill_roots),
    transport: httpx.AsyncBaseTransport | None = Depends(get_skill_catalog_transport),
) -> schemas.InstalledSkill:
    return await service.update_from_upstream(
        db,
        providers,
        name,
        force=body.force,
        roots=roots,
        transport=transport,
    )


@router.delete(
    "/skills/installed/{name}",
    response_model=None,
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="uninstallSkill",
)
async def uninstall_skill(
    name: str,
    db: AsyncSession = Depends(get_db),
    roots: SkillRoots = Depends(get_skill_roots),
) -> None:
    """Removes the copy masterwork installed: for a skill made generic since, its
    ~/.agents/skills folder plus the links the agent folders hold to it."""
    await service.uninstall_skill(db, name, roots=roots)
