"""Data access for the Azure DevOps work-item mirror."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.work import WorkItem, WorkItemSession, WorkSource


async def create_source(
    db: AsyncSession,
    *,
    org_url: str,
    project: str,
    team: str | None,
    query_wiql: str | None,
    secret_ref: str,
) -> WorkSource:
    source = WorkSource(
        org_url=org_url,
        project=project,
        team=team,
        query_wiql=query_wiql,
        secret_ref=secret_ref,
    )
    db.add(source)
    await db.flush()
    await db.refresh(source)
    return source


async def list_sources(db: AsyncSession) -> list[WorkSource]:
    result = await db.execute(select(WorkSource).order_by(WorkSource.created_at.desc()))
    return list(result.scalars().all())


async def get_source(db: AsyncSession, source_id: uuid.UUID) -> WorkSource | None:
    return await db.get(WorkSource, source_id)


async def list_items(
    db: AsyncSession, *, source_id: uuid.UUID | None, state: str | None
) -> list[WorkItem]:
    query = select(WorkItem)
    if source_id is not None:
        query = query.where(WorkItem.source_id == source_id)
    if state is not None:
        query = query.where(WorkItem.state == state)
    result = await db.execute(query.order_by(WorkItem.external_changed_at.desc()))
    return list(result.scalars().all())


async def get_item(db: AsyncSession, item_id: int) -> WorkItem | None:
    return await db.get(WorkItem, item_id)


async def upsert_item(
    db: AsyncSession,
    *,
    source_id: uuid.UUID,
    external_id: int,
    parent_external_id: int | None,
    pulled_as_parent: bool,
    external_url: str,
    item_type: str,
    title: str,
    description_md: str,
    acceptance_md: str | None,
    state: str,
    iteration: str | None,
    priority: int | None,
    tags: list[str] | None,
    raw: dict[str, Any],
    external_changed_at: datetime,
    synced_at: datetime,
) -> bool:
    """Select-then-insert-or-update on (source_id, external_id) rather than a
    dialect-specific ON CONFLICT, so this runs identically on SQLite and
    Postgres. Returns True when a new row was inserted, False on update."""
    result = await db.execute(
        select(WorkItem).where(
            WorkItem.source_id == source_id, WorkItem.external_id == external_id
        )
    )
    item = result.scalar_one_or_none()
    if item is None:
        db.add(
            WorkItem(
                source_id=source_id,
                external_id=external_id,
                parent_external_id=parent_external_id,
                pulled_as_parent=pulled_as_parent,
                external_url=external_url,
                item_type=item_type,
                title=title,
                description_md=description_md,
                acceptance_md=acceptance_md,
                state=state,
                iteration=iteration,
                priority=priority,
                tags=tags,
                raw=raw,
                external_changed_at=external_changed_at,
                synced_at=synced_at,
            )
        )
        await db.flush()
        return True

    item.parent_external_id = parent_external_id
    item.pulled_as_parent = pulled_as_parent
    item.external_url = external_url
    item.item_type = item_type
    item.title = title
    item.description_md = description_md
    item.acceptance_md = acceptance_md
    item.state = state
    item.iteration = iteration
    item.priority = priority
    item.tags = tags
    item.raw = raw
    item.external_changed_at = external_changed_at
    item.synced_at = synced_at
    await db.flush()
    return False


async def create_item_session(
    db: AsyncSession, *, work_item_id: int, session_id: str | None, kind: str
) -> WorkItemSession:
    link = WorkItemSession(work_item_id=work_item_id, session_id=session_id, kind=kind)
    db.add(link)
    await db.flush()
    await db.refresh(link)
    return link
