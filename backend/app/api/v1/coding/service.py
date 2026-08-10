"""Claude Code observability: hook ingest and the read side of the Sessions screen.

Ingest sits in the critical path of every hook firing, so it stays small: an
upsert of the session, an insert of the event, and — only when the event says
something about a stage or a lane — a handful of single-row upserts against
`coding_phases` / `coding_agents`. No CLI, no network, and filesystem access
only on the first event of a session.

The promotion itself is decided in `derive`, which touches no database, so
`backfill_session` can replay stored events through the same code path.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.assets.service import parse_asset_id
from app.api.v1.coding import assets, derive, evidence, schemas, serializers
from app.config import settings
from app.core.exceptions import CodingSessionNotFoundError
from app.db.models.coding import (
    EVIDENCE_RECOVERED,
    EVIDENCE_REPORTED,
    KIND_AGENT,
    LAUNCH_AUTOMATED,
    LAUNCH_INTERACTIVE,
    MAIN_AGENT,
    PHASE_ABANDONED,
    PHASE_PASSED,
    PHASE_RUNNING,
    STATUS_RUNNING,
    STATUS_SUCCESS,
    TERMINAL_PHASE_STATUSES,
    TITLE_FACTORY,
    TITLE_PROMPT,
    TITLE_PROVENANCE,
    CodingAgent,
    CodingAssetUse,
    CodingEvent,
    CodingPhase,
    CodingSession,
)
from app.repositories import coding as coding_repo
from app.repositories import coding_analytics as analytics_repo

# A runaway hook must not be able to bloat the database; anything past this is
# replaced by a marker that keeps the head of the payload for debugging.
MAX_JSON_CHARS = 32 * 1024
PREVIEW_CHARS = 2_000

# Column widths — truncated rather than 422'd, so no event is ever lost.
MAX_SESSION_ID = 200
MAX_EVENT_TYPE = 100
MAX_TOOL_NAME = 200
MAX_MODEL = 100
MAX_CWD = 4_000
MAX_TITLE = 2_000
MAX_WORKFLOW = 50
MAX_STATUS = 20
MAX_PHASE_NAME = 100
MAX_KIND = 20
MAX_AGENT_NAME = 100
MAX_COLOR = 20
MAX_SHA = 64
MAX_ASSET_NAME = 200
MAX_USE_SOURCE = 30

# Title precedence. A factory stage child's prompt is generated boilerplate
# ("You are the BUILD stage of…"), so the provenance name that puts it under its
# parent has to beat it; the runner's own statement of the request beats both.
# An equal-ranked title never replaces one already stored — the *first* prompt
# is the request, the fifth is a follow-up.
_TITLE_RANK: dict[str | None, int] = {
    None: 0,
    TITLE_PROMPT: 1,
    TITLE_PROVENANCE: 2,
    TITLE_FACTORY: 3,
}

# `stats` stays the free-form overflow, but these keys have columns of their
# own — the card reads them without parsing JSON. Aliases because the two
# producers spell the same number differently.
PROMOTED_STATS: dict[str, str] = {
    "cost_usd": "cost_usd",
    "total_cost_usd": "cost_usd",
    "tokens_total": "tokens_total",
    "total_tokens": "tokens_total",
    "tokens_in": "tokens_in",
    "input_tokens": "tokens_in",
    "tokens_out": "tokens_out",
    "output_tokens": "tokens_out",
    "cache_read_tokens": "cache_read_tokens",
    "cache_read_input_tokens": "cache_read_tokens",
}
_INTEGER_ROLLUPS = frozenset({"tokens_total", "tokens_in", "tokens_out", "cache_read_tokens"})


@dataclass(slots=True)
class BackfillResult:
    """What a rebuild produced, for the operator who ran it."""

    session_id: str
    events: int
    phases: int
    agents: int
    assets: int
    envelopes: int
    gate_checks: int


@dataclass(slots=True)
class BackfillTotals:
    """The same, summed over a whole-history rebuild."""

    sessions: int = 0
    events: int = 0
    phases: int = 0
    agents: int = 0
    assets: int = 0
    envelopes: int = 0
    gate_checks: int = 0


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _clip(value: str | None, limit: int) -> str | None:
    return value[:limit] if value is not None else None


def _capped(value: dict[str, Any] | None) -> dict[str, Any] | None:
    """Bound one JSON blob's stored size, keeping a readable head of it."""
    if value is None:
        return None
    try:
        encoded = json.dumps(value, default=str)
    except (TypeError, ValueError):
        return {"_unserializable": True}
    if len(encoded) <= MAX_JSON_CHARS:
        return value
    return {"_truncated": True, "_chars": len(encoded), "_preview": encoded[:PREVIEW_CHARS]}


