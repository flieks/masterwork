"""Claude Code session observability: a run header, its lanes, its stages, its events.

Written by hooks, not by the app — so the tables are append-heavy and tolerant:
`event_type` is a free string (a new hook type must never 400), and the session
row is upserted on first sight of any of its events. `coding_events.id` is the
poll cursor the Sessions screen advances, which is why it is a plain
autoincrementing integer rather than a uuid.

`coding_phases`, `coding_agents` and `coding_assets` are derived, not reported:
nothing outside this backend writes them, and `service.backfill_session` can
rebuild all three from the event stream alone.

`coding_envelopes` and `coding_gate_checks` are the exception — they are
*reported*, on the hook body, which is not what gets stored. A replay can
reconstruct part of a gate check from the event's payload and none of an
envelope body, so those two are preserved across a backfill rather than rebuilt.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import JSONColumn, UTCDateTime

DEFAULT_SOURCE = "claude-code"

# How the run was started, derived from the launcher chain the hook reports.
# Null when no chain was recorded (every session from before that hook shipped).
LAUNCH_INTERACTIVE = "interactive"
LAUNCH_AUTOMATED = "automated"

# Outcome of a whole run. `abandoned` is never stored: SessionEnd rides an async
# hook the dying process outruns, so most runs never close themselves and the
# stored `running` is a lie. It is derived at read time from silence instead.
# `interrupted` is its mirror image — only ever stored, never derived. Silence
# cannot tell a killed run from a lost hook, and calling the first `interrupted`
# would be a claim masterwork has no evidence for, so silence stays `abandoned`
# and this value is set only when a producer states it on the hook body (which
# no producer does today; the factory reports an aborted run as `failed`).
STATUS_RUNNING = "running"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_INTERRUPTED = "interrupted"
STATUS_ABANDONED = "abandoned"

# Live means recent. A run with no `ended_at` that has been silent this long is
# reported as abandoned, and sorts below the ones that are genuinely working.
IDLE_WINDOW = timedelta(minutes=2)
# Longer than this between two consecutive events is a pause (a closed laptop, a
# person reading), not work — so it does not count towards `active_ms`.
ACTIVE_GAP = timedelta(seconds=60)

# Which producer wrote the run. Null means the same as "chat": nobody said.
WORKFLOW_FACTORY = "factory"
WORKFLOW_CHAT = "chat"

# Outcome of one stage. Anything but `running` is final and stamps ended_at.
# `abandoned` is the stage-level twin of the run-level one: the turn never
# reported a `Stop`, and the only thing known about its end is that it had
# happened by the time the lane started its next turn.
PHASE_RUNNING = "running"
PHASE_PASSED = "passed"
PHASE_FAILED = "failed"
PHASE_SKIPPED = "skipped"
PHASE_ABANDONED = "abandoned"
TERMINAL_PHASE_STATUSES = frozenset({PHASE_PASSED, PHASE_FAILED, PHASE_SKIPPED, PHASE_ABANDONED})

# What ran the stage: a person, a subagent, plain code, or git.
KIND_ENGINEER = "engineer"
KIND_AGENT = "agent"
KIND_CODE = "code"
KIND_GIT = "git"

# The lane a plain Claude Code session's own turns belong to.
MAIN_AGENT = "main"

# The subagent-spawn tool, under both names the harness has shipped it as. Its
# `PreToolUse` is the only event that knows when a subagent started.
SPAWN_TOOLS = frozenset({"Task", "Agent"})

# What a run reached for. The names match masterwork's own asset ids, so a card
# links straight to the skill or subagent page: `<provider>:<kind>:<name>`.
ASSET_SKILL = "skill"
ASSET_AGENT = "agent"
ASSET_PROVIDER = "claude"
# What a `SubagentStop` is called when nothing could name the agent that ran.
UNKNOWN_AGENT = "subagent"

# Where one piece of evidence came from. `reported` arrived on the hook body and
# is the only kind that can carry an envelope body — the body is not part of the
# stored event stream, so a replay cannot re-derive it. `recovered` was
# reconstructed by the replay from a `gate_pass`/`gate_fail` line, and says only
# what that line said.
EVIDENCE_REPORTED = "reported"
EVIDENCE_RECOVERED = "recovered"

# The gate name a verdict is filed under when the producer named none. The
# runner emits two such lines — an out-of-boundary revert, and a stage that
# returned a non-ok status — and both are stage-level, not gate-level.
UNNAMED_GATE = "stage"

# The gate that reads the reply's envelope. Its verdict is the one gate line
# that also says an envelope attempt happened, which is what lets a replay
# reconstruct the attempt (never its body) for a run recorded before v1.19.
ENVELOPE_GATE = "envelope"

# Which of the four signals named an asset. It decides what input can exist: a
# `Skill` call carries its args and a spawn carries its prompt, while a SKILL.md
# read carries only the path and a `SubagentStop` carries nothing at all.
USE_SKILL_CALL = "skill_call"
USE_SPAWN_CALL = "spawn_call"
USE_SKILL_READ = "skill_read"
USE_SUBAGENT_STOP = "subagent_stop"

# Where a session's title came from, weakest first — the order is the precedence
# the ingest applies, so a boilerplate stage prompt cannot overwrite the
# provenance name that puts the child under its parent.
TITLE_CWD = "cwd"
TITLE_PROMPT = "prompt"
TITLE_PROVENANCE = "provenance"
TITLE_FACTORY = "factory"


def asset_id_for(kind: str, name: str) -> str:
    return f"{ASSET_PROVIDER}:{kind}:{name}"


class CodingSession(Base):
    __tablename__ = "coding_sessions"

    # The Claude Code session_id, not a uuid we mint — hooks only know theirs.
    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    cwd: Mapped[str] = mapped_column(Text, default="", server_default="")
    # Repo folder name derived from cwd on first sight; null outside a repo.
    git_repo: Mapped[str | None] = mapped_column(String(200), nullable=True)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source: Mapped[str] = mapped_column(
        String(50), default=DEFAULT_SOURCE, server_default=text(f"'{DEFAULT_SOURCE}'")
    )
    # LAUNCH_INTERACTIVE / LAUNCH_AUTOMATED, or null when unknown.
    launch_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # What the run was asked to do: the first prompt, or the factory's request.
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    # TITLE_* — which signal won. Null on a row written before v1.14.
    title_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # The run that launched this one, read off the hook's `launched_by` chain.
    # Deliberately not a foreign key: a self-referential FK would make SQLite
    # rebuild this table under three children, and the link is soft — an
    # unresolvable parent just means the child is shown as a root.
    parent_session_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    # WORKFLOW_FACTORY, or null/WORKFLOW_CHAT for a plain Claude Code session.
    workflow: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default=STATUS_RUNNING, server_default=text(f"'{STATUS_RUNNING}'")
    )

    # Rolled up from the run's phases, or from a `stats` key of the same name.
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    tokens_total: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cache_read_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    started_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    # Drives list ordering and, with started_at, the derived duration.
    last_event_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), index=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    # Free-form roll-up (tokens, cost, turn counts), shallow-merged per event.
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)


class CodingPhase(Base):
    """One stage of a run: a factory pipeline stage, or a synthesized chat turn."""

    __tablename__ = "coding_phases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("coding_sessions.id", ondelete="CASCADE")
    )
    # Position in the run, and the only thing the upsert keys on.
    seq: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Lane owner — matches a CodingAgent.name of the same session.
    agent: Mapped[str | None] = mapped_column(String(100), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default=PHASE_RUNNING, server_default=text(f"'{PHASE_RUNNING}'")
    )

    started_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # Reported by the producer when it knows better than started_at → ended_at.
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Review→build retries that this stage cost.
    corrections: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    gates_passed: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    gates_failed: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))

    __table_args__ = (UniqueConstraint("session_id", "seq", name="uq_coding_phases_session_seq"),)


class CodingAgent(Base):
    """One lane of the run's timeline: `main`, a subagent type, a pipeline stage."""

    __tablename__ = "coding_agents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("coding_sessions.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(100))
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Chosen by the producer, if it cares; the UI falls back to its own palette.
    color: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # The pair behind the context bar; the window is null until someone reports it.
    context_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    context_window: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    tokens_in: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    turns: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))

    __table_args__ = (UniqueConstraint("session_id", "name", name="uq_coding_agents_session_name"),)


