"""Cross-run aggregates over the coding-observability tables.

Everything a single run reports is already queryable per run; nothing was ever
counted *across* runs, which is the only way to answer "which gate keeps failing
on which role" or "was the expensive model worth it". These are the GROUP BYs.

Two rules the whole module obeys:

- **Rows come back positionally, into frozen dataclasses.** A labelled column
  named `count` or `index` collides with a method on SQLAlchemy's `Row`, and
  these queries are nothing but counts.
- **No interval arithmetic in SQL.** `func.now() - <timedelta>` silently never
  matches on SQLite, so every cutoff arrives as a parameter computed in Python.

Denominators are returned beside every numerator and no rate is computed here:
a "100% failure rate" over one check is noise, and the caller can only say so if
it is given the one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Select, case, distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.coding import (
    PHASE_FAILED,
    STATUS_SUCCESS,
    WORKFLOW_CHAT,
    CodingAgent,
    CodingAsset,
    CodingEnvelope,
    CodingGateCheck,
    CodingPhase,
    CodingSession,
)


@dataclass(frozen=True, slots=True)
class Scope:
    """The population an aggregate counts over. One object, because all four
    endpoints take the same four filters and a partially-applied scope is a bug
    that reads as a number."""

    since: datetime | None = None
    workflow: str | None = None
    # masterwork's own inspection runs, dropped unless the caller asks for them
    # — see INSPECTION_CWD in the coding service.
    exclude_cwd: str | None = None
    # A headless stage child is the inside view of a stage already counted on
    # its parent, so counting both reports the same work twice.
    include_children: bool = False


def session_conditions(scope: Scope) -> list[ColumnElement[bool]]:
    """The scope's session-level filters, as WHERE clauses."""
    conditions: list[ColumnElement[bool]] = []
    if scope.exclude_cwd is not None:
        conditions.append(CodingSession.cwd != scope.exclude_cwd)
    if not scope.include_children:
        conditions.append(CodingSession.parent_session_id.is_(None))
    if scope.workflow is not None:
        # Nothing writes "chat"; a plain session simply never claimed a workflow.
        conditions.append(
            or_(CodingSession.workflow == scope.workflow, CodingSession.workflow.is_(None))
            if scope.workflow == WORKFLOW_CHAT
            else CodingSession.workflow == scope.workflow
        )
    return conditions


def _counted(condition: ColumnElement[bool]) -> ColumnElement[int]:
    """How many rows match, spelled so both dialects accept it: COUNT ignores
    the nulls the CASE returns for the rows that do not."""
    return func.count(case((condition, 1)))


def _counted_runs(condition: ColumnElement[bool]) -> ColumnElement[int]:
    """The same, over distinct sessions."""
    return func.count(distinct(case((condition, CodingSession.id))))


def _scoped(query: Select[Any], conditions: list[ColumnElement[bool]]) -> Select[Any]:
    return query.where(*conditions) if conditions else query


# ------------------------------------------------------ gates, and where ---


@dataclass(frozen=True, slots=True)
class GateRoleRow:
    """One (gate, role) pair: how often it ran and how often it said no."""

    gate: str
    role: str | None
    checks: int
    failures: int
    runs: int


@dataclass(frozen=True, slots=True)
class GateNoteRow:
    """One distinct failure sentence a gate wrote, and how often it wrote it."""

    gate: str
    role: str | None
    note: str
    occurrences: int
    last_seen_at: datetime


async def gate_rows(
    db: AsyncSession,
    *,
    scope: Scope,
) -> list[GateRoleRow]:
    """Checks and failures per (gate, role), counted inside the window.

    The role is the lane the check's stage belonged to — null when the check
    resolved no stage, which the ingest allows on purpose (a gate that fired
    before any `phase_start` is a real case).
    """
    checks = func.count(CodingGateCheck.id)
    failures = _counted(CodingGateCheck.ok.is_(False))
    runs = func.count(distinct(CodingGateCheck.session_id))
    query = (
        select(CodingGateCheck.gate, CodingPhase.agent, checks, failures, runs)
        .join(CodingSession, CodingSession.id == CodingGateCheck.session_id)
        .join(CodingPhase, CodingPhase.id == CodingGateCheck.phase_id, isouter=True)
        .group_by(CodingGateCheck.gate, CodingPhase.agent)
        .order_by(CodingGateCheck.gate, CodingPhase.agent)
    )
    query = _scoped(query, session_conditions(scope))
    if scope.since is not None:
        query = query.where(CodingGateCheck.created_at >= scope.since)
    result = await db.execute(query)
    return [
        GateRoleRow(
            gate=row[0], role=row[1], checks=int(row[2]), failures=int(row[3]), runs=int(row[4])
        )
        for row in result.all()
    ]


