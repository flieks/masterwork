"""Claude Code observability endpoints: hook ingest, plus session and event reads.

Two routers because the consumers differ: `hooks` is written by a shell hook,
`coding` by the Sessions screen. Like the rest of this API, neither has auth —
the app is single-user and bound to localhost.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.api.v1.coding import schemas, service

router = APIRouter(tags=["coding"])
hooks_router = APIRouter(tags=["hooks"])


@hooks_router.post(
    "/hooks/events",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="ingestHookEvent",
)
async def ingest_hook_event(
    body: schemas.HookEventRequest,
    db: AsyncSession = Depends(get_db),
) -> None:
    await service.ingest_event(db, body)


@router.get(
    "/coding-sessions",
    response_model=list[schemas.CodingSession],
    operation_id="listCodingSessions",
)
async def list_coding_sessions(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    include_empty: bool = Query(
        False,
        description=(
            "Include sessions that ended without running a tool — mostly the desktop "
            "app's discarded startup processes, hidden by default."
        ),
    ),
    include_automated: bool = Query(
        False,
        description=(
            "Include sessions a `claude -p` one-shot started — wrapper scripts, hooks, "
            "schedulers — rather than a person. Hidden by default."
        ),
    ),
    workflow: str | None = Query(
        None,
        description=(
            'Keep only runs of this workflow — "factory" for pipeline runs, "chat" for '
            "plain Claude Code sessions (which also matches the ones that never named one)."
        ),
    ),
    status_filter: str | None = Query(
        None,
        alias="status",
        description=(
            "Keep only runs with this status: running | success | failed | interrupted | "
            "abandoned. Matched against the derived status, not the stored one."
        ),
    ),
    roots_only: bool = Query(
        False,
        description=(
            "Hide runs that another run launched — a pipeline's five headless stages "
            "collapse into their parent instead of showing as five orphan cards."
        ),
    ),
    db: AsyncSession = Depends(get_db),
) -> list[schemas.CodingSession]:
    return await service.list_sessions(
        db,
        limit=limit,
        offset=offset,
        include_empty=include_empty,
        include_automated=include_automated,
        workflow=workflow,
        status=status_filter,
        roots_only=roots_only,
    )


@router.get(
    "/coding-assets",
    response_model=list[schemas.CodingAssetUsage],
    operation_id="listCodingAssetUsage",
)
async def list_coding_asset_usage(
    since: datetime | None = Query(
        None, description="Only count assets used at or after this instant."
    ),
    kind: str | None = Query(None, description='Keep only "skill" or only "agent".'),
    include_inspection: bool = Query(
        False,
        description=(
            "Include masterwork's own analysis runs, which Read every linked asset's "
            "SKILL.md and would otherwise rank assets by inspection rather than use."
        ),
    ),
    db: AsyncSession = Depends(get_db),
) -> list[schemas.CodingAssetUsage]:
    return await service.list_asset_usage(
        db, kind=kind, since=since, include_inspection=include_inspection
    )


@router.get(
    "/coding-assets/{asset_id}/sessions",
    response_model=list[schemas.AssetSessionUse],
    operation_id="listAssetSessionUses",
    summary="The runs that used one asset, with the arguments each call carried",
)
async def list_asset_session_uses(
    asset_id: str,
    limit: int = Query(50, ge=1, le=200),
    include_inspection: bool = Query(
        False,
        description=(
            "Include masterwork's own analysis runs, which Read every linked asset's "
            "SKILL.md — see the same flag on /coding-assets."
        ),
    ),
    db: AsyncSession = Depends(get_db),
) -> list[schemas.AssetSessionUse]:
    """Matched on the id's kind and name, so a plugin asset's own id works too."""
    return await service.list_asset_sessions(
        db, asset_id, limit=limit, include_inspection=include_inspection
    )


@router.post(
    "/coding-sessions/backfill",
    response_model=schemas.BackfillTotals,
    operation_id="backfillCodingSessions",
    summary="Rebuild every session's derived rows from its stored events",
)
async def backfill_coding_sessions(db: AsyncSession = Depends(get_db)) -> schemas.BackfillTotals:
    totals = await service.backfill_all(db)
    return schemas.BackfillTotals(**asdict(totals))


@router.post(
    "/coding-sessions/{session_id}/backfill",
    response_model=schemas.BackfillResult,
    operation_id="backfillCodingSession",
    summary="Rebuild one session's derived rows from its stored events",
)
async def backfill_coding_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> schemas.BackfillResult:
    """Replay the stored stream through the current derivation.

    Stages, lanes and assets are derived, so a fix to how they are derived does
    nothing for a run already recorded until its events are replayed. Idempotent:
    the derived rows are dropped and rebuilt, never updated. The event stream
    itself is never touched — it is the record this rebuilds from.
    """
    result = await service.backfill_session(db, session_id)
    return schemas.BackfillResult(**asdict(result))


@router.get(
    "/coding-sessions/{session_id}",
    response_model=schemas.CodingSessionDetail,
    operation_id="getCodingSession",
)
async def get_coding_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> schemas.CodingSessionDetail:
    return await service.get_session(db, session_id)


@router.get(
    "/coding-sessions/{session_id}/events",
    response_model=list[schemas.CodingEvent],
    operation_id="listCodingSessionEvents",
)
async def list_coding_session_events(
    session_id: str,
    after: int = Query(0, ge=0, description="Last event id already held; 0 loads from the start."),
    limit: int = Query(500, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
) -> list[schemas.CodingEvent]:
    return await service.list_events(db, session_id, after=after, limit=limit)
