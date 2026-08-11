"""Map coding-session ORM rows to the contract's Pydantic schemas.

Three of a session's reported fields are derived here rather than stored, and
list and detail share the one helper that derives them so the two can never
disagree: `status` (silence turns an unclosed run into `abandoned`), `active_ms`
(wall time minus the pauses) and the `title`/`title_source` fallback for a run
that never carried a prompt.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from app.api.v1.coding import schemas
from app.db.models.coding import (
    LAUNCH_AUTOMATED,
    STATUS_ABANDONED,
    STATUS_RUNNING,
    TITLE_CWD,
    TITLE_PROVENANCE,
    WORKFLOW_FACTORY,
    CodingAgent,
    CodingAsset,
    CodingAssetUse,
    CodingEnvelope,
    CodingEvent,
    CodingGateCheck,
    CodingPhase,
    CodingSession,
    asset_id_for,
    idle_window,
)


def derived_status(session: CodingSession, *, now: datetime) -> str:
    """`running` only when the run is genuinely live.

    SessionEnd rides an async hook the dying process outruns, so most runs never
    close themselves and the stored `running` is a lie — 78 of 113 rows claimed
    it, the oldest last heard from a day and a half earlier. Silence past this
    kind of run's idle window reports `abandoned` instead. A run that did report
    an outcome keeps it: only the absence of one is filled in from silence.
    """
    if session.status != STATUS_RUNNING or session.ended_at is not None:
        return session.status
    silent_since = now - idle_window(session.workflow)
    return STATUS_ABANDONED if session.last_event_at < silent_since else STATUS_RUNNING


def derived_title(session: CodingSession) -> tuple[str | None, str | None]:
    """(title, title_source). Falls back for a run that never had a prompt: a
    headless one is named for its provenance, anything else for where it ran."""
    if session.title:
        return session.title, session.title_source
    where = session.git_repo or (Path(session.cwd).name if session.cwd else None)
    if session.launch_mode == LAUNCH_AUTOMATED:
        return (f"headless run · {where}" if where else "headless run"), TITLE_PROVENANCE
    return (where, TITLE_CWD) if where else (None, None)


def active_ms_for(session: CodingSession, phases: list[CodingPhase], from_events: int) -> int:
    """A pipeline run measures its own stages, so trust that over the gaps
    between events; everything else gets the inferred figure.

    Public because the cross-run trend reports the same number, and two
    definitions of "working time" would be one too many."""
    if session.workflow == WORKFLOW_FACTORY:
        measured = [p.duration_ms for p in phases if p.duration_ms is not None]
        if measured:
            return sum(measured)
    return from_events


def asset_uses(
    assets: list[CodingAsset], child_assets: list[tuple[str, str, int]]
) -> list[schemas.AssetUse]:
    """This run's assets, with what the runs it launched used folded in.

    A pipeline's parent process issues no tool calls at all — every skill and
    subagent is reached for inside a headless stage child — so a factory run's
    own list is empty by construction and folding is the only way the question
    *what does the pipeline use* has an answer. Nothing is overwritten by it.

    A child's uses arrive as a laneless row: the child's lane is its own `main`,
    which is not one of this run's lanes and would read as a lie. `via_children`
    keeps the fold legible, so a reader can always subtract it back out.
    """
    rows: dict[tuple[str, str, str | None], schemas.AssetUse] = {}
    order: list[tuple[str, str, str | None]] = []
    for asset in assets:
        key = (asset.kind, asset.name, asset.lane)
        rows[key] = coding_asset_to_use(asset)
        order.append(key)
    for kind, name, uses in child_assets:
        key = (kind, name, None)
        existing = rows.get(key)
        if existing is None:
            rows[key] = schemas.AssetUse(
                kind=kind,
                name=name,
                asset_id=asset_id_for(kind, name),
                lane=None,
                uses=uses,
                via_children=uses,
            )
            order.append(key)
        else:
            rows[key] = existing.model_copy(
                update={
                    "uses": existing.uses + uses,
                    "via_children": existing.via_children + uses,
                }
            )
    merged = [rows[key] for key in order]
    # The same order the counters came back in: most-used first, then stable.
    merged.sort(key=lambda use: (-use.uses, use.kind, use.name))
    return merged


def _session_fields(
    session: CodingSession,
    *,
    event_count: int,
    tool_call_count: int,
    phases: list[CodingPhase],
    agents: list[CodingAgent],
    assets: list[CodingAsset],
    child_assets: list[tuple[str, str, int]],
    child_count: int,
    active_ms: int,
    now: datetime,
) -> dict[str, Any]:
    """Everything a card and a detail view share, phases apart."""
    # Duration is computed here, not in SQL: SQLite has no interval arithmetic
    # to share with Postgres, and both timestamps are already loaded.
    end = session.ended_at or session.last_event_at
    duration = max((end - session.started_at).total_seconds(), 0.0)
    title, title_source = derived_title(session)
    return {
        "id": session.id,
        "cwd": session.cwd,
        "git_repo": session.git_repo,
        "model": session.model,
        "source": session.source,
        "launch_mode": session.launch_mode,
        "title": title,
        "title_source": title_source,
        "parent_session_id": session.parent_session_id,
        "child_count": child_count,
        "workflow": session.workflow,
        "status": derived_status(session, now=now),
        "started_at": session.started_at,
        "last_event_at": session.last_event_at,
        "ended_at": session.ended_at,
        "stats": session.stats,
        "cost_usd": session.cost_usd,
        "tokens_total": session.tokens_total,
        "tokens_in": session.tokens_in,
        "tokens_out": session.tokens_out,
        "cache_read_tokens": session.cache_read_tokens,
        "event_count": event_count,
        "tool_call_count": tool_call_count,
        "duration_seconds": duration,
        "wall_ms": int(duration * 1000),
        "active_ms": active_ms_for(session, phases, active_ms),
        "agents": [coding_agent_to_lane(a) for a in agents],
        "assets": asset_uses(assets, child_assets),
    }


def coding_session_to_schema(
    session: CodingSession,
    *,
    event_count: int,
    tool_call_count: int,
    phases: list[CodingPhase],
    agents: list[CodingAgent],
    assets: list[CodingAsset],
    child_assets: list[tuple[str, str, int]],
    child_count: int,
    active_ms: int,
    now: datetime,
) -> schemas.CodingSession:
    return schemas.CodingSession(
        **_session_fields(
            session,
            event_count=event_count,
            tool_call_count=tool_call_count,
            phases=phases,
            agents=agents,
            assets=assets,
            child_assets=child_assets,
            child_count=child_count,
            active_ms=active_ms,
            now=now,
        ),
        phases=[coding_phase_to_summary(p) for p in phases],
    )


def coding_session_to_detail(
    session: CodingSession,
    *,
    event_count: int,
    tool_call_count: int,
    phases: list[CodingPhase],
    agents: list[CodingAgent],
    assets: list[CodingAsset],
    child_assets: list[tuple[str, str, int]],
    child_count: int,
    active_ms: int,
    envelopes: list[CodingEnvelope],
    gate_checks: list[CodingGateCheck],
    now: datetime,
) -> schemas.CodingSessionDetail:
    return schemas.CodingSessionDetail(
        **_session_fields(
            session,
            event_count=event_count,
            tool_call_count=tool_call_count,
            phases=phases,
            agents=agents,
            assets=assets,
            child_assets=child_assets,
            child_count=child_count,
            active_ms=active_ms,
            now=now,
        ),
        phases=[coding_phase_to_schema(p) for p in phases],
        envelopes=[envelope_to_schema(e) for e in envelopes],
        gate_checks=[gate_check_to_schema(c) for c in gate_checks],
    )


def envelope_to_schema(envelope: CodingEnvelope) -> schemas.EnvelopeAttempt:
    return schemas.EnvelopeAttempt(
        id=envelope.id,
        phase_id=envelope.phase_id,
        event_id=envelope.event_id,
        role=envelope.role,
        attempt=envelope.attempt,
        parsed=envelope.parsed,
        parse_error=envelope.parse_error,
        status=envelope.status,
        body=envelope.body,
        raw_text=envelope.raw_text,
        origin=envelope.origin,
        created_at=envelope.created_at,
    )


def gate_check_to_schema(check: CodingGateCheck) -> schemas.GateCheckItem:
    return schemas.GateCheckItem(
        id=check.id,
        phase_id=check.phase_id,
        event_id=check.event_id,
        gate=check.gate,
        attempt=check.attempt,
        item=check.item,
        ok=check.ok,
        note=check.note,
        origin=check.origin,
        created_at=check.created_at,
    )


def coding_asset_to_use(asset: CodingAsset) -> schemas.AssetUse:
    return schemas.AssetUse(
        kind=asset.kind,
        name=asset.name,
        asset_id=asset_id_for(asset.kind, asset.name),
        lane=asset.lane,
        uses=asset.uses,
        via_children=0,
    )


def asset_usage_to_schema(row: tuple[str, str, int, int, datetime]) -> schemas.CodingAssetUsage:
    kind, name, sessions, uses, last_used_at = row
    return schemas.CodingAssetUsage(
        kind=kind,
        name=name,
        asset_id=asset_id_for(kind, name),
        sessions=sessions,
        uses=uses,
        last_used_at=last_used_at,
    )


def asset_call_to_schema(use: CodingAssetUse) -> schemas.AssetCall:
    return schemas.AssetCall(
        used_at=use.used_at,
        lane=use.lane,
        source=use.source,
        input=use.input,
    )


def asset_session_use_to_schema(
    session: CodingSession,
    *,
    uses: int,
    first_used_at: datetime,
    last_used_at: datetime,
    calls: list[CodingAssetUse],
    now: datetime,
) -> schemas.AssetSessionUse:
    title, _ = derived_title(session)
    return schemas.AssetSessionUse(
        session_id=session.id,
        title=title,
        git_repo=session.git_repo,
        cwd=session.cwd,
        status=derived_status(session, now=now),
        started_at=session.started_at,
        uses=uses,
        first_used_at=first_used_at,
        last_used_at=last_used_at,
        calls=[asset_call_to_schema(c) for c in calls],
    )


def coding_phase_to_summary(phase: CodingPhase) -> schemas.PhaseSummary:
    return schemas.PhaseSummary(
        seq=phase.seq,
        name=phase.name,
        agent=phase.agent,
        status=phase.status,
        started_at=phase.started_at,
        duration_ms=phase.duration_ms,
    )


def coding_phase_to_schema(phase: CodingPhase) -> schemas.CodingPhase:
    return schemas.CodingPhase(
        id=phase.id,
        seq=phase.seq,
        name=phase.name,
        kind=phase.kind,
        agent=phase.agent,
        description=phase.description,
        status=phase.status,
        started_at=phase.started_at,
        ended_at=phase.ended_at,
        duration_ms=phase.duration_ms,
        cost_usd=phase.cost_usd,
        tokens_in=phase.tokens_in,
        tokens_out=phase.tokens_out,
        corrections=phase.corrections,
        commit_sha=phase.commit_sha,
        gates_passed=phase.gates_passed,
        gates_failed=phase.gates_failed,
    )


def coding_agent_to_lane(agent: CodingAgent) -> schemas.AgentLane:
    return schemas.AgentLane(
        name=agent.name,
        model=agent.model,
        color=agent.color,
        context_tokens=agent.context_tokens,
        context_window=agent.context_window,
        cost_usd=agent.cost_usd,
        tokens_in=agent.tokens_in,
        tokens_out=agent.tokens_out,
        turns=agent.turns,
    )


def coding_event_to_schema(event: CodingEvent) -> schemas.CodingEvent:
    return schemas.CodingEvent(
        id=event.id,
        session_id=event.session_id,
        event_type=event.event_type,
        tool_name=event.tool_name,
        payload=event.payload,
        created_at=event.created_at,
        phase_id=event.phase_id,
        agent=event.agent,
        ok=event.ok,
        duration_ms=event.duration_ms,
        ended_at=event.ended_at,
    )