async def gate_total_rows(
    db: AsyncSession,
    *,
    scope: Scope,
) -> list[GateRoleRow]:
    """The same, per gate only — `role` is always None on these rows.

    A separate GROUP BY rather than a fold of `gate_rows`: `runs` counts
    distinct sessions, and a run whose gate fired on two roles would be counted
    twice by any sum of the per-role rows.
    """
    checks = func.count(CodingGateCheck.id)
    failures = _counted(CodingGateCheck.ok.is_(False))
    runs = func.count(distinct(CodingGateCheck.session_id))
    query = (
        select(CodingGateCheck.gate, checks, failures, runs)
        .join(CodingSession, CodingSession.id == CodingGateCheck.session_id)
        .group_by(CodingGateCheck.gate)
        .order_by(failures.desc(), checks.desc(), CodingGateCheck.gate)
    )
    query = _scoped(query, session_conditions(scope))
    if scope.since is not None:
        query = query.where(CodingGateCheck.created_at >= scope.since)
    result = await db.execute(query)
    return [
        GateRoleRow(
            gate=row[0], role=None, checks=int(row[1]), failures=int(row[2]), runs=int(row[3])
        )
        for row in result.all()
    ]


async def gate_note_rows(
    db: AsyncSession,
    *,
    scope: Scope,
) -> list[GateNoteRow]:
    """The failing notes, grouped verbatim by (gate, role, note), commonest first.

    Deliberately not normalized: a gate's note usually names the files it is
    about, so most counts are 1 and the list reads as the most recent distinct
    failures. Collapsing two sentences into one bucket would be a claim about
    them being the same failure, which masterwork cannot make.
    """
    occurrences = func.count(CodingGateCheck.id)
    last_seen = func.max(CodingGateCheck.created_at)
    query = (
        select(
            CodingGateCheck.gate, CodingPhase.agent, CodingGateCheck.note, occurrences, last_seen
        )
        .join(CodingSession, CodingSession.id == CodingGateCheck.session_id)
        .join(CodingPhase, CodingPhase.id == CodingGateCheck.phase_id, isouter=True)
        .where(CodingGateCheck.ok.is_(False), CodingGateCheck.note.is_not(None))
        .group_by(CodingGateCheck.gate, CodingPhase.agent, CodingGateCheck.note)
        .order_by(CodingGateCheck.gate, occurrences.desc(), last_seen.desc())
    )
    query = _scoped(query, session_conditions(scope))
    if scope.since is not None:
        query = query.where(CodingGateCheck.created_at >= scope.since)
    result = await db.execute(query)
    return [
        GateNoteRow(
            gate=row[0],
            role=row[1],
            note=row[2],
            occurrences=int(row[3]),
            last_seen_at=row[4],
        )
        for row in result.all()
    ]


# ------------------------------------------------ roles, and what they cost ---


@dataclass(frozen=True, slots=True)
class RoleStageRow:
    """One role's stages, summed. Every average's denominator is here too."""

    role: str | None
    runs: int
    stages: int
    corrections: int
    failed_stages: int
    timed_stages: int
    duration_ms: int
    costed_stages: int
    cost_usd: float
    tokens_in: int
    tokens_out: int
    gates_passed: int
    gates_failed: int


@dataclass(frozen=True, slots=True)
class RoleEnvelopeRow:
    """One role's envelope attempts, and the ones that did not parse."""

    role: str | None
    attempts: int
    failures: int