# `claude -p` is a one-shot with no one at the keyboard: a wrapper script, a hook,
# a scheduler. An interactive run never carries the flag, so its presence anywhere
# in the launcher chain is what separates a person's session from a machine's.
# `[/\s]`, not `/`: the hook writes chain entries as "<pid> claude -p …", so the
# command is bare and space-prefixed. Anchoring on a slash alone matched only the
# absolute-path form and left every headless child classified interactive.
_HEADLESS_LAUNCH = re.compile(r"(?:^|[/\s])claude\b.*?\s(?:-p|--print)(?:\s|$)")


def _launch_mode_for(chain: list[str]) -> str:
    automated = any(_HEADLESS_LAUNCH.search(e) for e in chain)
    return LAUNCH_AUTOMATED if automated else LAUNCH_INTERACTIVE


def _launcher_chain(payload: dict[str, Any] | None) -> list[str]:
    """The `launched_by` ancestry the SessionStart hook records, or nothing."""
    chain = (payload or {}).get("launched_by")
    return [e for e in chain if isinstance(e, str)] if isinstance(chain, list) else []


# The pipeline runner, seen from a stage child's ancestry. A chain entry is
# "<pid> <argv>", so the script name follows either a path separator or the
# space after the interpreter — both have to count. Its `--repo` argument is the
# fallback when the child's own event carried no cwd.
_FACTORY_LAUNCH = re.compile(r"(?:^|[/\s])factory/run\.py(?:\s|$)")
_REPO_ARG = re.compile(r"--repo[= ]+(\S+)")


def _set_title(session: CodingSession, title: str | None, source: str) -> None:
    """Keep the strongest title seen so far — see _TITLE_RANK."""
    if not title:
        return
    # Both sides default to 0: TITLE_CWD is derived at read time and so has no
    # rank here, and a future signal must not be able to turn a title into a 500.
    if session.title and _TITLE_RANK.get(source, 0) <= _TITLE_RANK.get(session.title_source, 0):
        return
    session.title = title[:MAX_TITLE]
    session.title_source = source


async def _link_provenance(
    db: AsyncSession, session: CodingSession, chain: list[str], now: datetime
) -> None:
    """Put a headless factory stage under the run that launched it.

    The runner spawns one `claude -p` per stage, so five real runs show up as
    five orphan chat cards unless the ancestry is read: a `factory/run.py`
    ancestor plus the repo it was pointed at identifies the parent run, and the
    parent's stage that was open at this moment names the child.
    """
    if session.parent_session_id is not None:
        return
    launcher = next((e for e in chain if _FACTORY_LAUNCH.search(e)), None)
    if launcher is None:
        return
    repo_arg = _REPO_ARG.search(launcher)
    repo = session.cwd or (repo_arg.group(1) if repo_arg else "")
    if not repo:
        return

    parent = await coding_repo.factory_session_at(db, cwd=repo, at=now)
    if parent is None or parent.id == session.id:
        return
    session.parent_session_id = parent.id
    stage = await coding_repo.phase_at(db, parent.id, at=now)
    _set_title(
        session,
        f"{stage.name} stage · {parent.id}" if stage is not None else f"stage · {parent.id}",
        TITLE_PROVENANCE,
    )


def _git_repo_for(cwd: str | None) -> str | None:
    """Nearest ancestor holding a .git, by folder name. A handful of stats, and
    only on the first event of a session — no subprocess, no remote lookup."""
    if not cwd:
        return None
    try:
        path = Path(cwd)
        for candidate in (path, *path.parents):
            if (candidate / ".git").exists():
                return candidate.name or None
    except OSError:
        return None
    return None


def _merge_stats(existing: dict[str, Any] | None, incoming: dict[str, Any]) -> dict[str, Any]:
    """Shallow merge, newest wins. Reassigned (not mutated) so SQLAlchemy sees it."""
    merged = {**(existing or {}), **incoming}
    capped = _capped(merged)
    # Only possible when the merge itself overflowed; the newest values win.
    return capped if capped is not None else incoming


