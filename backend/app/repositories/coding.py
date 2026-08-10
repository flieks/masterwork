"""Data access for Claude Code sessions and their event stream."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, case, delete, func, not_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.db.models.coding import (
    ACTIVE_GAP,
    EVIDENCE_RECOVERED,
    IDLE_WINDOW,
    LAUNCH_AUTOMATED,
    STATUS_ABANDONED,
    STATUS_RUNNING,
    WORKFLOW_CHAT,
    WORKFLOW_FACTORY,
    CodingAgent,
    CodingAsset,
    CodingAssetUse,
    CodingEnvelope,
    CodingEvent,
    CodingGateCheck,
    CodingPhase,
    CodingSession,
)

# Only PostToolUse means a tool actually ran; PreToolUse fires even when denied.
TOOL_CALL_EVENT = "PostToolUse"

# The Claude desktop app spawns a headless `claude` per open directory and discards
# it: SessionStart (+ SessionEnd, when the async hook outlives the process), no
# turn, no transcript file. They were ~75% of all rows, so the list leaves out any
# session that never got past these two — but only once it can no longer become
# real, since a session's first seconds look exactly the same. The test is which
# events are absent, not which are present, so a producer with its own vocabulary
# (the factory runner's `agent_turn`, `phase_start`, …) counts as doing something.
LIFECYCLE_EVENTS = ("SessionStart", "SessionEnd")


def _idle_cutoff() -> datetime:
    """The instant before which silence means the run is no longer live.

    Computed in Python rather than as `func.now() - IDLE_WINDOW`: SQLite (the
    packaged default) cannot do date arithmetic on CURRENT_TIMESTAMP, so that
    form silently never matched and ghosts stayed listed forever.
    """
    return datetime.now(tz=UTC).replace(tzinfo=None) - IDLE_WINDOW


def _is_live(cutoff: datetime) -> ColumnElement[bool]:
    """Open and recent. The only thing that may call itself `running`."""
    return and_(CodingSession.ended_at.is_(None), CodingSession.last_event_at >= cutoff)


def _is_empty_session(cutoff: datetime) -> ColumnElement[bool]:
    """Nothing but lifecycle events, and no longer live — see IDLE_WINDOW."""
    did_something = (
        select(CodingEvent.id)
        .where(
            CodingEvent.session_id == CodingSession.id,
            CodingEvent.event_type.not_in(LIFECYCLE_EVENTS),
        )
        .exists()
    )
    # SessionEnd rides an async hook that the dying process can outrun, so a ghost
    # is not reliably closed — silence has to count as finished too.
    finished = or_(
        CodingSession.ended_at.is_not(None),
        CodingSession.last_event_at < cutoff,
    )
    return and_(not_(did_something), finished)


def _status_filter(status: str, cutoff: datetime) -> ColumnElement[bool]:
    """Match the status the reader will actually see, not the stored one.

    `abandoned` is derived from silence and `running` is narrowed by it, so
    filtering on the column alone would contradict the serialized payload.
    """
    if status == STATUS_ABANDONED:
        return and_(CodingSession.status == STATUS_RUNNING, not_(_is_live(cutoff)))
    if status == STATUS_RUNNING:
        return and_(CodingSession.status == STATUS_RUNNING, _is_live(cutoff))
    return CodingSession.status == status


async def get_session(db: AsyncSession, session_id: str) -> CodingSession | None:
    return await db.get(CodingSession, session_id)


async def create_session(
    db: AsyncSession,
    *,
    session_id: str,
    cwd: str,
    git_repo: str | None,
    model: str | None,
    now: datetime,
) -> CodingSession:
    session = CodingSession(
        id=session_id,
        cwd=cwd,
        git_repo=git_repo,
        model=model,
        started_at=now,
        last_event_at=now,
    )
    db.add(session)
    await db.flush()
    return session


async def list_sessions(
    db: AsyncSession,
    *,
    limit: int,
    offset: int,
    include_empty: bool = False,
    include_automated: bool = False,
    workflow: str | None = None,
    status: str | None = None,
    roots_only: bool = False,
    parent_session_id: str | None = None,
) -> list[CodingSession]:
    cutoff = _idle_cutoff()
    query = select(CodingSession)
    # Naming a parent is a lookup of a known run's children, not a browse of the
    # grid, so the two hygiene suppressions are skipped: a pipeline's stages are
    # headless by construction, and `child_count` counts all of them — filtered,
    # this endpoint would contradict the number on the parent's own card.
    scoped = parent_session_id is not None
    if not include_empty and not scoped:
        query = query.where(not_(_is_empty_session(cutoff)))
    if not include_automated and not scoped:
        # is_distinct_from so the unclassified (null) rows stay listed.
        query = query.where(CodingSession.launch_mode.is_distinct_from(LAUNCH_AUTOMATED))
    if workflow is not None:
        # Nothing writes "chat"; a plain session simply never claimed a workflow.
        query = query.where(
            or_(CodingSession.workflow == workflow, CodingSession.workflow.is_(None))
            if workflow == WORKFLOW_CHAT
            else CodingSession.workflow == workflow
        )
    if status is not None:
        query = query.where(_status_filter(status, cutoff))
    if roots_only:
        query = query.where(CodingSession.parent_session_id.is_(None))
    if parent_session_id is not None:
        # The complement of roots_only; asking for both is a contradiction and
        # correctly answers with nothing.
        query = query.where(CodingSession.parent_session_id == parent_session_id)
    # Genuinely live runs outrank stale ones however recently the stale ones
    # spoke; everything else is most-recent-first.
    result = await db.execute(
        query.order_by(
            case((_is_live(cutoff), 0), else_=1),
            CodingSession.last_event_at.desc(),
        )
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


async def factory_session_at(db: AsyncSession, *, cwd: str, at: datetime) -> CodingSession | None:
    """The pipeline run that owned `cwd` at that instant, if one did.

    How a headless stage child finds its parent: the runner gives every stage
    the repo as its working directory, so the run whose window contains the
    child's first event is the run that launched it.
    """
    result = await db.execute(
        select(CodingSession)
        .where(
            CodingSession.workflow == WORKFLOW_FACTORY,
            CodingSession.cwd == cwd,
            CodingSession.started_at <= at,
            or_(CodingSession.ended_at.is_(None), CodingSession.ended_at >= at),
        )
        .order_by(CodingSession.started_at.desc())
        .limit(1)
    )
    return result.scalars().first()


async def phase_at(db: AsyncSession, session_id: str, *, at: datetime) -> CodingPhase | None:
    """The run's most recently started stage as of that instant — which stage a
    child launched then belongs to."""
    result = await db.execute(
        select(CodingPhase)
        .where(CodingPhase.session_id == session_id, CodingPhase.started_at <= at)
        .order_by(CodingPhase.started_at.desc(), CodingPhase.seq.desc())
        .limit(1)
    )
    return result.scalars().first()


async def child_counts(db: AsyncSession, session_ids: list[str]) -> dict[str, int]:
    """How many runs each of these launched; absent ids launched none."""
    if not session_ids:
        return {}
    result = await db.execute(
        select(CodingSession.parent_session_id, func.count(CodingSession.id))
        .where(CodingSession.parent_session_id.in_(session_ids))
        .group_by(CodingSession.parent_session_id)
    )
    return {row[0]: row[1] for row in result.all()}


async def all_session_ids(db: AsyncSession) -> list[str]:
    """Every session, oldest first — the order a full backfill replays in, so a
    parent's stages exist before its children look for them."""
    result = await db.execute(select(CodingSession.id).order_by(CodingSession.started_at))
    return list(result.scalars().all())


