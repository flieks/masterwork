"""Skill catalog business logic: search, preview, install, uninstall.

Dataclass -> schema mapping lives here as small `_to_*` helpers — this surface
has no separate serializers.py, matching app/api/v1/assets/service.py.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
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
    UpstreamCheckResult,
    UpstreamFileChange,
)
from app.core.exceptions import (
    InstalledSkillNotFoundError,
    SkillAlreadyInstalledError,
    SkillLocallyEditedError,
    SkillNotFoundError,
)
from app.db.models.skills import InstalledSkill as InstalledSkillRow
from app.providers.base import Provider
from app.providers.claude import parse_frontmatter
from app.repositories import skills as skills_repo
from app.services import skill_catalog, skill_install
from app.services.asset_history import prepare_snapshots, snapshot_writes
from app.services.skill_catalog import CatalogSkill as CatalogSkillData
from app.services.skill_catalog import FetchedSkill, SkillHistory, SourceError
from app.services.skill_install import (
    DriftStatus,
    SkillLocation,
    SkillRoots,
    SkillTarget,
    primary_location,
    skill_locations,
)

ASSET_KIND = "skill"


def _to_catalog_skill(skill: CatalogSkillData, *, installed_in: list[SkillTarget]) -> CatalogSkill:
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
        installed=bool(installed_in),
        installed_in=installed_in,
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


def _skill_url(owner: str, repo: str, root_path: str | None) -> str:
    base = f"https://github.com/{owner}/{repo}"
    return f"{base}/tree/HEAD/{root_path}" if root_path else base


def _row_target(row: InstalledSkillRow) -> SkillTarget:
    try:
        return SkillTarget(row.target)
    except ValueError:
        return SkillTarget.claude


def _location_of(row: InstalledSkillRow, roots: SkillRoots) -> SkillLocation | None:
    """The copy this install row governs: the one in the folder it was written
    to, or wherever it moved since (a migrate makes it generic)."""
    return primary_location(skill_locations(row.name, roots), _row_target(row))


def _to_installed(row: InstalledSkillRow, roots: SkillRoots) -> InstalledSkill:
    location = _location_of(row, roots)
    provider = (location.target if location else _row_target(row)).value
    return InstalledSkill(
        asset_id=f"{provider}:{ASSET_KIND}:{row.name}",
        target=_row_target(row),
        location=location.target if location else None,
        name=row.name,
        owner=row.owner,
        repo=row.repo,
        license=row.license,
        registry=SkillRegistry(row.source_registry),
        installed_at=row.installed_at,
        source_url=_skill_url(row.owner, row.repo, row.root_path),
        installed_sha=row.installed_sha,
        root_path=row.root_path,
        last_checked_at=row.last_checked_at,
        upstream_sha=row.upstream_sha,
        drift_status=DriftStatus(row.drift_status) if row.drift_status else None,
    )


async def search_catalog(
    query: str,
    limit: int,
    *,
    roots: SkillRoots,
    transport: httpx.AsyncBaseTransport | None = None,
) -> CatalogSearchResponse:
    result = await skill_catalog.search_catalog(query, limit, transport=transport)
    on_disk = skill_install.installed_anywhere(roots)
    return CatalogSearchResponse(
        skills=[_to_catalog_skill(s, installed_in=on_disk.get(s.skill, [])) for s in result.skills],
        errors=[_to_source_error(e) for e in result.errors],
        search_type=result.search_type,
    )


async def get_catalog_skill(
    db: AsyncSession,
    owner: str,
    repo: str,
    skill: str,
    *,
    roots: SkillRoots,
    transport: httpx.AsyncBaseTransport | None = None,
) -> CatalogSkillDetail:
    fetched, license_id = await asyncio.gather(
        skill_catalog.fetch_skill(owner, repo, skill, transport=transport),
        skill_catalog.resolve_license(owner, repo, transport=transport),
    )
    # Needs the folder fetch_skill resolved, so it cannot join the gather above.
    history = await skill_catalog.fetch_history(owner, repo, fetched.root_path, transport=transport)
    row = await skills_repo.get_installed(db, skill)
    locations = skill_locations(skill, roots)
    location = primary_location(locations, _row_target(row) if row else None)
    local_md = _read_skill_md(location.folder) if location else None
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
        installed=bool(locations),
        installed_in=[loc.target for loc in locations],
        installed_by_masterwork=row is not None,
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
    target: SkillTarget = SkillTarget.claude,
    roots: SkillRoots,
    transport: httpx.AsyncBaseTransport | None = None,
) -> InstalledSkill:
    skill_install.check_installable(owner, repo, skill)
    skill_install.validate_slug(skill)
    locations = skill_locations(skill, roots)
    if locations and not overwrite:
        where = ", ".join(str(loc.entry) for loc in locations)
        raise SkillAlreadyInstalledError(f"{skill} is already installed ({where})")
    # An overwrite replaces the copy where it lives, so it never leaves a twin behind.
    existing = primary_location(locations, target)
    if existing is not None:
        skill_install.ensure_managed(existing, skill)

    fetched, license_id = await asyncio.gather(
        skill_catalog.fetch_skill(owner, repo, skill, transport=transport),
        skill_catalog.resolve_license(owner, repo, transport=transport),
    )
    # Needs the folder fetch_skill resolved; None on failure, and the install goes ahead.
    sha = await skill_catalog.fetch_head_sha(owner, repo, fetched.root_path, transport=transport)
    written_to = existing.target if existing else target
    folder = existing.folder if existing else roots.root(target) / skill
    skill_install.write_skill_folder(fetched, slug=skill, folder=folder, overwrite=overwrite)
    if existing is None and target is SkillTarget.generic:
        skill_install.link_into_agents(skill, folder, roots)

    registry = _inferred_registry(owner, repo, skill)
    row = await skills_repo.upsert_installed(
        db,
        name=skill,
        owner=owner,
        repo=repo,
        license=license_id,
        registry=registry.value,
        installed_sha=sha,
        installed_tree_hash=_disk_hash(folder),
        root_path=fetched.root_path,
        target=written_to.value,
    )
    await db.commit()
    return _to_installed(row, roots)


def _disk_hash(folder: Path) -> str | None:
    """Hashed from what landed on disk, not from the fetch, so the baseline is
    exactly what a later local-edit check will re-read."""
    if not folder.is_dir():
        return None
    return skill_install.tree_hash(skill_install.read_folder_files(folder))


def _read_skill_md(folder: Path) -> str | None:
    try:
        return (folder / "SKILL.md").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


async def list_installed(db: AsyncSession, roots: SkillRoots) -> list[InstalledSkill]:
    return [_to_installed(row, roots) for row in await skills_repo.list_installed(db)]


@dataclass(frozen=True)
class _Comparison:
    fetched: FetchedSkill
    history: SkillHistory
    local: dict[str, bytes]
    status: DriftStatus


async def _compare_with_upstream(
    row: InstalledSkillRow,
    *,
    location: SkillLocation | None,
    transport: httpx.AsyncBaseTransport | None,
) -> _Comparison:
    """Re-fetch the folder the install came from and classify the drift.
    Raises SkillNotFoundError when upstream no longer has it."""
    # The recorded root_path is the folder itself; fetch_skill resolves it
    # directly, and a row older than the column falls back to the slug lookup.
    fetched = await skill_catalog.fetch_skill(
        row.owner, row.repo, row.root_path or row.name, transport=transport
    )
    history = await skill_catalog.fetch_history(
        row.owner, row.repo, fetched.root_path, transport=transport
    )
    local = skill_install.read_folder_files(location.folder) if location else {}
    status = skill_install.classify_drift(
        local_hash=skill_install.tree_hash(local),
        upstream_hash=skill_install.tree_hash(skill_install.fetched_files(fetched)),
        installed_hash=row.installed_tree_hash,
        installed_sha=row.installed_sha,
        upstream_sha=history.last_commit_sha,
    )
    return _Comparison(fetched=fetched, history=history, local=local, status=status)


async def check_upstream(
    db: AsyncSession,
    name: str,
    *,
    roots: SkillRoots,
    transport: httpx.AsyncBaseTransport | None = None,
) -> UpstreamCheckResult:
    now = datetime.now(UTC)
    row = await skills_repo.get_installed(db, name)
    if row is None:
        return UpstreamCheckResult(
            name=name,
            status=DriftStatus.unknown_origin,
            checked_at=now,
            skill_md_diff="",
            other_changes=[],
        )

    try:
        cmp = await _compare_with_upstream(
            row, location=_location_of(row, roots), transport=transport
        )
    except SkillNotFoundError:
        await skills_repo.record_check(
            db, row, checked_at=now, upstream_sha=None, status=DriftStatus.unknown_origin.value
        )
        await db.commit()
        return UpstreamCheckResult(
            name=name,
            status=DriftStatus.unknown_origin,
            checked_at=now,
            source_url=_skill_url(row.owner, row.repo, row.root_path),
            installed_sha=row.installed_sha,
            skill_md_diff="",
            other_changes=[],
        )

    upstream = skill_install.fetched_files(cmp.fetched)
    await skills_repo.record_check(
        db, row, checked_at=now, upstream_sha=cmp.history.last_commit_sha, status=cmp.status.value
    )
    await db.commit()
    return UpstreamCheckResult(
        name=name,
        status=cmp.status,
        checked_at=now,
        source_url=_skill_url(row.owner, row.repo, cmp.fetched.root_path),
        installed_sha=row.installed_sha,
        upstream_sha=cmp.history.last_commit_sha,
        upstream_last_modified_at=cmp.history.last_modified_at,
        upstream_last_change_summary=cmp.history.last_change_summary,
        skill_md_diff=skill_install.skill_md_diff(cmp.local.get("SKILL.md"), upstream["SKILL.md"]),
        other_changes=[
            UpstreamFileChange(path=c.path, change=c.change)
            for c in skill_install.changed_paths(cmp.local, upstream)
        ],
    )


async def update_from_upstream(
    db: AsyncSession,
    providers: list[Provider],
    name: str,
    *,
    force: bool,
    roots: SkillRoots,
    transport: httpx.AsyncBaseTransport | None = None,
) -> InstalledSkill:
    row = await skills_repo.get_installed(db, name)
    if row is None:
        raise InstalledSkillNotFoundError(f"no installed skill named {name!r}")
    location = _location_of(row, roots)
    if location is not None:
        skill_install.ensure_managed(location, name)

    # A vanished upstream propagates as the 404 it is; the update has no source.
    cmp = await _compare_with_upstream(row, location=location, transport=transport)
    if cmp.status in (DriftStatus.edited_locally, DriftStatus.diverged) and not force:
        raise SkillLocallyEditedError(
            f"{name} was edited locally since it was installed; updating would overwrite"
            " those edits. Pass force to replace them with the upstream copy."
        )

    # The real folder, even when the agent folders only link to it: the links stay.
    target = location.target if location else _row_target(row)
    folder = location.folder if location else roots.root(target) / name
    touched = [folder / "SKILL.md"]
    await prepare_snapshots(providers, touched)
    skill_install.write_skill_folder(cmp.fetched, slug=name, folder=folder, overwrite=True)
    await snapshot_writes(providers, touched, f"masterwork: update skill from upstream: {name}")

    row.installed_sha = cmp.history.last_commit_sha
    row.installed_tree_hash = _disk_hash(folder)
    row.target = target.value
    row.root_path = cmp.fetched.root_path
    await skills_repo.record_check(
        db,
        row,
        checked_at=datetime.now(UTC),
        upstream_sha=cmp.history.last_commit_sha,
        status=DriftStatus.current.value,
    )
    await db.commit()
    return _to_installed(row, roots)


async def uninstall_skill(db: AsyncSession, name: str, *, roots: SkillRoots) -> None:
    """Remove the copy the row governs — for a generic skill, its folder and the
    links the agent folders hold to it. A copy in another folder is left alone."""
    row = await skills_repo.get_installed(db, name)
    if row is None:
        raise InstalledSkillNotFoundError(f"no installed skill named {name!r}")
    skill_install.validate_slug(name)
    location = _location_of(row, roots)
    if location is not None:
        skill_install.remove_location(name, location, roots)
    await skills_repo.delete_installed(db, name)
    await db.commit()