def _promote_stats(session: CodingSession, stats: dict[str, Any] | None) -> None:
    """Copy the counters that have columns out of the free-form blob."""
    for key, column in PROMOTED_STATS.items():
        value = (stats or {}).get(key)
        if not isinstance(value, int | float) or isinstance(value, bool):
            continue
        setattr(session, column, int(value) if column in _INTEGER_ROLLUPS else float(value))


async def _rollup_phases(db: AsyncSession, session: CodingSession) -> None:
    """The run's totals are the sum of its stages — but only where the stages
    have something to say, so a hook-reported total survives a stage that does
    not track cost."""
    cost, tokens_in, tokens_out = await coding_repo.phase_rollup(db, session.id)
    if cost is not None:
        session.cost_usd = cost
    if tokens_in is not None:
        session.tokens_in = tokens_in
    if tokens_out is not None:
        session.tokens_out = tokens_out
    if tokens_in is not None or tokens_out is not None:
        session.tokens_total = (tokens_in or 0) + (tokens_out or 0)


def _close_phase(phase: CodingPhase, now: datetime) -> None:
    phase.ended_at = now
    if phase.duration_ms is None:
        phase.duration_ms = max(int((now - phase.started_at).total_seconds() * 1000), 0)


async def _upsert_phase(
    db: AsyncSession, session_id: str, write: derive.PhaseWrite, now: datetime
) -> CodingPhase | None:
    """Find the stage this event is about, or start it; then apply what the
    event knows. A None field is silence, not an instruction to clear."""
    phase: CodingPhase | None = None
    if write.seq is not None:
        phase = await coding_repo.get_phase_by_seq(db, session_id, write.seq)
    if phase is None and write.name:
        phase = await coding_repo.get_phase_by_name(db, session_id, write.name)
    if phase is None:
        if not write.name:
            return None
        seq = (
            write.seq if write.seq is not None else await coding_repo.next_phase_seq(db, session_id)
        )
        phase = await coding_repo.create_phase(
            db,
            session_id=session_id,
            seq=seq,
            name=write.name[:MAX_PHASE_NAME],
            started_at=now,
        )

    if write.name:
        phase.name = write.name[:MAX_PHASE_NAME]
    if write.kind:
        phase.kind = write.kind[:MAX_KIND]
    if write.agent:
        phase.agent = write.agent[:MAX_AGENT_NAME]
    if write.description is not None:
        phase.description = write.description
    if write.status:
        phase.status = write.status[:MAX_STATUS]
    if write.duration_ms is not None:
        phase.duration_ms = write.duration_ms
    if write.cost_usd is not None:
        phase.cost_usd = write.cost_usd
    if write.tokens_in is not None:
        phase.tokens_in = write.tokens_in
    if write.tokens_out is not None:
        phase.tokens_out = write.tokens_out
    if write.corrections is not None:
        phase.corrections = write.corrections
    if write.commit_sha:
        phase.commit_sha = write.commit_sha[:MAX_SHA]

    if write.add_tokens_in:
        phase.tokens_in = (phase.tokens_in or 0) + write.add_tokens_in
    if write.add_tokens_out:
        phase.tokens_out = (phase.tokens_out or 0) + write.add_tokens_out
    phase.gates_passed += write.add_gates_passed
    phase.gates_failed += write.add_gates_failed

    if phase.status in TERMINAL_PHASE_STATUSES and phase.ended_at is None:
        _close_phase(phase, now)
    return phase


async def _upsert_agent(
    db: AsyncSession, session: CodingSession, write: derive.AgentWrite
) -> CodingAgent:
    name = write.name[:MAX_AGENT_NAME]
    agent = await coding_repo.get_agent(db, session.id, name)
    if agent is None:
        agent = await coding_repo.create_agent(db, session_id=session.id, name=name)

    if write.model:
        agent.model = write.model[:MAX_MODEL]
    elif agent.model is None and name == MAIN_AGENT:
        # The main lane is the session itself, so it runs the session's model.
        agent.model = session.model
    if write.color:
        agent.color = write.color[:MAX_COLOR]
    if write.context_tokens is not None:
        agent.context_tokens = write.context_tokens
    if write.context_window is not None:
        agent.context_window = write.context_window
    if write.cost_usd is not None:
        agent.cost_usd = write.cost_usd
    if write.tokens_in is not None:
        agent.tokens_in = write.tokens_in
    if write.tokens_out is not None:
        agent.tokens_out = write.tokens_out

    agent.turns += write.add_turns
    if write.add_cost_usd:
        agent.cost_usd = (agent.cost_usd or 0.0) + write.add_cost_usd
    if write.add_tokens_in:
        agent.tokens_in = (agent.tokens_in or 0) + write.add_tokens_in
    if write.add_tokens_out:
        agent.tokens_out = (agent.tokens_out or 0) + write.add_tokens_out
    return agent