async def add_event(
    db: AsyncSession,
    *,
    session_id: str,
    event_type: str,
    tool_name: str | None,
    payload: dict[str, Any] | None,
    now: datetime,
) -> CodingEvent:
    event = CodingEvent(
        session_id=session_id,
        event_type=event_type,
        tool_name=tool_name,
        payload=payload,
        created_at=now,
    )
    db.add(event)
    await db.flush()
    return event


async def list_events(
    db: AsyncSession, session_id: str, *, after: int, limit: int
) -> list[CodingEvent]:
    """Rowid cursor: the same query serves history and live polling."""
    result = await db.execute(
        select(CodingEvent)
        .where(CodingEvent.session_id == session_id, CodingEvent.id > after)
        .order_by(CodingEvent.id)
        .limit(limit)
    )
    return list(result.scalars().all())


async def session_events(db: AsyncSession, session_id: str) -> list[CodingEvent]:
    """Every event of one session, oldest first — the backfill's replay order."""
    result = await db.execute(
        select(CodingEvent).where(CodingEvent.session_id == session_id).order_by(CodingEvent.id)
    )
    return list(result.scalars().all())


async def get_phase_by_seq(db: AsyncSession, session_id: str, seq: int) -> CodingPhase | None:
    result = await db.execute(
        select(CodingPhase).where(CodingPhase.session_id == session_id, CodingPhase.seq == seq)
    )
    return result.scalar_one_or_none()


