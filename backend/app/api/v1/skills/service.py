"""Skill catalog business logic: search, preview, install, uninstall.

Dataclass -> schema mapping lives here as small `_to_*` helpers — this surface
has no separate serializers.py, matching app/api/v1/assets/service.py.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.skills.schemas import (
    CatalogSearchResponse,
    CatalogSkill,
    CatalogSkillDetail,
    CatalogSourceError,
    InstalledSkill,
    SkillRegistry,
)
from app.core.exceptions import InstalledSkillNotFoundError, SkillAlreadyInstalledError
from app.providers.claude import parse_frontmatter
from app.repositories import skills as skills_repo
from app.services import skill_catalog, skill_install
from app.services.skill_catalog import CatalogSkill as CatalogSkillData
from app.services.skill_catalog import SourceError

ASSET_PROVIDER = "claude"
ASSET_KIND = "skill"


def _to_catalog_skill(skill: CatalogSkillData, *, installed: bool) -> CatalogSkill:
    return CatalogSkill(
        owner=skill.owner,
        repo=skill.repo,
        skill=skill.skill,
        name=skill.name,
        description=skill.description,
        registry=SkillRegistry(skill.registry),
        installs=skill.installs,
        license=skill.license,
        license_resolved=skill.license_resolved,
        url=skill.url,
        installed=installed,
    )


def _to_source_error(error: SourceError) -> CatalogSourceError:
    return CatalogSourceError(registry=SkillRegistry(error.registry), message=error.message)


def _display_name(skill_md: str, fallback: str) -> str:
    meta = parse_frontmatter(skill_md)
    name = meta.get("name")
    return name.strip() if isinstance(name, str) and name.strip() else fallback


def _inferred_registry(owner: str, repo: str, skill: str) -> SkillRegistry:
    # A GitHub repo-search hit's `skill` is the repo name itself (see
    # skill_catalog._parse_github_record); skills.sh always carries a real
    # skill-folder slug. Best-effort at preview/install time, when the caller
    # no longer tells us which registry the record came from.
    return SkillRegistry.github if skill == repo else SkillRegistry.skills_sh


def _version_of(skill_md: str) -> str | None:
    """`version`, or `metadata.version` — there is no standard, and most skills
    declare neither, so this is best-effort and often None."""
    meta = parse_frontmatter(skill_md)
    for value in (
        meta.get("version"),
        (meta.get("metadata") or {}).get("version")
        if isinstance(meta.get("metadata"), dict)
        else None,
    ):
        if isinstance(value, (str, int, float)) and str(value).strip():
            return str(value).strip()
    return None


def _skill_url(owner: str, repo: str, root_path: str) -> str:
    base = f"https://github.com/{owner}/{repo}"
    return f"{base}/tree/HEAD/{root_path}" if root_path else base


async def search_catalog(
    query: str,
    limit: int,
    *,
    skills_root: Path,
    transport: httpx.AsyncBaseTransport | None = None,
) -> CatalogSearchResponse:
    result = await skill_catalog.search_catalog(query, limit, transport=transport)
    on_disk = skill_install.installed_slugs(skills_root)
    return CatalogSearchResponse(
        skills=[_to_catalog_skill(s, installed=s.skill in on_disk) for s in result.skills],
        errors=[_to_source_error(e) for e in result.errors],
    )


async def get_catalog_skill(
    db: AsyncSession,
    owner: str,
    repo: str,
    skill: str,
    *,
    skills_root: Path,
    transport: httpx.AsyncBaseTransport | None = None,
) -> CatalogSkillDetail:
    fetched, license_id = await asyncio.gather(
        skill_catalog.fetch_skill(owner, repo, skill, transport=transport),
        skill_catalog.resolve_license(owner, repo, transport=transport),
    )
    # Needs the folder fetch_skill resolved, so it cannot join the gather above.
    history = await skill_catalog.fetch_history(owner, repo, fetched.root_path, transport=transport)
    local_md = skill_install.read_installed_skill_md(skill, skills_root=skills_root)
    return CatalogSkillDetail(
        owner=owner,
        repo=repo,
        skill=skill,
        name=_display_name(fetched.skill_md, skill),
        registry=_inferred_registry(owner, repo, skill),
        license=license_id,
        all_rights_reserved=license_id is None,
        url=_skill_url(owner, repo, fetched.root_path),
        version=_version_of(fetched.skill_md),
        installed_version=_version_of(local_md) if local_md is not None else None,
        created_at=history.created_at,
        last_modified_at=history.last_modified_at,
        last_change_summary=history.last_change_summary,
        differs_from_installed=(
            None if local_md is None else local_md.strip() != fetched.skill_md.strip()
        ),
        installed=skill_install.is_installed(skill, skills_root=skills_root),
        installed_by_masterwork=await skills_repo.get_installed(db, skill) is not None,
        skill_md=fetched.skill_md,
        files=[f.relative_path for f in fetched.files],
    )


async def install_skill(
    db: AsyncSession,
    owner: str,
    repo: str,
    skill: str,
    *,
    overwrite: bool,
    skills_root: Path,
    transport: httpx.AsyncBaseTransport | None = None,
) -> InstalledSkill:
    skill_install.check_installable(owner, repo, skill)
    if not overwrite and skill_install.is_installed(skill, skills_root=skills_root):
        raise SkillAlreadyInstalledError(f"{skill} is already installed")

    fetched, license_id = await asyncio.gather(
        skill_catalog.fetch_skill(owner, repo, skill, transport=transport),
        skill_catalog.resolve_license(owner, repo, transport=transport),
    )
    skill_install.install_skill(fetched, slug=skill, skills_root=skills_root, overwrite=overwrite)

    registry = _inferred_registry(owner, repo, skill)
    row = await skills_repo.upsert_installed(
        db, name=skill, owner=owner, repo=repo, license=license_id, registry=registry.value
    )
    await db.commit()
    return InstalledSkill(
        asset_id=f"{ASSET_PROVIDER}:{ASSET_KIND}:{row.name}",
        name=row.name,
        owner=row.owner,
        repo=row.repo,
        license=row.license,
        registry=SkillRegistry(row.source_registry),
        installed_at=row.installed_at,
    )


async def uninstall_skill(db: AsyncSession, name: str, *, skills_root: Path) -> None:
    row = await skills_repo.get_installed(db, name)
    if row is None:
        raise InstalledSkillNotFoundError(f"no installed skill named {name!r}")
    skill_install.uninstall_skill(name, skills_root=skills_root)
    await skills_repo.delete_installed(db, name)
    await db.commit()