async def _count_asset(
    db: AsyncSession, session_id: str, use: assets.AssetUse, now: datetime
) -> None:
    """One more use of one asset by one lane: the counter, and the call itself."""
    name = use.name[:MAX_ASSET_NAME]
    lane = _clip(use.lane, MAX_AGENT_NAME)
    asset = await coding_repo.get_asset(db, session_id, kind=use.kind, name=name, lane=lane)
    if asset is None:
        asset = await coding_repo.create_asset(
            db, session_id=session_id, kind=use.kind, name=name, lane=lane, now=now
        )
    asset.uses += 1
    # A replay walks the stream in order, but a live hook can arrive out of it.
    asset.first_seen_at = min(asset.first_seen_at, now)
    asset.last_seen_at = max(asset.last_seen_at, now)
    await coding_repo.add_asset_use(
        db,
        session_id=session_id,
        kind=use.kind,
        name=name,
        lane=lane,
        source=use.source[:MAX_USE_SOURCE],
        input=use.input,
        now=now,
    )


async def _open_turn(
    db: AsyncSession, session: CodingSession, derived: derive.Derived, now: datetime
) -> CodingPhase:
    """A plain session has no stages, so one round trip becomes one: a prompt on
    `main`, a spawn call on a subagent's lane. Turns are numbered per lane —
    `seq` is session-wide, and once lanes interleave it skips."""
    lane = derived.lane or MAIN_AGENT
    await _abandon_open_turn(db, session, lane, now)
    seq = await coding_repo.next_phase_seq(db, session.id)
    turn = await coding_repo.phase_count(db, session.id, agent=lane) + 1
    label = derived.turn_label or f"turn {turn}"
    phase = await coding_repo.create_phase(
        db, session_id=session.id, seq=seq, name=label[:MAX_PHASE_NAME], started_at=now
    )
    phase.kind = KIND_AGENT
    phase.agent = lane[:MAX_AGENT_NAME]
    phase.status = PHASE_RUNNING
    # Why this turn exists. Most turns of a long session are not a person
    # typing — a background task finishing re-enters as a prompt too — and a
    # stage with no stated cause reads as one that started for no reason.
    phase.description = derived.turn_detail
    return phase


async def _abandon_open_turn(
    db: AsyncSession, session: CodingSession, lane: str, now: datetime
) -> None:
    """Close a turn on `main` that the next prompt overtook.

    A dropped `Stop` hook used to leave its phase `running` forever, so one turn
    claimed the rest of the run and every later one drew on top of it. The
    prompt that opens the next turn is proof the previous one had ended, so it
    is closed here — as `abandoned`, because *when* it ended is still unknown.

    Only `main`: `parallel()` spawns several agents of the same type at once, so
    two open turns on a subagent lane are two agents working, not a lost hook.
    """
    if lane != MAIN_AGENT:
        return
    phase = await coding_repo.open_phase(db, session.id, agent=lane)
    if phase is None:
        return
    # The status carries the whole claim; `description` says what *started* the
    # turn, which is still true and more useful than how it ended.
    phase.status = PHASE_ABANDONED
    _close_phase(phase, now)


async def _mark_end(
    db: AsyncSession, session: CodingSession, derived: derive.Derived, now: datetime
) -> CodingPhase:
    """A finish with no recorded start. Sessions from before the spawn hook have
    a `SubagentStop` and nothing before it, so the lane gets an instant at the
    moment it ended rather than a blank row — the honest shape of what is known.
    """
    phase = await _open_turn(db, session, derived, now)
    phase.description = "start not recorded — spawn hook was not installed"
    phase.status = PHASE_PASSED
    phase.duration_ms = 0
    _close_phase(phase, now)
    return phase


