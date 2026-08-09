"""Claude Code session observability: a run header, its lanes, its stages, its events.

Written by hooks, not by the app — so the tables are append-heavy and tolerant:
`event_type` is a free string (a new hook type must never 400), and the session
row is upserted on first sight of any of its events. `coding_events.id` is the
poll cursor the Sessions screen advances, which is why it is a plain
autoincrementing integer rather than a uuid.

`coding_phases`, `coding_agents` and `coding_assets` are derived, not reported:
nothing outside this backend writes them, and `service.backfill_session` can
rebuild all three from the event stream alone.
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
