"""Sync Azure DevOps work items into the local mirror.

The WIQL/batch payload is untrusted external data: it is stored verbatim in
`raw`, rendered as markdown into `description_md`/`acceptance_md`, and never
executed, `eval`'d, or interpolated into a shell command. The WIQL sent to
DevOps is `DEFAULT_WIQL`, the sprint-scoped query built from the team's own
current iteration path, or the operator-entered `source.query_wiql` — no
*work-item field* is ever concatenated into a query. The iteration path is
the one interpolation that is allowed: it is external data, but it comes from
DevOps' own sprint-lookup API rather than user input, is escaped by doubling
its single quotes (the WIQL string-literal escape), and WIQL has no comment
or statement-separator syntax to break out of a literal with.
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from markdownify import markdownify
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.work import WorkSource
from app.providers.azuredevops import AzureDevOpsClient, AzureDevOpsError
from app.repositories import work as work_repo

DEFAULT_WIQL = (
    "SELECT [System.Id] FROM WorkItems WHERE [System.AssignedTo] = @Me "
    "AND [System.State] NOT IN ('Closed','Removed','Done') "
    "ORDER BY [System.ChangedDate] DESC"
)

SPRINT_WIQL = (
    "SELECT [System.Id] FROM WorkItems WHERE [System.IterationPath] UNDER '{path}' "
    "AND [System.State] NOT IN ('Closed','Removed','Done') "
    "ORDER BY [System.ChangedDate] DESC"
)


def _sprint_wiql(path: str) -> str:
    """WIQL string literals escape a quote by doubling it."""
    return SPRINT_WIQL.format(path=path.replace("'", "''"))


FIELDS = (
    "System.Title",
    "System.Description",
    "Microsoft.VSTS.Common.AcceptanceCriteria",
    "System.State",
    "System.IterationPath",
    "System.WorkItemType",
    "Microsoft.VSTS.Common.Priority",
    "System.Tags",
    "System.ChangedDate",
    "System.Parent",
    "System.AssignedTo",
)

_FIELD_TITLE = "System.Title"
_FIELD_DESCRIPTION = "System.Description"
_FIELD_ACCEPTANCE = "Microsoft.VSTS.Common.AcceptanceCriteria"
_FIELD_STATE = "System.State"
_FIELD_ITERATION = "System.IterationPath"
_FIELD_TYPE = "System.WorkItemType"
_FIELD_PRIORITY = "Microsoft.VSTS.Common.Priority"
_FIELD_TAGS = "System.Tags"
_FIELD_CHANGED = "System.ChangedDate"
_FIELD_PARENT = "System.Parent"
_FIELD_ASSIGNED = "System.AssignedTo"

# Collapse markdownify's trailing-space-before-newline artifacts.
_TRAILING_WHITESPACE = re.compile(r"[ \t]+\n")


@dataclass(frozen=True)
class SyncCounts:
    fetched: int
    inserted: int
    updated: int


def html_to_markdown(html: str | None) -> str:
    """DevOps rich-text fields arrive as HTML; converted here, never rendered as HTML."""
    if not html:
        return ""
    text = markdownify(html, heading_style="ATX").strip()
    return _TRAILING_WHITESPACE.sub("\n", text)


def parse_tags(raw: str | None) -> list[str] | None:
    """DevOps sends `System.Tags` as `"a; b; c"` — split, strip, drop empties."""
    if not raw:
        return None
    tags = [tag.strip() for tag in raw.split(";") if tag.strip()]
    return tags or None


def parse_assigned_to(raw: Any) -> str | None:
    """`System.AssignedTo` is an identity object; keep the display name only."""
    if not isinstance(raw, dict):
        return None
    name = raw.get("displayName")
    return name if isinstance(name, str) and name else None


def _work_item_url(source: WorkSource, external_id: int) -> str:
    return f"{source.org_url.rstrip('/')}/{source.project}/_workitems/edit/{external_id}"


def _parse_changed_date(value: Any, *, fallback: datetime) -> datetime:
    if not isinstance(value, str) or not value:
        return fallback
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return fallback


async def _upsert_payload(
    db: AsyncSession,
    source: WorkSource,
    payload: dict[str, Any],
    *,
    now: datetime,
    pulled_as_parent: bool,
) -> bool | None:
    """Upsert one batch payload; None when the payload has no usable id."""
    external_id = payload.get("id")
    if not isinstance(external_id, int):
        return None
    fields: dict[str, Any] = payload.get("fields") or {}
    priority = fields.get(_FIELD_PRIORITY)
    acceptance_html = fields.get(_FIELD_ACCEPTANCE)
    parent = fields.get(_FIELD_PARENT)
    return await work_repo.upsert_item(
        db,
        source_id=source.id,
        external_id=external_id,
        parent_external_id=parent if isinstance(parent, int) else None,
        pulled_as_parent=pulled_as_parent,
        external_url=_work_item_url(source, external_id),
        item_type=str(fields.get(_FIELD_TYPE) or ""),
        title=str(fields.get(_FIELD_TITLE) or ""),
        description_md=html_to_markdown(fields.get(_FIELD_DESCRIPTION)),
        acceptance_md=html_to_markdown(acceptance_html) if acceptance_html else None,
        state=str(fields.get(_FIELD_STATE) or ""),
        iteration=fields.get(_FIELD_ITERATION),
        assigned_to=parse_assigned_to(fields.get(_FIELD_ASSIGNED)),
        priority=int(priority) if isinstance(priority, int | float) else None,
        tags=parse_tags(fields.get(_FIELD_TAGS)),
        raw=payload,
        external_changed_at=_parse_changed_date(fields.get(_FIELD_CHANGED), fallback=now),
        synced_at=now,
    )


async def sync_source(
    db: AsyncSession, source: WorkSource, client: AzureDevOpsClient
) -> SyncCounts:
    """Resolve the current sprint and PAT owner (both best-effort), then run
    the operator's WIQL if set, else the whole current sprint if one resolved,
    else the default assigned-to-me query; batch-fetch, and upsert every
    returned item on (source_id, external_id). Parents referenced but not
    returned by the WIQL (stories owned by others) are fetched in a second
    pass and flagged `pulled_as_parent` so the UI can group under them."""
    now = datetime.now(tz=UTC)

    # Both best-effort: a failed lookup keeps the source's last known value.
    with contextlib.suppress(AzureDevOpsError):
        source.current_iteration = await client.get_current_iteration_path()
    with contextlib.suppress(AzureDevOpsError):
        source.owner_display_name = await client.get_authenticated_user_display_name()

    if source.query_wiql:
        wiql = source.query_wiql
    elif source.current_iteration:
        wiql = _sprint_wiql(source.current_iteration)
    else:
        wiql = DEFAULT_WIQL
    ids = await client.query_work_item_ids(wiql)
    payloads = await client.get_work_items_batch(ids, FIELDS)

    inserted = 0
    updated = 0
    for payload in payloads:
        inserted_row = await _upsert_payload(db, source, payload, now=now, pulled_as_parent=False)
        if inserted_row is None:
            continue
        if inserted_row:
            inserted += 1
        else:
            updated += 1

    fetched_ids = {p["id"] for p in payloads if isinstance(p.get("id"), int)}
    parent_ids = sorted(
        {
            parent
            for p in payloads
            for parent in [(p.get("fields") or {}).get(_FIELD_PARENT)]
            if isinstance(parent, int) and parent not in fetched_ids
        }
    )
    parent_payloads = (
        await client.get_work_items_batch(parent_ids, FIELDS) if parent_ids else []
    )
    for payload in parent_payloads:
        inserted_row = await _upsert_payload(db, source, payload, now=now, pulled_as_parent=True)
        if inserted_row is None:
            continue
        if inserted_row:
            inserted += 1
        else:
            updated += 1

    source.last_sync_at = now
    await db.flush()
    return SyncCounts(
        fetched=len(payloads) + len(parent_payloads), inserted=inserted, updated=updated
    )
