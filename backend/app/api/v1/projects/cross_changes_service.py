"""Cross-project change alerts.

A project's score was computed against its linked asset files as they were at
its last completed run. Other projects (and global chats) edit the same shared
files; this lists those edits so the UI can flag "your score may be stale —
re-run". Pure DB read, no claude call.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.projects import schemas, service
from app.repositories import projects as project_repo
from app.repositories import proposals as proposal_repo
from app.repositories import simulations as simulation_repo


def _parse_applied_at(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


async def list_cross_changes(
    db: AsyncSession, project_id: str
) -> schemas.ProjectCrossChangesResponse:
    project = await service.get_project_or_404(db, project_id)
    baseline = await simulation_repo.latest_completed_for_project(db, project.id)
    since = baseline.completed_at if baseline is not None else None
    if since is None:  # no scored run yet — there is no score to invalidate
        return schemas.ProjectCrossChangesResponse(since=None, changes=[])

    linked = set(project.asset_ids)
    names = {p.id: p.name for p in await project_repo.list_projects(db)}
    changes: list[schemas.CrossChange] = []

    def add(
        change: dict[str, Any],
        *,
        source: str,
        owner_id: uuid.UUID | None,
        title: str,
        applied_at: datetime,
    ) -> None:
        asset_id = change.get("asset_id")
        action = change.get("action")
        # `link`/`unlink` only alter the other project's own toolkit, not the file.
        if asset_id in linked and action in ("update", "create", "delete"):
            changes.append(
                schemas.CrossChange(
                    asset_id=str(asset_id),
                    action=str(action),
                    source=source,
                    project_id=str(owner_id) if owner_id else None,
                    project_name=names.get(owner_id) if owner_id else None,
                    title=title,
                    applied_at=applied_at,
                )
            )

    for simulation in await simulation_repo.list_completed_excluding_project(db, project.id):
        for suggestion in simulation.suggestions or []:
            applied_at = _parse_applied_at(suggestion.get("applied_at"))
            if suggestion.get("status") != "applied" or applied_at is None or applied_at <= since:
                continue
            for change in suggestion.get("changes") or []:
                add(
                    change,
                    source="simulation",
                    owner_id=simulation.project_id,
                    title=str(suggestion.get("title") or ""),
                    applied_at=applied_at,
                )

    for proposal, owner_id in await proposal_repo.list_applied_excluding_project(db, project.id):
        if proposal.applied_at is None or proposal.applied_at <= since:
            continue
        for change in proposal.changes or []:
            add(
                change,
                source="proposal",
                owner_id=owner_id,
                title=proposal.summary or "",
                applied_at=proposal.applied_at,
            )

    changes.sort(key=lambda c: c.applied_at, reverse=True)
    return schemas.ProjectCrossChangesResponse(since=since, changes=changes)