async def role_stage_rows(
    db: AsyncSession,
    *,
    scope: Scope,
) -> list[RoleStageRow]:
    """Per lane, over the stages that STARTED inside the window.

    The stage's own clock, not its run's: a window has to count the work done
    inside it, and a stage is the unit of work here.

    Gate figures come from the phase counters rather than the v1.19 check rows,
    because the counters exist for every run ever recorded — see the note on
    `gate_rows`, which can only see runs whose evidence was reported or replayed.
    """
    query = (
        select(
            CodingPhase.agent,
            func.count(distinct(CodingPhase.session_id)),
            func.count(CodingPhase.id),
            func.sum(CodingPhase.corrections),
            _counted(CodingPhase.status == PHASE_FAILED),
            func.count(CodingPhase.duration_ms),
            func.sum(CodingPhase.duration_ms),
            func.count(CodingPhase.cost_usd),
            func.sum(CodingPhase.cost_usd),
            func.sum(CodingPhase.tokens_in),
            func.sum(CodingPhase.tokens_out),
            func.sum(CodingPhase.gates_passed),
            func.sum(CodingPhase.gates_failed),
        )
        .join(CodingSession, CodingSession.id == CodingPhase.session_id)
        .group_by(CodingPhase.agent)
    )
    query = _scoped(query, session_conditions(scope))
    if scope.since is not None:
        query = query.where(CodingPhase.started_at >= scope.since)
    result = await db.execute(query)
    return [
        RoleStageRow(
            role=row[0],
            runs=int(row[1]),
            stages=int(row[2]),
            corrections=int(row[3] or 0),
            failed_stages=int(row[4]),
            timed_stages=int(row[5]),
            duration_ms=int(row[6] or 0),
            costed_stages=int(row[7]),
            cost_usd=float(row[8] or 0.0),
            tokens_in=int(row[9] or 0),
            tokens_out=int(row[10] or 0),
            gates_passed=int(row[11] or 0),
            gates_failed=int(row[12] or 0),
        )
        for row in result.all()
    ]


async def role_envelope_rows(
    db: AsyncSession,
    *,
    scope: Scope,
) -> list[RoleEnvelopeRow]:
    """Envelope attempts per role, keyed on the role the envelope itself named.

    A role that repeatedly fails to emit valid JSON has a contract problem, and
    this is the only place the failed attempts are counted — they cost a
    correction round each and leave no other trace.
    """
    attempts = func.count(CodingEnvelope.id)
    failures = _counted(CodingEnvelope.parsed.is_(False))
    query = (
        select(CodingEnvelope.role, attempts, failures)
        .join(CodingSession, CodingSession.id == CodingEnvelope.session_id)
        .group_by(CodingEnvelope.role)
    )
    query = _scoped(query, session_conditions(scope))
    if scope.since is not None:
        query = query.where(CodingEnvelope.created_at >= scope.since)
    result = await db.execute(query)
    return [
        RoleEnvelopeRow(role=row[0], attempts=int(row[1]), failures=int(row[2]))
        for row in result.all()
    ]


# ------------------------------------------------------------ runs in time ---


async def trend_sessions(
    db: AsyncSession,
    *,
    scope: Scope,
    limit: int,
) -> list[CodingSession]:
    """The most recent `limit` runs that started inside the window, oldest first.

    Newest-first in SQL and reversed here: a trend wants the *latest* runs, and
    reading them oldest-first is what lets a client plot them left to right
    without re-sorting.
    """
    query = select(CodingSession)
    query = _scoped(query, session_conditions(scope))
    if scope.since is not None:
        query = query.where(CodingSession.started_at >= scope.since)
    result = await db.execute(
        query.order_by(CodingSession.started_at.desc(), CodingSession.id).limit(limit)
    )
    return list(reversed(result.scalars().all()))


async def envelope_counts(db: AsyncSession, session_ids: list[str]) -> dict[str, tuple[int, int]]:
    """(attempts, failures) per run; absent ids reported no envelope."""
    if not session_ids:
        return {}
    result = await db.execute(
        select(
            CodingEnvelope.session_id,
            func.count(CodingEnvelope.id),
            _counted(CodingEnvelope.parsed.is_(False)),
        )
        .where(CodingEnvelope.session_id.in_(session_ids))
        .group_by(CodingEnvelope.session_id)
    )
    return {row[0]: (int(row[1]), int(row[2])) for row in result.all()}


async def gate_check_counts(db: AsyncSession, session_ids: list[str]) -> dict[str, tuple[int, int]]:
    """(checks, failures) per run, from the v1.19 evidence rows."""
    if not session_ids:
        return {}
    result = await db.execute(
        select(
            CodingGateCheck.session_id,
            func.count(CodingGateCheck.id),
            _counted(CodingGateCheck.ok.is_(False)),
        )
        .where(CodingGateCheck.session_id.in_(session_ids))
        .group_by(CodingGateCheck.session_id)
    )
    return {row[0]: (int(row[1]), int(row[2])) for row in result.all()}


# --------------------------------------------------------------- by model ---


@dataclass(frozen=True, slots=True)
class ModelLaneRow:
    """One model's lanes, and the runs they belonged to."""

    model: str | None
    lanes: int
    runs: int
    accepted_runs: int
    cost_usd: float
    tokens_in: int
    tokens_out: int
    turns: int