async def get_phase_by_name(db: AsyncSession, session_id: str, name: str) -> CodingPhase | None:
    """The latest stage of that name — a retried stage reuses its row."""
    result = await db.execute(
        select(CodingPhase)
        .where(CodingPhase.session_id == session_id, CodingPhase.name == name)
        .order_by(CodingPhase.seq.desc())
        .limit(1)
    )
    return result.scalars().first()


async def open_phase(
    db: AsyncSession, session_id: str, *, agent: str | None = None
) -> CodingPhase | None:
    """The newest stage still running; what an unlabelled event belongs to.

    Lane-scoped when the event named one, because a subagent's span runs
    *alongside* `main`'s turn rather than after it — unscoped, the newest-first
    ordering glued every one of main's tool calls onto whichever subagent
    happened to be open.
    """
    conditions = [CodingPhase.session_id == session_id, CodingPhase.ended_at.is_(None)]
    if agent is not None:
        conditions.append(CodingPhase.agent == agent)
    result = await db.execute(
        select(CodingPhase).where(*conditions).order_by(CodingPhase.seq.desc()).limit(1)
    )
    return result.scalars().first()


async def next_phase_seq(db: AsyncSession, session_id: str) -> int:
    result = await db.execute(
        select(func.max(CodingPhase.seq)).where(CodingPhase.session_id == session_id)
    )
    return (result.scalar() or 0) + 1


async def phase_count(db: AsyncSession, session_id: str, *, agent: str) -> int:
    """How many stages one lane already has — a lane's turn number, which `seq`
    stopped being able to supply once lanes could interleave."""
    result = await db.execute(
        select(func.count())
        .select_from(CodingPhase)
        .where(CodingPhase.session_id == session_id, CodingPhase.agent == agent)
    )
    return result.scalar() or 0


async def create_phase(
    db: AsyncSession, *, session_id: str, seq: int, name: str, started_at: datetime
) -> CodingPhase:
    phase = CodingPhase(session_id=session_id, seq=seq, name=name, started_at=started_at)
    db.add(phase)
    await db.flush()
    return phase


async def phase_rollup(
    db: AsyncSession, session_id: str
) -> tuple[float | None, int | None, int | None]:
    """(cost, tokens_in, tokens_out) summed over the run's stages; each is None
    when no stage reported it, which is what keeps a sum from erasing a total a
    hook sent directly."""
    result = await db.execute(
        select(
            func.sum(CodingPhase.cost_usd),
            func.sum(CodingPhase.tokens_in),
            func.sum(CodingPhase.tokens_out),
        ).where(CodingPhase.session_id == session_id)
    )
    cost, tokens_in, tokens_out = result.one()
    return (
        float(cost) if cost is not None else None,
        int(tokens_in) if tokens_in is not None else None,
        int(tokens_out) if tokens_out is not None else None,
    )


async def get_agent(db: AsyncSession, session_id: str, name: str) -> CodingAgent | None:
    result = await db.execute(
        select(CodingAgent).where(CodingAgent.session_id == session_id, CodingAgent.name == name)
    )
    return result.scalar_one_or_none()


async def create_agent(db: AsyncSession, *, session_id: str, name: str) -> CodingAgent:
    agent = CodingAgent(session_id=session_id, name=name)
    db.add(agent)
    await db.flush()
    return agent


async def phases_by_session(
    db: AsyncSession, session_ids: list[str]
) -> dict[str, list[CodingPhase]]:
    if not session_ids:
        return {}
    result = await db.execute(
        select(CodingPhase)
        .where(CodingPhase.session_id.in_(session_ids))
        .order_by(CodingPhase.session_id, CodingPhase.seq)
    )
    grouped: dict[str, list[CodingPhase]] = {sid: [] for sid in session_ids}
    for phase in result.scalars().all():
        grouped[phase.session_id].append(phase)
    return grouped