async def _resolve_phase(
    db: AsyncSession, session: CodingSession, derived: derive.Derived, now: datetime
) -> CodingPhase | None:
    if derived.phase is not None:
        return await _upsert_phase(db, session.id, derived.phase, now)
    if derived.opens_turn:
        return await _open_turn(db, session, derived, now)

    # Anything else belongs to whatever is currently running in its own lane.
    phase = await coding_repo.open_phase(db, session.id, agent=derived.lane)
    if derived.closes_turn:
        # Only ever its own lane's stage: a `Stop` that closed the newest open
        # phase would end a subagent's span the moment `main` finished a turn.
        if phase is None:
            no_start = derived.lane is not None and derived.lane != MAIN_AGENT
            return await _mark_end(db, session, derived, now) if no_start else None
        phase.status = PHASE_PASSED
        _close_phase(phase, now)
        return phase

    # An event only looking for a home takes its lane's stage, else whatever is
    # open — a producer that names stages but not lanes has nothing for `main`
    # to match against, and its tool calls still belong to the stage.
    return phase or await coding_repo.open_phase(db, session.id)


async def _record_evidence(
    db: AsyncSession,
    event: CodingEvent,
    *,
    payload: dict[str, Any] | None,
    phase_id: int | None,
    lane: str | None,
    reported: evidence.Evidence | None,
    replayed: frozenset[int] | None,
    now: datetime,
) -> None:
    """Store the claims and verdicts this event carried.

    One event yields evidence from exactly one source, which is what keeps the
    two from doubling up: a producer that stated blocks is taken at its word and
    is never also mined, and a replay re-points what such an event already
    reported instead of mining it a second time.
    """
    if replayed is not None and event.id in replayed:
        await coding_repo.relink_evidence(db, event_id=event.id, phase_id=phase_id)
        return
    if reported is not None and not reported.empty:
        found, origin = reported, EVIDENCE_REPORTED
    else:
        found = evidence.from_event(event.event_type, payload, lane=lane)
        origin = EVIDENCE_RECOVERED
    if found.empty:
        return

    if (envelope := found.envelope) is not None:
        attempt = envelope.attempt or 1 + await coding_repo.count_envelopes(
            db, event.session_id, phase_id=phase_id, role=envelope.role
        )
        await coding_repo.add_envelope(
            db,
            session_id=event.session_id,
            phase_id=phase_id,
            event_id=event.id,
            role=envelope.role,
            attempt=attempt,
            parsed=envelope.parsed,
            parse_error=envelope.parse_error,
            status=envelope.status,
            body=_capped(envelope.body),
            raw_text=envelope.raw_text,
            origin=origin,
            now=now,
        )
    for check in found.checks:
        attempt = check.attempt or 1 + await coding_repo.count_gate_checks(
            db, event.session_id, phase_id=phase_id, gate=check.gate, item=check.item
        )
        await coding_repo.add_gate_check(
            db,
            session_id=event.session_id,
            phase_id=phase_id,
            event_id=event.id,
            gate=check.gate,
            attempt=attempt,
            item=check.item,
            ok=check.ok,
            note=check.note,
            origin=origin,
            now=now,
        )


async def _apply_derived(
    db: AsyncSession,
    session: CodingSession,
    event: CodingEvent,
    derived: derive.Derived,
    now: datetime,
    *,
    payload: dict[str, Any] | None,
    reported: evidence.Evidence | None = None,
    replayed: frozenset[int] | None = None,
) -> None:
    """Write down what the event implied, and point the event at it.

    `payload` is passed separately from `event.payload` so the live ingest reads
    the hook's own body while a replay reads the stored (capped) copy.

    `reported` is the evidence the hook body stated, which only a live ingest
    has; `replayed` is the set of events that already hold such evidence, which
    only a replay passes — the hook body is not stored, so a rebuild has to
    preserve those rows rather than recreate them.
    """
    # Provenance rides the SessionStart payload, and is re-read on every replay
    # so a rebuilt session gets the same parent and title a live one would.
    if chain := _launcher_chain(payload):
        session.launch_mode = _launch_mode_for(chain)
        await _link_provenance(db, session, chain, now)

    _set_title(session, derived.title, derived.title_source or TITLE_PROMPT)
    if derived.workflow:
        session.workflow = derived.workflow[:MAX_WORKFLOW]
    if derived.status:
        session.status = derived.status[:MAX_STATUS]

    phase = await _resolve_phase(db, session, derived, now)
    for write in derived.agents:
        if write.name:
            await _upsert_agent(db, session, write)

    event.phase_id = phase.id if phase is not None else None
    event.agent = _clip(derived.lane, MAX_AGENT_NAME)
    event.ok = derived.ok
    event.duration_ms = derived.duration_ms
    # A hook that reports a duration reports work that has just finished, so the
    # event's own timestamp is when it ended and the span starts before it.
    event.ended_at = event.created_at if derived.duration_ms is not None else None

    for use in assets.from_event(event.event_type, event.tool_name, payload, lane=derived.lane):
        await _count_asset(db, session.id, use, now)

    await _record_evidence(
        db,
        event,
        payload=payload,
        phase_id=event.phase_id,
        lane=derived.lane,
        reported=reported,
        replayed=replayed,
        now=now,
    )

    if derived.stats:
        _promote_stats(session, derived.stats)
    if phase is not None:
        await _rollup_phases(db, session)


