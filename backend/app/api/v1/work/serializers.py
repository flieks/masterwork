"""Map WorkSource/WorkItem ORM rows to the contract's Pydantic schemas.

Explicit, not `model_validate(row)` with `from_attributes=True`: the pk/fk
columns are `uuid.UUID`, and Pydantic v2 does not coerce UUID to `str` for a
`str`-typed field — it raises. `str(row.id)` here is the whole fix.
"""

from __future__ import annotations

from app.api.v1.work import schemas
from app.db.models.work import WorkItem, WorkSource


def work_source_to_schema(source: WorkSource) -> schemas.WorkSource:
    return schemas.WorkSource(
        id=str(source.id),
        provider=source.provider,
        org_url=source.org_url,
        project=source.project,
        team=source.team,
        query_wiql=source.query_wiql,
        secret_ref=source.secret_ref,
        last_sync_at=source.last_sync_at,
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


def work_item_to_schema(item: WorkItem) -> schemas.WorkItem:
    return schemas.WorkItem(
        id=item.id,
        source_id=str(item.source_id),
        external_id=item.external_id,
        external_url=item.external_url,
        item_type=item.item_type,
        title=item.title,
        description_md=item.description_md,
        acceptance_md=item.acceptance_md,
        state=item.state,
        iteration=item.iteration,
        priority=item.priority,
        tags=list(item.tags) if item.tags is not None else None,
        external_changed_at=item.external_changed_at,
        synced_at=item.synced_at,
    )
