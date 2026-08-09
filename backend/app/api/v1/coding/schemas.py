"""Claude Code observability API schemas — names match the frozen contract v1.14."""

from __future__ import annotations

from datetime import datetime
from functools import cache
from typing import Any

from pydantic import BaseModel, Field, TypeAdapter, ValidationError, model_validator


class _NamedBlock(BaseModel):
    """A sub-object a hook is allowed to send as a bare name: `"phase": "plan"`
    means the same as `"phase": {"name": "plan"}`."""

    @model_validator(mode="before")
    @classmethod
    def _accept_bare_name(cls, data: Any) -> Any:
        return {"name": data} if isinstance(data, str) else data


class PhaseIn(_NamedBlock):
    """One stage of the run as a hook reports it. Every field is optional: a
    later event fills in what the first one could not know yet, and an absent
    field never clears what is already stored."""

    name: str | None = Field(None, description="Stage name — plan, build, review, …")
    seq: int | None = Field(None, description="Position in the run; appended when omitted.")
    kind: str | None = Field(None, description="engineer | agent | code | git")
    agent: str | None = Field(None, description="Lane owner; matches an agent name.")
    description: str | None = None
    status: str | None = Field(None, description="running | passed | failed | skipped | abandoned")
    duration_ms: int | None = None
    cost_usd: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    corrections: int | None = Field(None, description="Retries this stage cost.")
    commit_sha: str | None = None


class AgentIn(_NamedBlock):
    """One lane of the run as a hook reports it; same partial-update rules."""

    name: str | None = None
    model: str | None = None
    color: str | None = None
    context_tokens: int | None = None
    context_window: int | None = Field(None, description="With context_tokens, the context bar.")
    cost_usd: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None