async def agents_by_session(
    db: AsyncSession, session_ids: list[str]
) -> dict[str, list[CodingAgent]]:
    """Lanes in the order they first appeared, which is insertion order."""
    if not session_ids:
        return {}
    result = await db.execute(
        select(CodingAgent)
        .where(CodingAgent.session_id.in_(session_ids))
        .order_by(CodingAgent.session_id, CodingAgent.id)
    )
    grouped: dict[str, list[CodingAgent]] = {sid: [] for sid in session_ids}
    for agent in result.scalars().all():
        grouped[agent.session_id].append(agent)
    return grouped


async def get_asset(
    db: AsyncSession, session_id: str, *, kind: str, name: str, lane: str | None
) -> CodingAsset | None:
    """The counter row for one (asset, lane) pair. `lane IS NULL` is matched
    explicitly — the unique constraint cannot enforce it, since both dialects
    treat nulls as distinct."""
    result = await db.execute(
        select(CodingAsset).where(
            CodingAsset.session_id == session_id,
            CodingAsset.kind == kind,
            CodingAsset.name == name,
            CodingAsset.lane.is_(None) if lane is None else CodingAsset.lane == lane,
        )
    )
    return result.scalars().first()


async def create_asset(
    db: AsyncSession,
    *,
    session_id: str,
    kind: str,
    name: str,
    lane: str | None,
    now: datetime,
) -> CodingAsset:
    asset = CodingAsset(
        session_id=session_id,
        kind=kind,
        name=name,
        lane=lane,
        uses=0,
        first_seen_at=now,
        last_seen_at=now,
    )
    db.add(asset)
    await db.flush()
    return asset


async def assets_by_session(
    db: AsyncSession, session_ids: list[str]
) -> dict[str, list[CodingAsset]]:
    """Assets per session, most-used first — the order a card shows them in."""
    if not session_ids:
        return {}
    result = await db.execute(
        select(CodingAsset)
        .where(CodingAsset.session_id.in_(session_ids))
        .order_by(
            CodingAsset.session_id,
            CodingAsset.uses.desc(),
            CodingAsset.kind,
            CodingAsset.name,
        )
    )
    grouped: dict[str, list[CodingAsset]] = {sid: [] for sid in session_ids}
    for asset in result.scalars().all():
        grouped[asset.session_id].append(asset)
    return grouped


async def asset_usage(
    db: AsyncSession,
    *,
    kind: str | None = None,
    since: datetime | None = None,
    exclude_cwd: str | None = None,
) -> list[tuple[str, str, int, int, datetime]]:
    """The cross-session rollup: (kind, name, sessions, uses, last_used_at).

    Two sources, because a window and an all-time total are different questions.
    All-time sums `coding_assets`, the per-(run, asset, lane) counter of record.
    A window cannot: the counter carries only a `last_seen_at`, so narrowing on
    it and summing `uses` returns an asset's *entire* history the moment its
    most recent call happens to land inside the window — "last 24h" reporting
    figures from weeks ago. So `since` is answered from `coding_asset_uses`, the
    append-only log that timestamps every individual call.

    Rows are returned positionally rather than as labelled columns — a label
    named `count` or `index` collides with a method on SQLAlchemy's Row.
    """
    if since is None:
        return await _usage_from_counters(db, kind=kind, exclude_cwd=exclude_cwd)
    return await _usage_from_log(db, kind=kind, since=since, exclude_cwd=exclude_cwd)


async def _usage_from_counters(
    db: AsyncSession, *, kind: str | None, exclude_cwd: str | None
) -> list[tuple[str, str, int, int, datetime]]:
    sessions = func.count(func.distinct(CodingAsset.session_id))
    uses = func.sum(CodingAsset.uses)
    last_used = func.max(CodingAsset.last_seen_at)
    query = select(CodingAsset.kind, CodingAsset.name, sessions, uses, last_used)
    if exclude_cwd is not None:
        # Runs launched from that directory are masterwork reading assets, not an
        # agent using them — see INSPECTION_CWD in the service.
        query = query.join(CodingSession, CodingSession.id == CodingAsset.session_id).where(
            CodingSession.cwd != exclude_cwd
        )
    if kind is not None:
        query = query.where(CodingAsset.kind == kind)
    result = await db.execute(
        query.group_by(CodingAsset.kind, CodingAsset.name)
        # Name breaks ties so the ranking is stable across calls.
        .order_by(uses.desc(), CodingAsset.name)
    )
    return [(row[0], row[1], int(row[2]), int(row[3] or 0), row[4]) for row in result.all()]


