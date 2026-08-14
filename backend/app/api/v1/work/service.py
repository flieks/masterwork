"""Work-item business logic: source CRUD, sync orchestration, and the
session-start prompt assembly.

Session launch is deferred (see plan.md): this backend has no reusable
session-launch path, so `start_work_item` returns the assembled prompt with
`launched=False` and links a `work_item_sessions` row with `session_id=None`.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.work import schemas, serializers
from app.core.exceptions import (
    InvalidWorkSourceError,
    WorkItemNotFoundError,
    WorkSourceNotFoundError,
    WorkSyncError,
)
from app.db.models.work import KIND_SPAWNED, WorkItem, WorkSource
from app.providers.azuredevops import AzureDevOpsClient, AzureDevOpsError
from app.repositories import work as work_repo
from app.services import work_sync

_ORG_URL_RE = re.compile(r"^https://dev\.azure\.com/[A-Za-z0-9._~-]+/?$")


def _parse_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        return None


async def get_source_or_404(db: AsyncSession, source_id: str) -> WorkSource:
    parsed = _parse_uuid(source_id)
    source = await work_repo.get_source(db, parsed) if parsed is not None else None
    if source is None:
        raise WorkSourceNotFoundError(f"unknown work source: {source_id}")
    return source


async def get_item_or_404(db: AsyncSession, item_id: int) -> WorkItem:
    item = await work_repo.get_item(db, item_id)
    if item is None:
        raise WorkItemNotFoundError(f"unknown work item: {item_id}")
    return item


async def list_sources(db: AsyncSession) -> list[schemas.WorkSource]:
    sources = await work_repo.list_sources(db)
    return [serializers.work_source_to_schema(s) for s in sources]


async def create_source(
    db: AsyncSession, body: schemas.WorkSourceCreateRequest
) -> schemas.WorkSource:
    if not _ORG_URL_RE.match(body.org_url):
        raise InvalidWorkSourceError(
            f"org_url must look like https://dev.azure.com/<org>, got: {body.org_url}"
        )
    source = await work_repo.create_source(
        db,
        org_url=body.org_url,
        project=body.project,
        team=body.team,
        query_wiql=body.query_wiql,
        secret_ref=body.secret_ref,
    )
    await db.commit()
    return serializers.work_source_to_schema(source)


async def list_items(
    db: AsyncSession, *, source_id: str | None, state: str | None
) -> list[schemas.WorkItem]:
    parsed_source_id: uuid.UUID | None = None
    if source_id is not None:
        parsed_source_id = _parse_uuid(source_id)
        if parsed_source_id is None:
            raise WorkSourceNotFoundError(f"unknown work source: {source_id}")
    items = await work_repo.list_items(db, source_id=parsed_source_id, state=state)
    return [serializers.work_item_to_schema(i) for i in items]


async def sync_source(
    db: AsyncSession,
    source_id: str,
    client_factory: Callable[[WorkSource], AzureDevOpsClient],
) -> schemas.WorkSyncResult:
    """`client_factory` builds the AzureDevOpsClient for one source — the
    route injects it via `get_devops_client_factory`, tests inject a fake."""
    source = await get_source_or_404(db, source_id)
    client = client_factory(source)
    try:
        counts = await work_sync.sync_source(db, source, client)
    except AzureDevOpsError as exc:
        raise WorkSyncError(
            f"sync failed for {source.org_url.rstrip('/')}/{source.project}: {exc}"
        ) from exc
    await db.commit()
    return schemas.WorkSyncResult(
        fetched=counts.fetched, inserted=counts.inserted, updated=counts.updated
    )


def _assemble_prompt(item: WorkItem) -> str:
    lines = [
        f"{item.item_type} #{item.external_id}: {item.title}",
        item.external_url,
        "",
        "## Story",
        item.description_md,
    ]
    if item.acceptance_md:
        lines += ["", "## Acceptance criteria", item.acceptance_md]
    return "\n".join(lines)


async def start_work_item(db: AsyncSession, item_id: int) -> schemas.WorkItemStartResponse:
    item = await get_item_or_404(db, item_id)
    prompt = _assemble_prompt(item)
    link = await work_repo.create_item_session(
        db, work_item_id=item.id, session_id=None, kind=KIND_SPAWNED
    )
    await db.commit()
    return schemas.WorkItemStartResponse(
        prompt=prompt, launched=False, session_id=None, link_id=link.id
    )