class HookEventRequest(BaseModel):
    """One hook firing. Deliberately permissive: everything but `session_id` and
    `event_type` is optional, over-long values are truncated rather than
    rejected, and an optional field whose value will not validate is dropped
    rather than answered with a 422 — a hook never fails a Claude Code run,
    including when the backend has moved on and it has not."""

    session_id: str = Field(..., min_length=1, description="Claude Code session id.")
    event_type: str = Field(
        ..., min_length=1, description='Hook name, e.g. "PreToolUse"; any string is accepted.'
    )
    cwd: str | None = Field(None, description="Working directory; used on first sight only.")
    model: str | None = Field(None, description="Model id; the latest value wins.")
    tool_name: str | None = Field(None, description="Tool the event is about, when it is one.")
    payload: dict[str, Any] | None = Field(
        None, description="Free-form hook input; truncated past 32 KB serialized."
    )
    stats: dict[str, Any] | None = Field(
        None, description="Free-form counters shallow-merged into the session's stats."
    )
    ended: bool = Field(False, description="True on the last event of the session.")

    title: str | None = Field(None, description="The run's request; kept from the first one seen.")
    workflow: str | None = Field(None, description='"factory" for a pipeline run.')
    status: str | None = Field(None, description="running | success | failed | interrupted")
    phase: PhaseIn | None = Field(None, description="Stage to upsert and link this event to.")
    agent: AgentIn | None = Field(None, description="Lane to upsert.")
    ok: bool | None = Field(None, description="Did the thing this event reports succeed?")
    duration_ms: int | None = Field(None, description="How long it took, when the hook knows.")

    @model_validator(mode="before")
    @classmethod
    def _drop_unusable_optionals(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        bad = {k for k, v in data.items() if v is not None and not _validates(k, v)}
        return {k: v for k, v in data.items() if k not in bad} if bad else data


@cache
def _adapter(field: str) -> TypeAdapter[Any] | None:
    """A per-field validator, or None for a field that must keep 422'ing."""
    model_field = HookEventRequest.model_fields.get(field)
    if model_field is None or model_field.is_required():
        return None
    return TypeAdapter(model_field.annotation)


def _validates(field: str, value: Any) -> bool:
    adapter = _adapter(field)
    if adapter is None:
        return True  # unknown key (ignored anyway) or a required one (may 422)
    try:
        adapter.validate_python(value)
    except ValidationError:
        return False
    return True


class PhaseSummary(BaseModel):
    """Just enough of a stage to draw the mini-lane chart on a session card."""

    seq: int = Field(..., description="Position in the run.")
    name: str
    agent: str | None = Field(..., description="Lane this stage belongs to.")
    status: str = Field(..., description="running | passed | failed | skipped | abandoned")
    started_at: datetime
    duration_ms: int | None = Field(..., description="Reported, or started_at → ended_at.")


class CodingPhase(PhaseSummary):
    """The full stage row, for the detail waterfall."""

    id: int = Field(..., description="What an event's phase_id points at.")
    kind: str | None = Field(..., description="engineer | agent | code | git")
    description: str | None
    ended_at: datetime | None = Field(..., description="Null while the stage is running.")
    cost_usd: float | None
    tokens_in: int | None
    tokens_out: int | None
    corrections: int
    commit_sha: str | None
    gates_passed: int
    gates_failed: int


class AgentLane(BaseModel):
    """One horizontal lane of the run's timeline."""

    name: str = Field(..., description='"main", a subagent type, or a pipeline stage.')
    model: str | None
    color: str | None
    context_tokens: int | None
    context_window: int | None = Field(..., description="Null when nobody reported one.")
    cost_usd: float | None
    tokens_in: int | None
    tokens_out: int | None
    turns: int


class AssetUse(BaseModel):
    """One skill or subagent this run reached for, and how often."""

    kind: str = Field(..., description='"skill" or "agent".')
    name: str
    asset_id: str = Field(
        ..., description='"claude:skill:<name>" / "claude:agent:<name>" — links to the asset page.'
    )
    lane: str | None = Field(..., description="The lane that used it; null when it had none.")
    uses: int


class CodingAssetUsage(BaseModel):
    """One asset's usage across every run — the flywheel view."""

    kind: str = Field(..., description='"skill" or "agent".')
    name: str
    asset_id: str
    sessions: int = Field(..., description="Distinct runs that used it.")
    uses: int = Field(..., description="Total uses across those runs.")
    last_used_at: datetime


class AssetCall(BaseModel):
    """One recorded call of an asset, and what the caller handed it."""

    used_at: datetime
    lane: str | None = Field(..., description="The lane that made the call; null when it had none.")
    source: str = Field(
        ...,
        description=(
            "Which signal named it: skill_call (an explicit Skill call, carries args) | "
            "spawn_call (a Task/Agent call, carries the brief) | skill_read (a SKILL.md "
            "read, carries only the path) | subagent_stop (a finished subagent, carries "
            "nothing)."
        ),
    )
    input: dict[str, str] | None = Field(
        ...,
        description=(
            "The call's arguments, truncated to 2 000 characters per key. Null when the "
            "signal carried none — a skill loaded by being read has no arguments."
        ),
    )


class AssetSessionUse(BaseModel):
    """One run that used an asset, and the calls it made."""

    session_id: str
    title: str | None = Field(
        ..., description="The run's title, derived the same way the Sessions screen derives it."
    )
    git_repo: str | None
    cwd: str
    status: str = Field(..., description="Derived run status, not the stored one.")
    started_at: datetime
    uses: int = Field(..., description="Calls this run made, across all of its lanes.")
    first_used_at: datetime
    last_used_at: datetime
    calls: list[AssetCall] = Field(
        ...,
        description=(
            "The individual calls, newest first. Capped across the whole response, so it "
            "can hold fewer entries than `uses` — and none at all for a run recorded "
            "before the log shipped, until it is backfilled."
        ),
    )


class CodingSession(BaseModel):
    id: str = Field(..., description="Claude Code session id.")
    cwd: str = Field(..., description='Working directory, "" if no event carried one.')
    git_repo: str | None = Field(..., description="Repo folder name derived from cwd.")
    model: str | None
    source: str = Field(..., description='Event producer; always "claude-code" today.')
    launch_mode: str | None = Field(
        ...,
        description=(
            '"automated" when a `claude -p` one-shot (script, hook, scheduler) started '
            'the run, "interactive" when a person did, null when unknown.'
        ),
    )
    title: str | None = Field(..., description="The run's request or first prompt.")
    title_source: str | None = Field(
        ...,
        description=(
            "Where `title` came from: prompt | factory | provenance | cwd. Null when the "
            "session has no title at all."
        ),
    )
    parent_session_id: str | None = Field(
        ..., description="The run that launched this one — a pipeline stage's parent."
    )
    child_count: int = Field(..., description="Runs this one launched.")
    workflow: str | None = Field(..., description='"factory", or null/"chat" for a plain session.')
    status: str = Field(
        ...,
        description=(
            "running | success | failed | interrupted | abandoned. `abandoned` is derived, "
            "never stored: an open run that has been silent for over 2 minutes. `running` "
            "therefore only ever means genuinely live."
        ),
    )
    started_at: datetime = Field(..., description="First event seen for this session.")
    last_event_at: datetime
    ended_at: datetime | None = Field(..., description="Set by an event with ended=true.")
    stats: dict[str, Any] | None = Field(..., description="Merged free-form counters.")
    cost_usd: float | None
    tokens_total: int | None
    tokens_in: int | None
    tokens_out: int | None
    cache_read_tokens: int | None
    event_count: int
    tool_call_count: int = Field(..., description="Events with event_type PostToolUse.")
    duration_seconds: float = Field(
        ..., description="started_at → ended_at, or → last_event_at while still open."
    )
    wall_ms: int = Field(
        ..., description="duration_seconds in milliseconds — the clock on the wall."
    )
    active_ms: int = Field(
        ...,
        description=(
            "Time the run was actually working: the sum of the gaps between consecutive "
            "events, discarding any gap over 60 s. A pipeline run prefers the measured sum "
            "of its stage durations. Lead with this, not wall_ms."
        ),
    )
    phases: list[PhaseSummary] = Field(..., description="Ordered by seq.")
    agents: list[AgentLane] = Field(..., description="Lanes, in the order they first appeared.")
    assets: list[AssetUse] = Field(..., description="Skills and subagents used, most-used first.")


class CodingSessionDetail(CodingSession):
    """The same session with whole phase rows instead of card summaries."""

    phases: list[CodingPhase] = Field(..., description="Ordered by seq.")  # type: ignore[assignment]


class CodingEvent(BaseModel):
    id: int = Field(..., description="Monotonic cursor; pass the last one back as `after`.")
    session_id: str
    event_type: str
    tool_name: str | None
    payload: dict[str, Any] | None
    created_at: datetime
    phase_id: int | None = Field(..., description="The stage this event happened in.")
    agent: str | None = Field(..., description="The lane this event happened in.")
    ok: bool | None
    duration_ms: int | None
    ended_at: datetime | None = Field(
        ..., description="When the reported work finished; span start is ended_at - duration_ms."
    )


class BackfillResult(BaseModel):
    """What rebuilding one session's derived rows produced."""

    session_id: str
    events: int = Field(..., description="Events replayed through the live derivation.")
    phases: int = Field(..., description="Stages the replay rebuilt.")
    agents: int = Field(..., description="Lanes the replay rebuilt.")
    assets: int = Field(..., description="Skill and subagent uses the replay rebuilt.")


class BackfillTotals(BaseModel):
    """The same, summed over a whole-history rebuild."""

    sessions: int
    events: int
    phases: int
    agents: int
    assets: int