async def _apply(db: AsyncSession, body: schemas.HookEventRequest) -> None:
    now = _utcnow()
    session_id = body.session_id[:MAX_SESSION_ID]
    session = await coding_repo.get_session(db, session_id)

    if session is None:
        cwd = (body.cwd or "")[:MAX_CWD]
        session = await coding_repo.create_session(
            db,
            session_id=session_id,
            cwd=cwd,
            git_repo=_clip(_git_repo_for(cwd), MAX_SESSION_ID),
            model=_clip(body.model, MAX_MODEL),
            now=now,
        )
    else:
        # cwd is fixed for a session, so only fill it if the first event lacked it.
        if not session.cwd and body.cwd:
            session.cwd = body.cwd[:MAX_CWD]
            session.git_repo = _clip(_git_repo_for(session.cwd), MAX_SESSION_ID)
        if body.model:  # /model mid-session: latest wins
            session.model = body.model[:MAX_MODEL]

    session.last_event_at = now
    if body.ended:
        session.ended_at = now
        if session.status == STATUS_RUNNING:
            session.status = STATUS_SUCCESS
    if body.stats is not None:
        capped = _capped(body.stats)
        session.stats = _merge_stats(session.stats, capped or {})
        _promote_stats(session, capped)

    event = await coding_repo.add_event(
        db,
        session_id=session_id,
        event_type=body.event_type[:MAX_EVENT_TYPE],
        tool_name=_clip(body.tool_name, MAX_TOOL_NAME),
        payload=_capped(body.payload),
        now=now,
    )
    derived = derive.from_event(body.event_type, body.tool_name, body.payload, body=body)
    await _apply_derived(
        db,
        session,
        event,
        derived,
        now,
        payload=body.payload,
        reported=evidence.from_body(body, lane=derived.lane),
    )


async def ingest_event(db: AsyncSession, body: schemas.HookEventRequest) -> None:
    try:
        await _apply(db, body)
        await db.commit()
    except IntegrityError:
        # Parallel tool calls fire hooks concurrently; the loser of the race to
        # create the session row retries against the row the winner inserted.
        await db.rollback()
        await _apply(db, body)
        await db.commit()


