"""The cross-run read side: four aggregates over what every run already records.

A run's stages, gate checks, envelope attempts and lanes are all queryable one
run at a time, and none of it was ever summed. These four answer the questions
that actually improve an agent — which gate keeps saying no and to whom, which
role keeps being sent back, whether the trend is getting worse, and whether the
expensive model earned it.

Rates are computed here rather than in SQL so that a zero denominator produces
`None` and not a division: a role that ran no gate has an *unknown* failure
rate, and reporting 0.0 would be a claim that it never fails. Every rate ships
beside the count it was divided by, because a rate over two runs is noise and
only the reader can decide that.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.coding import schemas, serializers
from app.api.v1.coding.service import INSPECTION_CWD
from app.db.models.coding import STATUS_SUCCESS
from app.repositories import coding as coding_repo
from app.repositories import coding_analytics as analytics_repo
from app.repositories.coding_analytics import Scope

# How many distinct failure notes one gate reports. The notes are the actionable
# payload of the whole endpoint, but a gate that writes a hundred distinct
# sentences must not be able to make the response a hundred times bigger.
MAX_GATE_NOTES = 5


def _rate(numerator: float, denominator: int) -> float | None:
    """Null, never 0.0, when nothing was measured — see the module docstring."""
    return numerator / denominator if denominator else None


def _sort_key(name: str | None) -> tuple[int, str]:
    """Order names alphabetically and put the unnamed row last."""
    return (1, "") if name is None else (0, name)


def scope_for(
    *,
    since: datetime | None,
    workflow: str | None,
    include_inspection: bool,
    include_children: bool,
) -> Scope:
    """Turn the four query parameters every endpoint takes into one scope."""
    return Scope(
        since=since,
        workflow=workflow,
        exclude_cwd=None if include_inspection else INSPECTION_CWD,
        include_children=include_children,
    )


async def list_gate_stats(db: AsyncSession, *, scope: Scope) -> list[schemas.GateStat]:
    """Per gate: how often it ran, how often it said no, and on which role.

    A `changed_files` gate failing over and over on one role is a defect in that
    role's prompt rather than a flaky check — which is exactly how one was found.
    """
    totals = await analytics_repo.gate_total_rows(db, scope=scope)
    per_role = await analytics_repo.gate_rows(db, scope=scope)
    notes = await analytics_repo.gate_note_rows(db, scope=scope)

    roles_by_gate: dict[str, list[schemas.GateRoleStat]] = {}
    for row in per_role:
        roles_by_gate.setdefault(row.gate, []).append(
            schemas.GateRoleStat(
                role=row.role,
                checks=row.checks,
                failures=row.failures,
                failure_rate=_rate(row.failures, row.checks),
                runs=row.runs,
            )
        )
    for entries in roles_by_gate.values():
        entries.sort(key=lambda e: (-(e.failure_rate or 0.0), _sort_key(e.role)))

    notes_by_gate: dict[str, list[schemas.GateFailureNote]] = {}
    for note in notes:
        bucket = notes_by_gate.setdefault(note.gate, [])
        if len(bucket) < MAX_GATE_NOTES:
            bucket.append(
                schemas.GateFailureNote(
                    note=note.note,
                    role=note.role,
                    occurrences=note.occurrences,
                    last_seen_at=note.last_seen_at,
                )
            )

    return [
        schemas.GateStat(
            gate=total.gate,
            checks=total.checks,
            failures=total.failures,
            failure_rate=_rate(total.failures, total.checks),
            runs=total.runs,
            by_role=roles_by_gate.get(total.gate, []),
            top_failure_notes=notes_by_gate.get(total.gate, []),
        )
        for total in totals
    ]


def _empty_role_stat(row: analytics_repo.RoleEnvelopeRow) -> schemas.RoleStat:
    """A role that returned envelopes but owns no stage in the window.

    Worth a row rather than a silent drop: an envelope whose stage could not be
    resolved is a case the ingest allows on purpose, and a parse failure is a
    contract problem whether or not a stage was ever opened for it.
    """
    return schemas.RoleStat(
        role=row.role,
        runs=0,
        stages=0,
        corrections=0,
        avg_corrections=None,
        failed_stages=0,
        stage_failure_rate=None,
        timed_stages=0,
        total_duration_ms=0,
        avg_duration_ms=None,
        costed_stages=0,
        total_cost_usd=0.0,
        avg_cost_usd=None,
        tokens_in=0,
        tokens_out=0,
        gate_checks=0,
        gate_failures=0,
        gate_failure_rate=None,
        envelope_attempts=row.attempts,
        envelope_failures=row.failures,
        envelope_failure_rate=_rate(row.failures, row.attempts),
    )


async def list_role_stats(db: AsyncSession, *, scope: Scope) -> list[schemas.RoleStat]:
    """Per role: corrections, gate failures, duration, cost, and parse failures.

    The gate figures come from the stage counters, which every run carries; the
    per-gate breakdown in `list_gate_stats` reads the v1.19 evidence rows, which
    only a reported or replayed run carries. The two can therefore disagree, and
    the one covering more history is this one.
    """
    stages = await analytics_repo.role_stage_rows(db, scope=scope)
    envelopes = await analytics_repo.role_envelope_rows(db, scope=scope)
    by_role = {row.role: row for row in envelopes}

    stats: list[schemas.RoleStat] = []
    for row in stages:
        attempts = by_role.pop(row.role, None)
        made = attempts.attempts if attempts is not None else 0
        failed = attempts.failures if attempts is not None else 0
        gate_checks = row.gates_passed + row.gates_failed
        stats.append(
            schemas.RoleStat(
                role=row.role,
                runs=row.runs,
                stages=row.stages,
                corrections=row.corrections,
                avg_corrections=_rate(row.corrections, row.stages),
                failed_stages=row.failed_stages,
                stage_failure_rate=_rate(row.failed_stages, row.stages),
                timed_stages=row.timed_stages,
                total_duration_ms=row.duration_ms,
                avg_duration_ms=_rate(row.duration_ms, row.timed_stages),
                costed_stages=row.costed_stages,
                total_cost_usd=row.cost_usd,
                avg_cost_usd=_rate(row.cost_usd, row.costed_stages),
                tokens_in=row.tokens_in,
                tokens_out=row.tokens_out,
                gate_checks=gate_checks,
                gate_failures=row.gates_failed,
                gate_failure_rate=_rate(row.gates_failed, gate_checks),
                envelope_attempts=made,
                envelope_failures=failed,
                envelope_failure_rate=_rate(failed, made),
            )
        )
    stats.extend(_empty_role_stat(left) for left in by_role.values())
    stats.sort(key=lambda s: (-s.corrections, -s.gate_failures, _sort_key(s.role)))
    return stats


async def list_run_stats(
    db: AsyncSession, *, scope: Scope, limit: int = 100
) -> list[schemas.RunStat]:
    """The runs themselves, oldest first, so a client can plot the trend.

    The most recent `limit` runs are selected and then reversed: a trend wants
    the latest ones, and reading them left to right is what makes a regression
    visible without re-sorting.
    """
    sessions = await analytics_repo.trend_sessions(db, scope=scope, limit=limit)
    ids = [s.id for s in sessions]
    phases = await coding_repo.phases_by_session(db, ids)
    active = await coding_repo.active_ms_by_session(db, ids)
    children = await coding_repo.child_counts(db, ids)
    envelopes = await analytics_repo.envelope_counts(db, ids)
    checks = await analytics_repo.gate_check_counts(db, ids)
    now = datetime.now(tz=UTC)

    stats: list[schemas.RunStat] = []
    for session in sessions:
        rows = phases[session.id]
        status = serializers.derived_status(session, now=now)
        title, _ = serializers.derived_title(session)
        end = session.ended_at or session.last_event_at
        wall_ms = max(int((end - session.started_at).total_seconds() * 1000), 0)
        attempts, parse_failures = envelopes.get(session.id, (0, 0))
        gate_checks, gate_failures = checks.get(session.id, (0, 0))
        stats.append(
            schemas.RunStat(
                session_id=session.id,
                title=title,
                workflow=session.workflow,
                git_repo=session.git_repo,
                model=session.model,
                status=status,
                accepted=status == STATUS_SUCCESS,
                started_at=session.started_at,
                ended_at=session.ended_at,
                wall_ms=wall_ms,
                active_ms=serializers.active_ms_for(session, rows, active.get(session.id, 0)),
                cost_usd=session.cost_usd,
                tokens_total=session.tokens_total,
                tokens_in=session.tokens_in,
                tokens_out=session.tokens_out,
                stages=len(rows),
                corrections=sum(p.corrections for p in rows),
                gates_passed=sum(p.gates_passed for p in rows),
                gates_failed=sum(p.gates_failed for p in rows),
                gate_checks=gate_checks,
                gate_failures=gate_failures,
                envelope_attempts=attempts,
                envelope_failures=parse_failures,
                child_count=children.get(session.id, 0),
            )
        )
    return stats


async def list_model_stats(db: AsyncSession, *, scope: Scope) -> list[schemas.ModelStat]:
    """Per model: what it cost, what it needed corrected, and how often it landed.

    Attribution is a join, never a guess: a stage names its lane and the lane
    names its model, so a stage's model is the model that ran it.
    """
    lanes = await analytics_repo.model_lane_rows(db, scope=scope)
    stages = await analytics_repo.model_stage_rows(db, scope=scope)
    by_model = {row.model: row for row in stages}

    stats: list[schemas.ModelStat] = []
    for lane in lanes:
        stage = by_model.get(lane.model)
        gate_checks = (stage.gates_passed + stage.gates_failed) if stage is not None else 0
        stats.append(
            schemas.ModelStat(
                model=lane.model,
                lanes=lane.lanes,
                runs=lane.runs,
                accepted_runs=lane.accepted_runs,
                acceptance_rate=_rate(lane.accepted_runs, lane.runs),
                stages=stage.stages if stage is not None else 0,
                corrections=stage.corrections if stage is not None else 0,
                avg_corrections=(
                    _rate(stage.corrections, stage.stages) if stage is not None else None
                ),
                failed_stages=stage.failed_stages if stage is not None else 0,
                timed_stages=stage.timed_stages if stage is not None else 0,
                total_duration_ms=stage.duration_ms if stage is not None else 0,
                avg_duration_ms=(
                    _rate(stage.duration_ms, stage.timed_stages) if stage is not None else None
                ),
                cost_usd=lane.cost_usd,
                tokens_in=lane.tokens_in,
                tokens_out=lane.tokens_out,
                turns=lane.turns,
                gate_checks=gate_checks,
                gate_failures=stage.gates_failed if stage is not None else 0,
                gate_failure_rate=(
                    _rate(stage.gates_failed, gate_checks) if stage is not None else None
                ),
            )
        )
    # The unnamed row sorts last however many runs it covers: it is not a model,
    # and it appears in nearly every run because `git` and `checks` name none.
    stats.sort(key=lambda s: (s.model is None, -s.runs, _sort_key(s.model)))
    return stats