async def _usage_from_log(
    db: AsyncSession, *, kind: str | None, since: datetime, exclude_cwd: str | None
) -> list[tuple[str, str, int, int, datetime]]:
    """The same shape, counted per call inside the window.

    One log row is one call, so `uses` is a row count rather than a sum, and a
    run only counts as a user of the asset if it called it inside the window.
    """
    sessions = func.count(func.distinct(CodingAssetUse.session_id))
    uses = func.count(CodingAssetUse.id)
    last_used = func.max(CodingAssetUse.used_at)
    query = select(CodingAssetUse.kind, CodingAssetUse.name, sessions, uses, last_used).where(
        CodingAssetUse.used_at >= since
    )
    if exclude_cwd is not None:
        query = query.join(CodingSession, CodingSession.id == CodingAssetUse.session_id).where(
            CodingSession.cwd != exclude_cwd
        )
    if kind is not None:
        query = query.where(CodingAssetUse.kind == kind)
    result = await db.execute(
        query.group_by(CodingAssetUse.kind, CodingAssetUse.name).order_by(
            uses.desc(), CodingAssetUse.name
        )
    )
    return [(row[0], row[1], int(row[2]), int(row[3] or 0), row[4]) for row in result.all()]


async def add_asset_use(
    db: AsyncSession,
    *,
    session_id: str,
    kind: str,
    name: str,
    lane: str | None,
    source: str,
    input: dict[str, str] | None,
    now: datetime,
) -> CodingAssetUse:
    """Append one call to the log the asset page reads."""
    use = CodingAssetUse(
        session_id=session_id,
        kind=kind,
        name=name,
        lane=lane,
        source=source,
        input=input,
        used_at=now,
    )
    db.add(use)
    await db.flush()
    return use


async def asset_sessions(
    db: AsyncSession,
    *,
    kind: str,
    name: str,
    limit: int,
    exclude_cwd: str | None = None,
) -> list[tuple[CodingSession, int, datetime, datetime]]:
    """(session, uses, first_used_at, last_used_at) for every run that used one
    asset, most recently used first.

    Grouped over `coding_assets` rather than the call log: the counters are the
    number of record, and one session holds a row per lane.
    """
    uses = func.sum(CodingAsset.uses)
    first_used = func.min(CodingAsset.first_seen_at)
    last_used = func.max(CodingAsset.last_seen_at)
    query = (
        select(CodingSession, uses, first_used, last_used)
        .join(CodingAsset, CodingAsset.session_id == CodingSession.id)
        .where(CodingAsset.kind == kind, CodingAsset.name == name)
    )
    if exclude_cwd is not None:
        query = query.where(CodingSession.cwd != exclude_cwd)
    result = await db.execute(
        query.group_by(CodingSession.id).order_by(last_used.desc()).limit(limit)
    )
    return [(row[0], int(row[1] or 0), row[2], row[3]) for row in result.all()]