async def backfill_session(db: AsyncSession, session_id: str) -> BackfillResult:
    """Rebuild one session's stages and lanes from the events already stored.

    Sessions recorded before v1.13 kept phase, agent, cost and tokens inside
    `coding_events.payload`, and ones recorded before v1.14 have no assets at
    all; replaying them through the live derivation gives them the same shape a
    new run gets. Idempotent by construction — the derived rows are dropped and
    rebuilt rather than updated, which is what stops the counters (gates, turns,
    uses) doubling on a second run.
    """
    session = await coding_repo.get_session(db, session_id)
    if session is None:
        raise CodingSessionNotFoundError(f"unknown coding session: {session_id}")

    # A status the producer stated on the hook *body* is not in the event stream
    # — only `payload` is stored — so the replay cannot re-derive it. `failed`
    # and `success` come back from the factory's own payload; `interrupted` only
    # ever arrives this way, and without this a rebuild would quietly downgrade
    # a reported outcome to `success`.
    reported_status = session.status
    # Evidence is reported, not derived: an envelope body never entered the
    # event stream, so only what a previous replay recovered may be dropped.
    # The survivors are re-pointed at their rebuilt stage during the replay.
    replayed = await coding_repo.reported_evidence_event_ids(db, session_id)
    await coding_repo.clear_recovered_evidence(db, session_id)
    await coding_repo.clear_derived(db, session_id)
    await coding_repo.clear_assets(db, session_id)
    await coding_repo.clear_asset_uses(db, session_id)
    session.title = None
    session.title_source = None
    session.parent_session_id = None
    session.workflow = None
    session.status = STATUS_RUNNING
    session.cost_usd = None
    session.tokens_total = None
    session.tokens_in = None
    session.tokens_out = None
    session.cache_read_tokens = None
    _promote_stats(session, session.stats)

    events = await coding_repo.session_events(db, session_id)
    for event in events:
        derived = derive.from_event(event.event_type, event.tool_name, event.payload)
        await _apply_derived(
            db,
            session,
            event,
            derived,
            event.created_at,
            payload=event.payload,
            replayed=replayed,
        )

    if session.status == STATUS_RUNNING and reported_status != STATUS_RUNNING:
        session.status = reported_status
    # A replayed stream has no `ended` flag; the stored timestamp says the same.
    if session.ended_at is not None and session.status == STATUS_RUNNING:
        session.status = STATUS_SUCCESS

    phases = await coding_repo.phases_by_session(db, [session_id])
    agents = await coding_repo.agents_by_session(db, [session_id])
    session_assets = await coding_repo.assets_by_session(db, [session_id])
    envelopes, gate_checks = await coding_repo.evidence_counts(db, session_id)
    await db.commit()
    return BackfillResult(
        session_id=session_id,
        events=len(events),
        phases=len(phases[session_id]),
        agents=len(agents[session_id]),
        assets=len(session_assets[session_id]),
        envelopes=envelopes,
        gate_checks=gate_checks,
    )


async def backfill_all(db: AsyncSession) -> BackfillTotals:
    """Replay every stored session, oldest first.

    Oldest first so a pipeline run's stages exist by the time its headless
    children look for the stage they belong to. Idempotent for the same reason
    one session is: each is dropped and rebuilt, not updated.
    """
    totals = BackfillTotals()
    for session_id in await coding_repo.all_session_ids(db):
        result = await backfill_session(db, session_id)
        totals.sessions += 1
        totals.events += result.events
        totals.phases += result.phases
        totals.agents += result.agents
        totals.assets += result.assets
        totals.envelopes += result.envelopes
        totals.gate_checks += result.gate_checks
    return totals


async def get_session_or_404(db: AsyncSession, session_id: str) -> CodingSession:
    session = await coding_repo.get_session(db, session_id)
    if session is None:
        raise CodingSessionNotFoundError(f"unknown coding session: {session_id}")
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
) -> list[schemas.CodingSession]:
    sessions = await coding_repo.list_sessions(
        db,
        limit=limit,
        offset=offset,
        include_empty=include_empty,
        include_automated=include_automated,
        workflow=workflow,
        status=status,
        roots_only=roots_only,
        parent_session_id=parent_session_id,
    )
    ids = [s.id for s in sessions]
    counts = await coding_repo.event_counts(db, ids)
    phases = await coding_repo.phases_by_session(db, ids)
    agents = await coding_repo.agents_by_session(db, ids)
    session_assets = await coding_repo.assets_by_session(db, ids)
    children = await coding_repo.child_counts(db, ids)
    child_assets = await analytics_repo.child_assets_by_parent(db, ids)
    active = await coding_repo.active_ms_by_session(db, ids)
    now = _utcnow()
    return [
        serializers.coding_session_to_schema(
            s,
            event_count=counts.get(s.id, (0, 0))[0],
            tool_call_count=counts.get(s.id, (0, 0))[1],
            phases=phases[s.id],
            agents=agents[s.id],
            assets=session_assets[s.id],
            child_assets=child_assets[s.id],
            child_count=children.get(s.id, 0),
            active_ms=active.get(s.id, 0),
            now=now,
        )
        for s in sessions
    ]


# The evidence a detail response carries. An envelope body runs to 32 KB, so the
# two caps differ by an order of magnitude: a run has a handful of attempts per
# stage and a great many checks, and neither array may make the page unloadable.
MAX_DETAIL_ENVELOPES = 100
MAX_DETAIL_GATE_CHECKS = 500


