"""Proposal accept/reject.

Accept applies the file changes in the backend itself (re-validating every path
against the provider roots), then applies the project update (if any), then
validates the resulting linked asset ids. The apply-files-before-validate order
lets one proposal create a new skill file AND link it in the same accept.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.assets import service as asset_service
from app.api.v1.chat import schemas, serializers
from app.config import settings
from app.core.exceptions import ProposalNotFoundError, ProposalNotPendingError
from app.db.models.chat import Proposal
from app.providers.base import Provider, resolve_within_roots
from app.repositories import projects as project_repo
from app.repositories import proposals as proposal_repo
from app.services.file_changes import apply_change
from app.services.skills_git import commit_snapshot

# Failed proposals stay actionable so a transient error (e.g. permissions) can
# be retried; applied/rejected are terminal.
_ACTIONABLE_STATUSES = ("pending", "failed")


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _parse_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        return None


async def _get_proposal_or_404(db: AsyncSession, proposal_id: str) -> Proposal:
    parsed = _parse_uuid(proposal_id)
    proposal = await proposal_repo.get_proposal(db, parsed) if parsed is not None else None
    if proposal is None:
        raise ProposalNotFoundError(f"unknown proposal: {proposal_id}")
    return proposal


async def _fail(db: AsyncSession, proposal: Proposal, error: str) -> schemas.Proposal:
    proposal.status = "failed"
    proposal.error = error
    await db.commit()
    return serializers.proposal_to_schema(proposal)


def _project_fail_detail(proposal: Proposal, detail: str) -> str:
    # File changes are already on disk by the time the project update runs.
    prefix = "file changes were applied; " if proposal.changes else ""
    return f"{prefix}project update failed: {detail}"


async def _apply_project_update(
    db: AsyncSession,
    providers: list[Provider],
    proposal: Proposal,
    project_update: dict[str, Any],
) -> schemas.Proposal | None:
    """Apply the proposal's project update, or return a failed Proposal.

    Validation happens BEFORE any mutation so a rejected update leaves the
    project untouched (while any already-applied file changes stay applied).
    """
    raw_project_id = project_update.get("project_id")
    project_id = _parse_uuid(str(raw_project_id))
    project = await project_repo.get_project(db, project_id) if project_id is not None else None
    if project is None:
        return await _fail(
            db,
            proposal,
            _project_fail_detail(proposal, f"project not found: {raw_project_id}"),
        )

    # asset_ids is a full replace when present, else the current list is kept.
    new_asset_ids = project_update.get("asset_ids")
    final_asset_ids = list(new_asset_ids) if new_asset_ids is not None else list(project.asset_ids)
    existing = asset_service.existing_asset_ids(providers)
    unknown = [aid for aid in final_asset_ids if aid not in existing]
    if unknown:
        return await _fail(
            db,
            proposal,
            _project_fail_detail(proposal, f"unknown asset ids: {', '.join(unknown)}"),
        )

    # Partial apply — null on any field means leave it unchanged.
    if project_update.get("name") is not None:
        project.name = project_update["name"]
    if project_update.get("goal") is not None:
        project.goal = project_update["goal"]
    if project_update.get("flow_mermaid") is not None:
        project.flow_mermaid = project_update["flow_mermaid"]
    if new_asset_ids is not None:
        project.asset_ids = list(new_asset_ids)
    project.updated_at = _utcnow()
    return None


async def accept_proposal(
    db: AsyncSession, providers: list[Provider], proposal_id: str
) -> schemas.Proposal:
    proposal = await _get_proposal_or_404(db, proposal_id)
    if proposal.status not in _ACTIONABLE_STATUSES:
        raise ProposalNotPendingError(f"proposal is '{proposal.status}', not pending or failed")

    roots = [root for provider in providers for root in provider.roots()]

    # Validate every path before writing anything.
    resolved_paths: list[Path] = []
    for change in proposal.changes:
        resolved = resolve_within_roots(Path(str(change.get("path"))), roots)
        if resolved is None:
            return await _fail(db, proposal, f"path outside allowed roots: {change.get('path')}")
        resolved_paths.append(resolved)

    # Apply file changes first (so a newly-created asset can then be linked).
    for change, resolved in zip(proposal.changes, resolved_paths, strict=True):
        try:
            apply_change(change, resolved)
        except (OSError, ValueError) as exc:
            return await _fail(db, proposal, f"{change.get('path')}: {exc}")

    # Then the project update, validating the resulting asset ids against disk.
    if proposal.project_update is not None:
        failure = await _apply_project_update(db, providers, proposal, proposal.project_update)
        if failure is not None:
            return failure

    proposal.status = "applied"
    proposal.error = None
    proposal.applied_at = _utcnow()
    await db.commit()
    await commit_snapshot(
        settings.claude_skills_root.parent,
        f"masterwork: accept proposal: {(proposal.summary or '')[:72]}",
    )
    return serializers.proposal_to_schema(proposal)


async def reject_proposal(db: AsyncSession, proposal_id: str) -> schemas.Proposal:
    proposal = await _get_proposal_or_404(db, proposal_id)
    if proposal.status not in _ACTIONABLE_STATUSES:
        raise ProposalNotPendingError(f"proposal is '{proposal.status}', not pending or failed")
    proposal.status = "rejected"
    await db.commit()
    return serializers.proposal_to_schema(proposal)