async def asset_use_log(
    db: AsyncSession, *, kind: str, name: str, session_ids: list[str], limit: int
) -> list[CodingAssetUse]:
    """The individual calls of one asset within these runs, newest first.

    Capped across all of them together: the rows only ever back an expanded
    row, and a run that read one SKILL.md a hundred times must not be able to
    make the response a hundred times bigger.
    """
    if not session_ids:
        return []
    result = await db.execute(
        select(CodingAssetUse)
        .where(
            CodingAssetUse.kind == kind,
            CodingAssetUse.name == name,
            CodingAssetUse.session_id.in_(session_ids),
        )
        .order_by(CodingAssetUse.used_at.desc(), CodingAssetUse.id.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def clear_asset_uses(db: AsyncSession, session_id: str) -> None:
    await db.execute(delete(CodingAssetUse).where(CodingAssetUse.session_id == session_id))
    await db.flush()


# --- evidence: the envelope an agent returned, and every gate check's note ---


async def add_envelope(
    db: AsyncSession,
    *,
    session_id: str,
    phase_id: int | None,
    event_id: int | None,
    role: str | None,
    attempt: int,
    parsed: bool,
    parse_error: str | None,
    status: str | None,
    body: dict[str, Any] | None,
    raw_text: str | None,
    origin: str,
    now: datetime,
) -> CodingEnvelope:
    envelope = CodingEnvelope(
        session_id=session_id,
        phase_id=phase_id,
        event_id=event_id,
        role=role,
        attempt=attempt,
        parsed=parsed,
        parse_error=parse_error,
        status=status,
        body=body,
        raw_text=raw_text,
        origin=origin,
        created_at=now,
    )
    db.add(envelope)
    await db.flush()
    return envelope


async def add_gate_check(
    db: AsyncSession,
    *,
    session_id: str,
    phase_id: int | None,
    event_id: int | None,
    gate: str,
    attempt: int,
    item: str | None,
    ok: bool,
    note: str | None,
    origin: str,
    now: datetime,
) -> CodingGateCheck:
    check = CodingGateCheck(
        session_id=session_id,
        phase_id=phase_id,
        event_id=event_id,
        gate=gate,
        attempt=attempt,
        item=item,
        ok=ok,
        note=note,
        origin=origin,
        created_at=now,
    )
    db.add(check)
    await db.flush()
    return check


def _same(column: Any, value: Any) -> ColumnElement[bool]:
    matched: ColumnElement[bool] = column.is_(None) if value is None else column == value
    return matched


async def count_envelopes(
    db: AsyncSession, session_id: str, *, phase_id: int | None, role: str | None
) -> int:
    """How many attempts this role already made in this stage — the next one's
    number, when the producer did not state it. Nulls are matched explicitly:
    both dialects treat them as distinct in an equality test."""
    result = await db.execute(
        select(func.count())
        .select_from(CodingEnvelope)
        .where(
            CodingEnvelope.session_id == session_id,
            _same(CodingEnvelope.phase_id, phase_id),
            _same(CodingEnvelope.role, role),
        )
    )
    return result.scalar() or 0


async def count_gate_checks(
    db: AsyncSession, session_id: str, *, phase_id: int | None, gate: str, item: str | None
) -> int:
    """The same, per (stage, gate, item) — one gate re-run is one more attempt."""
    result = await db.execute(
        select(func.count())
        .select_from(CodingGateCheck)
        .where(
            CodingGateCheck.session_id == session_id,
            _same(CodingGateCheck.phase_id, phase_id),
            CodingGateCheck.gate == gate,
            _same(CodingGateCheck.item, item),
        )
    )
    return result.scalar() or 0


async def reported_evidence_event_ids(db: AsyncSession, session_id: str) -> frozenset[int]:
    """The events whose evidence arrived on the hook body.

    A replay must not mine those events again: what they reported is already
    stored, and richer than anything the stored payload could prove.
    """
    envelopes = await db.execute(
        select(CodingEnvelope.event_id).where(
            CodingEnvelope.session_id == session_id,
            CodingEnvelope.origin != EVIDENCE_RECOVERED,
            CodingEnvelope.event_id.is_not(None),
        )
    )
    checks = await db.execute(
        select(CodingGateCheck.event_id).where(
            CodingGateCheck.session_id == session_id,
            CodingGateCheck.origin != EVIDENCE_RECOVERED,
            CodingGateCheck.event_id.is_not(None),
        )
    )
    found = [*envelopes.scalars().all(), *checks.scalars().all()]
    return frozenset(i for i in found if i is not None)


async def relink_evidence(db: AsyncSession, *, event_id: int, phase_id: int | None) -> None:
    """Point one event's reported evidence at the stage a replay just rebuilt.

    A backfill drops and recreates every phase, so the row's old `phase_id` names
    a stage that no longer exists. The event is the stable handle between them.
    """
    await db.execute(
        update(CodingEnvelope).where(CodingEnvelope.event_id == event_id).values(phase_id=phase_id)
    )
    await db.execute(
        update(CodingGateCheck)
        .where(CodingGateCheck.event_id == event_id)
        .values(phase_id=phase_id)
    )
    await db.flush()


async def clear_recovered_evidence(db: AsyncSession, session_id: str) -> None:
    """Drop only what a replay wrote. Reported rows carry bodies the event
    stream never held, so rebuilding them would destroy them."""
    await db.execute(
        delete(CodingEnvelope).where(
            CodingEnvelope.session_id == session_id,
            CodingEnvelope.origin == EVIDENCE_RECOVERED,
        )
    )
    await db.execute(
        delete(CodingGateCheck).where(
            CodingGateCheck.session_id == session_id,
            CodingGateCheck.origin == EVIDENCE_RECOVERED,
        )
    )
    await db.flush()


async def envelopes_for_session(
    db: AsyncSession, session_id: str, *, limit: int
) -> list[CodingEnvelope]:
    """Oldest first, so attempt 1 precedes attempt 2. Capped at the most recent
    `limit` — an envelope body is up to 32 KB, and the cap has to bound the
    response, not the head of the run."""
    result = await db.execute(
        select(CodingEnvelope)
        .where(CodingEnvelope.session_id == session_id)
        .order_by(CodingEnvelope.id.desc())
        .limit(limit)
    )
    return list(reversed(result.scalars().all()))


async def gate_checks_for_session(
    db: AsyncSession, session_id: str, *, limit: int
) -> list[CodingGateCheck]:
    result = await db.execute(
        select(CodingGateCheck)
        .where(CodingGateCheck.session_id == session_id)
        .order_by(CodingGateCheck.id.desc())
        .limit(limit)
    )
    return list(reversed(result.scalars().all()))


async def evidence_counts(db: AsyncSession, session_id: str) -> tuple[int, int]:
    """(envelopes, gate_checks) a session holds — what a backfill reports."""
    envelopes = await db.execute(
        select(func.count())
        .select_from(CodingEnvelope)
        .where(CodingEnvelope.session_id == session_id)
    )
    checks = await db.execute(
        select(func.count())
        .select_from(CodingGateCheck)
        .where(CodingGateCheck.session_id == session_id)
    )
    return envelopes.scalar() or 0, checks.scalar() or 0


async def active_ms_by_session(db: AsyncSession, session_ids: list[str]) -> dict[str, int]:
    """Time each run spent actually working: the sum of the gaps between its
    consecutive events, with any gap longer than ACTIVE_GAP thrown away.

    Folded in Python over two columns rather than computed with a window
    function — SQLite and Postgres share no interval arithmetic, which is the
    same reason `duration_seconds` is computed in the serializer.
    """
    if not session_ids:
        return {}
    result = await db.execute(
        select(CodingEvent.session_id, CodingEvent.created_at)
        .where(CodingEvent.session_id.in_(session_ids))
        .order_by(CodingEvent.session_id, CodingEvent.id)
    )
    totals: dict[str, int] = dict.fromkeys(session_ids, 0)
    previous: dict[str, datetime] = {}
    for session_id, created_at in result.all():
        last = previous.get(session_id)
        if last is not None:
            gap = created_at - last
            if gap.total_seconds() >= 0 and gap <= ACTIVE_GAP:
                totals[session_id] += int(gap.total_seconds() * 1000)
        previous[session_id] = created_at
    return totals


async def clear_assets(db: AsyncSession, session_id: str) -> None:
    await db.execute(delete(CodingAsset).where(CodingAsset.session_id == session_id))
    await db.flush()


async def clear_derived(db: AsyncSession, session_id: str) -> None:
    """Drop the phases and lanes of one session and unlink its events.

    Unlinking first rather than leaning on ON DELETE SET NULL: SQLite only
    enforces foreign keys when the connection asks it to, and a stale phase_id
    would silently break the waterfall.
    """
    await db.execute(
        update(CodingEvent)
        .where(CodingEvent.session_id == session_id)
        .values(phase_id=None, agent=None, ok=None, duration_ms=None, ended_at=None)
    )
    await db.execute(delete(CodingPhase).where(CodingPhase.session_id == session_id))
    await db.execute(delete(CodingAgent).where(CodingAgent.session_id == session_id))
    await db.flush()


async def event_counts(db: AsyncSession, session_ids: list[str]) -> dict[str, tuple[int, int]]:
    """(event_count, tool_call_count) per session id; absent ids have no events."""
    if not session_ids:
        return {}
    result = await db.execute(
        select(
            CodingEvent.session_id,
            func.count(CodingEvent.id),
            func.count(case((CodingEvent.event_type == TOOL_CALL_EVENT, 1))),
        )
        .where(CodingEvent.session_id.in_(session_ids))
        .group_by(CodingEvent.session_id)
    )
    return {row[0]: (row[1], row[2]) for row in result.all()}