@dataclass(frozen=True, slots=True)
class ModelStageRow:
    """The stages that ran on one model — the lane's name is the stage's lane."""

    model: str | None
    stages: int
    corrections: int
    failed_stages: int
    timed_stages: int
    duration_ms: int
    gates_passed: int
    gates_failed: int


async def model_lane_rows(
    db: AsyncSession,
    *,
    scope: Scope,
) -> list[ModelLaneRow]:
    """Per model, over the runs that started inside the window.

    A lane carries no timestamp of its own, so the run's start is the only clock
    there is; `accepted_runs` counts the stored `success`, which is exactly the
    derived status — silence only ever turns `running` into `abandoned`.
    """
    query = (
        select(
            CodingAgent.model,
            func.count(CodingAgent.id),
            func.count(distinct(CodingAgent.session_id)),
            _counted_runs(CodingSession.status == STATUS_SUCCESS),
            func.sum(CodingAgent.cost_usd),
            func.sum(CodingAgent.tokens_in),
            func.sum(CodingAgent.tokens_out),
            func.sum(CodingAgent.turns),
        )
        .join(CodingSession, CodingSession.id == CodingAgent.session_id)
        .group_by(CodingAgent.model)
    )
    query = _scoped(query, session_conditions(scope))
    if scope.since is not None:
        query = query.where(CodingSession.started_at >= scope.since)
    result = await db.execute(query)
    return [
        ModelLaneRow(
            model=row[0],
            lanes=int(row[1]),
            runs=int(row[2]),
            accepted_runs=int(row[3]),
            cost_usd=float(row[4] or 0.0),
            tokens_in=int(row[5] or 0),
            tokens_out=int(row[6] or 0),
            turns=int(row[7] or 0),
        )
        for row in result.all()
    ]


async def model_stage_rows(
    db: AsyncSession,
    *,
    scope: Scope,
) -> list[ModelStageRow]:
    """The same population's stages, attributed through the lane that ran them.

    `coding_phases.agent` names a lane of the same run, and that lane carries
    the model — so a stage's model is a join, never a guess.
    """
    query = (
        select(
            CodingAgent.model,
            func.count(CodingPhase.id),
            func.sum(CodingPhase.corrections),
            _counted(CodingPhase.status == PHASE_FAILED),
            func.count(CodingPhase.duration_ms),
            func.sum(CodingPhase.duration_ms),
            func.sum(CodingPhase.gates_passed),
            func.sum(CodingPhase.gates_failed),
        )
        .select_from(CodingPhase)
        .join(
            CodingAgent,
            (CodingAgent.session_id == CodingPhase.session_id)
            & (CodingAgent.name == CodingPhase.agent),
        )
        .join(CodingSession, CodingSession.id == CodingPhase.session_id)
        .group_by(CodingAgent.model)
    )
    query = _scoped(query, session_conditions(scope))
    if scope.since is not None:
        query = query.where(CodingSession.started_at >= scope.since)
    result = await db.execute(query)
    return [
        ModelStageRow(
            model=row[0],
            stages=int(row[1]),
            corrections=int(row[2] or 0),
            failed_stages=int(row[3]),
            timed_stages=int(row[4]),
            duration_ms=int(row[5] or 0),
            gates_passed=int(row[6] or 0),
            gates_failed=int(row[7] or 0),
        )
        for row in result.all()
    ]


# ------------------------------------------- what a run's children reached for ---


async def child_assets_by_parent(
    db: AsyncSession, parent_ids: list[str]
) -> dict[str, list[tuple[str, str, int]]]:
    """(kind, name, uses) per parent, summed over every run it launched.

    The lane is dropped on purpose: a stage child's lane is its own `main`,
    which is not one of the parent's lanes and would read as a lie on the
    parent's card.
    """
    if not parent_ids:
        return {}
    uses = func.sum(CodingAsset.uses)
    result = await db.execute(
        select(CodingSession.parent_session_id, CodingAsset.kind, CodingAsset.name, uses)
        .join(CodingAsset, CodingAsset.session_id == CodingSession.id)
        .where(CodingSession.parent_session_id.in_(parent_ids))
        .group_by(CodingSession.parent_session_id, CodingAsset.kind, CodingAsset.name)
        .order_by(uses.desc(), CodingAsset.name)
    )
    grouped: dict[str, list[tuple[str, str, int]]] = {pid: [] for pid in parent_ids}
    for row in result.all():
        grouped[row[0]].append((row[1], row[2], int(row[3] or 0)))
    return grouped