class CodingAsset(Base):
    """One skill or subagent a run reached for, and how often.

    Derived like phases and lanes are — nothing outside this backend writes it,
    and `service.backfill_session` rebuilds it from the event stream alone.
    """

    __tablename__ = "coding_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("coding_sessions.id", ondelete="CASCADE")
    )
    # ASSET_SKILL or ASSET_AGENT.
    kind: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(200))
    # The lane that used it; null when the event belonged to no lane.
    lane: Mapped[str | None] = mapped_column(String(100), nullable=True)
    uses: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))

    first_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "session_id", "kind", "name", "lane", name="uq_coding_assets_session_kind_name_lane"
        ),
        Index("ix_coding_assets_kind_name", "kind", "name"),
        Index("ix_coding_assets_last_seen_at", "last_seen_at"),
    )


class CodingAssetUse(Base):
    """One call of one asset, and what the caller handed it.

    `coding_assets` counts, this one remembers — it is what lets an asset page
    show the runs that reached for it and the arguments each call carried.
    Derived like the counters are, and dropped and rebuilt by the same backfill.
    """

    __tablename__ = "coding_asset_uses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("coding_sessions.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(200))
    lane: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # USE_* — which signal named it, and so what `input` can hold.
    source: Mapped[str] = mapped_column(String(30))
    # The call's arguments, already truncated. Null when the signal carried none.
    input: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)
    used_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    __table_args__ = (
        # The asset page reads by (kind, name) and orders by time.
        Index("ix_coding_asset_uses_kind_name", "kind", "name"),
        Index("ix_coding_asset_uses_session_id", "session_id"),
    )


