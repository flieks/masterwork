"""Data access for the Azure DevOps work-item mirror."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.work import (
    WorkItem,
    WorkItemSession,
    WorkPrThread,
    WorkPullRequest,
    WorkRepoPath,
    WorkSource,
)


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
    assigned_to: str | None,
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
        select(WorkItem).where(WorkItem.source_id == source_id, WorkItem.external_id == external_id)
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
                assigned_to=assigned_to,
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
    item.assigned_to = assigned_to
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


# --- pull requests -----------------------------------------------------


async def list_prs(db: AsyncSession, *, source_id: uuid.UUID | None) -> list[WorkPullRequest]:
    query = select(WorkPullRequest)
    if source_id is not None:
        query = query.where(WorkPullRequest.source_id == source_id)
    result = await db.execute(query.order_by(WorkPullRequest.external_changed_at.desc()))
    return list(result.scalars().all())


async def get_pr(db: AsyncSession, pr_id: int) -> WorkPullRequest | None:
    return await db.get(WorkPullRequest, pr_id)


async def upsert_pr(
    db: AsyncSession,
    *,
    source_id: uuid.UUID,
    external_id: int,
    repository_id: str,
    repository_name: str,
    repository_remote_url: str,
    title: str,
    description: str,
    source_branch: str,
    target_branch: str,
    status: str,
    is_draft: bool,
    created_by: str | None,
    external_url: str,
    raw: dict[str, Any],
    external_changed_at: datetime,
    synced_at: datetime,
) -> bool:
    """Select-then-insert-or-update on (source_id, external_id), same shape as
    `upsert_item`. Returns True when a new row was inserted, False on update."""
    result = await db.execute(
        select(WorkPullRequest).where(
            WorkPullRequest.source_id == source_id, WorkPullRequest.external_id == external_id
        )
    )
    pr = result.scalar_one_or_none()
    if pr is None:
        db.add(
            WorkPullRequest(
                source_id=source_id,
                external_id=external_id,
                repository_id=repository_id,
                repository_name=repository_name,
                repository_remote_url=repository_remote_url,
                title=title,
                description=description,
                source_branch=source_branch,
                target_branch=target_branch,
                status=status,
                is_draft=is_draft,
                created_by=created_by,
                external_url=external_url,
                raw=raw,
                external_changed_at=external_changed_at,
                synced_at=synced_at,
            )
        )
        await db.flush()
        return True

    pr.repository_id = repository_id
    pr.repository_name = repository_name
    pr.repository_remote_url = repository_remote_url
    pr.title = title
    pr.description = description
    pr.source_branch = source_branch
    pr.target_branch = target_branch
    pr.status = status
    pr.is_draft = is_draft
    pr.created_by = created_by
    pr.external_url = external_url
    pr.raw = raw
    pr.external_changed_at = external_changed_at
    pr.synced_at = synced_at
    await db.flush()
    return False


async def list_pr_threads(db: AsyncSession, pull_request_id: int) -> list[WorkPrThread]:
    result = await db.execute(
        select(WorkPrThread)
        .where(WorkPrThread.pull_request_id == pull_request_id)
        .order_by(WorkPrThread.external_id)
    )
    return list(result.scalars().all())


async def upsert_pr_thread(
    db: AsyncSession,
    *,
    pull_request_id: int,
    external_id: int,
    status: str | None,
    is_resolved: bool,
    file_path: str | None,
    right_file_line: int | None,
    comments: list[dict[str, Any]],
    raw: dict[str, Any],
    synced_at: datetime,
) -> bool:
    """Select-then-insert-or-update on (pull_request_id, external_id)."""
    result = await db.execute(
        select(WorkPrThread).where(
            WorkPrThread.pull_request_id == pull_request_id,
            WorkPrThread.external_id == external_id,
        )
    )
    thread = result.scalar_one_or_none()
    if thread is None:
        db.add(
            WorkPrThread(
                pull_request_id=pull_request_id,
                external_id=external_id,
                status=status,
                is_resolved=is_resolved,
                file_path=file_path,
                right_file_line=right_file_line,
                comments=comments,
                raw=raw,
                synced_at=synced_at,
            )
        )
        await db.flush()
        return True

    thread.status = status
    thread.is_resolved = is_resolved
    thread.file_path = file_path
    thread.right_file_line = right_file_line
    thread.comments = comments
    thread.raw = raw
    thread.synced_at = synced_at
    await db.flush()
    return False


# --- repo paths ----------------------------------------------------------


async def get_repo_path(db: AsyncSession, remote_url: str) -> WorkRepoPath | None:
    result = await db.execute(select(WorkRepoPath).where(WorkRepoPath.remote_url == remote_url))
    return result.scalar_one_or_none()


async def upsert_repo_path(db: AsyncSession, *, remote_url: str, local_path: str) -> WorkRepoPath:
    """Select-then-insert-or-update on the normalized remote_url — a re-save
    of an already-known remote updates the path rather than duplicating."""
    existing = await get_repo_path(db, remote_url)
    if existing is None:
        row = WorkRepoPath(remote_url=remote_url, local_path=local_path)
        db.add(row)
        await db.flush()
        await db.refresh(row)
        return row
    existing.local_path = local_path
    await db.flush()
    return existing