async def get_session(db: AsyncSession, session_id: str) -> schemas.CodingSessionDetail:
    session = await get_session_or_404(db, session_id)
    counts = await coding_repo.event_counts(db, [session_id])
    events, tool_calls = counts.get(session_id, (0, 0))
    phases = await coding_repo.phases_by_session(db, [session_id])
    agents = await coding_repo.agents_by_session(db, [session_id])
    session_assets = await coding_repo.assets_by_session(db, [session_id])
    children = await coding_repo.child_counts(db, [session_id])
    child_assets = await analytics_repo.child_assets_by_parent(db, [session_id])
    active = await coding_repo.active_ms_by_session(db, [session_id])
    envelopes = await coding_repo.envelopes_for_session(db, session_id, limit=MAX_DETAIL_ENVELOPES)
    gate_checks = await coding_repo.gate_checks_for_session(
        db, session_id, limit=MAX_DETAIL_GATE_CHECKS
    )
    return serializers.coding_session_to_detail(
        session,
        event_count=events,
        tool_call_count=tool_calls,
        phases=phases[session_id],
        agents=agents[session_id],
        assets=session_assets[session_id],
        child_assets=child_assets[session_id],
        child_count=children.get(session_id, 0),
        active_ms=active.get(session_id, 0),
        envelopes=envelopes,
        gate_checks=gate_checks,
        now=_utcnow(),
    )


# masterwork runs its own analysis passes — simulations, diagrams, trigger guides —
# as `claude -p` with ~/.claude as the working directory, and each one Reads every
# linked asset's SKILL.md. Those reads look exactly like a skill being used, so
# counting them would rank assets by how often masterwork inspected them: 14 of the
# first 22 recorded skill uses came from here. The rollup leaves them out by default.
INSPECTION_CWD = str(settings.claude_skills_root.parent)


async def list_asset_usage(
    db: AsyncSession,
    *,
    kind: str | None = None,
    since: datetime | None = None,
    include_inspection: bool = False,
) -> list[schemas.CodingAssetUsage]:
    rows = await coding_repo.asset_usage(
        db,
        kind=kind,
        since=since,
        exclude_cwd=None if include_inspection else INSPECTION_CWD,
    )
    return [serializers.asset_usage_to_schema(row) for row in rows]


# One expanded row shows a handful of calls, so the log is fetched for the whole
# page at once and capped there: a single run that read one SKILL.md two hundred
# times must not be able to make the response two hundred times bigger.
MAX_ASSET_CALLS = 200


async def list_asset_sessions(
    db: AsyncSession,
    asset_id: str,
    *,
    limit: int = 50,
    include_inspection: bool = False,
) -> list[schemas.AssetSessionUse]:
    """The runs that used one asset, newest first, each with its calls.

    Matched on (kind, name) rather than the whole id: a plugin skill is recorded
    under the name Claude Code calls it by ("vercel:bootstrap") while its asset
    id names the provider that installed it ("claude-plugin:skill:…").
    """
    _, kind, name = parse_asset_id(asset_id)
    rows = await coding_repo.asset_sessions(
        db,
        kind=kind,
        name=name,
        limit=limit,
        exclude_cwd=None if include_inspection else INSPECTION_CWD,
    )
    if not rows:
        return []
    calls = await coding_repo.asset_use_log(
        db,
        kind=kind,
        name=name,
        session_ids=[session.id for session, *_ in rows],
        limit=MAX_ASSET_CALLS,
    )
    by_session: dict[str, list[CodingAssetUse]] = {}
    for call in calls:
        by_session.setdefault(call.session_id, []).append(call)

    now = _utcnow()
    return [
        serializers.asset_session_use_to_schema(
            session,
            uses=uses,
            first_used_at=first_used_at,
            last_used_at=last_used_at,
            calls=by_session.get(session.id, []),
            now=now,
        )
        for session, uses, first_used_at, last_used_at in rows
    ]


async def list_events(
    db: AsyncSession, session_id: str, *, after: int, limit: int
) -> list[schemas.CodingEvent]:
    await get_session_or_404(db, session_id)
    events = await coding_repo.list_events(db, session_id, after=after, limit=limit)
    return [serializers.coding_event_to_schema(e) for e in events]