class CodingEnvelope(Base):
    """One envelope an agent returned, and whether the runner could read it.

    Reported, not derived — unlike every other table in this module. The hook
    *body* carries it and only `payload` is stored, so a replay cannot rebuild a
    reported row and must not drop it: the backfill deletes `recovered` rows
    only, and re-points the survivors at the phase the replay rebuilt.
    """

    __tablename__ = "coding_envelopes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("coding_sessions.id", ondelete="CASCADE")
    )
    # The stage it was returned in; null when no stage could be resolved.
    phase_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("coding_phases.id", ondelete="SET NULL"), nullable=True
    )
    # The event that carried it. What a replay re-links a reported row by, and
    # the way back from a claim to the raw line that recorded it.
    event_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("coding_events.id", ondelete="CASCADE"), nullable=True
    )
    # The role/lane that produced it — plan, build, review, document.
    role: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 1-based try number within (session, phase, role); a correction is a retry.
    attempt: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    # No server_default: both dialects spell a boolean literal differently and
    # every writer supplies the value, so there is nothing to default from.
    parsed: Mapped[bool] = mapped_column(Boolean, default=False)
    # Why it did not parse. The whole point of storing the attempt at all.
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The status the envelope declared: ok | blocked | failed.
    status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # The envelope object as returned, size-capped like every other stored blob.
    body: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)
    # The reply the envelope was read out of — the only body a rejected attempt
    # has, since a reply that did not parse produced no object.
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    origin: Mapped[str] = mapped_column(
        String(20), default=EVIDENCE_REPORTED, server_default=text(f"'{EVIDENCE_REPORTED}'")
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    __table_args__ = (
        # How the detail view reads it: everything for one run, grouped by stage.
        Index("ix_coding_envelopes_session_phase", "session_id", "phase_id"),
        Index("ix_coding_envelopes_event_id", "event_id"),
    )


class CodingGateCheck(Base):
    """One deterministic check a gate ran, and the sentence it wrote.

    One row per CHECK, not per gate: `gates_passed`/`gates_failed` on the phase
    are counters, and a counter cannot say *changed_files: claimed but not
    changed on disk: README.md*. The note is the payload that matters.

    Reported like an envelope is, and preserved across a backfill the same way —
    except that a `gate_pass`/`gate_fail` event carries its gate and its note in
    `payload`, so unlike an envelope body most of this one *is* recoverable.
    """

    __tablename__ = "coding_gate_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("coding_sessions.id", ondelete="CASCADE")
    )
    phase_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("coding_phases.id", ondelete="SET NULL"), nullable=True
    )
    event_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("coding_events.id", ondelete="CASCADE"), nullable=True
    )
    # The gate that ran: envelope | artifacts | changed_files | boundary | …
    gate: Mapped[str] = mapped_column(String(100))
    attempt: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    # The thing checked, when the gate checked several — a path, a command.
    # Null when the gate is one verdict about the whole stage.
    item: Mapped[str | None] = mapped_column(String(500), nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    origin: Mapped[str] = mapped_column(
        String(20), default=EVIDENCE_REPORTED, server_default=text(f"'{EVIDENCE_REPORTED}'")
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_coding_gate_checks_session_phase", "session_id", "phase_id"),
        Index("ix_coding_gate_checks_event_id", "event_id"),
    )


class CodingEvent(Base):
    __tablename__ = "coding_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(200), ForeignKey("coding_sessions.id", ondelete="CASCADE")
    )
    # Hook name as sent ("PreToolUse", "Stop", …). Deliberately not an enum.
    event_type: Mapped[str] = mapped_column(String(100))
    tool_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    # Which stage and lane the event belongs to; null until one is known.
    # SET NULL rather than CASCADE: rebuilding the derived rows must not delete
    # the raw stream they were derived from.
    phase_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("coding_phases.id", ondelete="SET NULL"), nullable=True
    )
    agent: Mapped[str | None] = mapped_column(String(100), nullable=True)
    ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    # The cursor query is exactly this pair: session_id = ? AND id > ?.
    __table_args__ = (Index("ix_coding_events_session_id_id", "session_id", "id"),)
