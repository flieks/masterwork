"""Read one event three ways, so every run renders as lanes over a time axis.

Three producers write to the ingest and only one of them can be told what to
send: a caller that fills in the v1.13 `phase`/`agent` blocks explicitly, the
factory runner, which names its stage and lane inside `payload` and predates
those blocks, and Claude Code's own hooks, which name nothing at all. All three
are read here, in that order of precedence.

Nothing in this module touches the database — which is what lets the backfill
replay stored events through exactly the code path a live hook takes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.api.v1.coding import assets, schemas
from app.db.models.coding import (
    KIND_AGENT,
    KIND_CODE,
    MAIN_AGENT,
    PHASE_FAILED,
    PHASE_PASSED,
    PHASE_RUNNING,
    SPAWN_TOOLS,
    STATUS_FAILED,
    STATUS_SUCCESS,
    TITLE_FACTORY,
    TITLE_PROMPT,
    TITLE_SUMMARY,
    UNKNOWN_AGENT,
    WORKFLOW_FACTORY,
)

# A first prompt is a whole message; a card only has room for its opening.
TITLE_CHARS = 300

# An agent-written title is a phrase, not a message — 15 words never reach this.
SUMMARY_CHARS = 120

# A span's label comes from the spawn call's own one-liner; a lane rail is narrow.
SPAN_LABEL_CHARS = 60

# One line under a stage's name, saying what started it. Long enough for a
# sentence of a prompt, short enough not to become the panel.
TURN_DETAIL_CHARS = 200

# The factory wraps its real stages in a synthetic "run" phase whose detail is
# the request that started everything. That is a title, not a lane.
RUN_PHASE = "run"

# Events that mean a lane just finished a turn, whoever sent them.
TURN_EVENTS = frozenset({"agent_turn", "Stop", "SubagentStop"})


@dataclass(slots=True)
class PhaseWrite:
    """What an event says about its stage.

    Two kinds of field: the named ones replace what is stored (and `None` means
    "no opinion", never "clear it"), while the `add_*` ones accumulate, because
    gates and turns are counted across events rather than reported as totals.
    """

    name: str | None = None
    seq: int | None = None
    kind: str | None = None
    agent: str | None = None
    description: str | None = None
    status: str | None = None
    duration_ms: int | None = None
    cost_usd: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    corrections: int | None = None
    commit_sha: str | None = None

    add_tokens_in: int = 0
    add_tokens_out: int = 0
    add_gates_passed: int = 0
    add_gates_failed: int = 0


@dataclass(slots=True)
class AgentWrite:
    """What an event says about a lane; same replace/accumulate split."""

    name: str
    model: str | None = None
    color: str | None = None
    context_tokens: int | None = None
    context_window: int | None = None
    cost_usd: float | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None

    add_turns: int = 0
    add_cost_usd: float = 0.0
    add_tokens_in: int = 0
    add_tokens_out: int = 0


@dataclass(slots=True)
class Derived:
    """Everything one event has to say, before any of it is written down."""

    phase: PhaseWrite | None = None
    agents: list[AgentWrite] = field(default_factory=list)
    title: str | None = None
    # Which TITLE_* signal produced `title`; the service ranks them.
    title_source: str | None = None
    workflow: str | None = None
    status: str | None = None
    stats: dict[str, Any] | None = None
    ok: bool | None = None
    duration_ms: int | None = None
    # The event's own lane, stored on the event row for the waterfall.
    lane: str | None = None
    # A chat session has no stages of its own, so prompt→Stop becomes one — and
    # spawn→SubagentStop the same, on the subagent's lane rather than on `main`.
    opens_turn: bool = False
    closes_turn: bool = False
    # What to call the span being opened; `None` numbers it as a turn.
    turn_label: str | None = None
    # Why the span opened — the prompt that started it, in one readable line.
    turn_detail: str | None = None


def _text(value: Any) -> str | None:
    """A non-empty string, or nothing. Hooks send "" where they mean null."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _int(value: Any) -> int | None:
    return int(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _float(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _positive(value: Any) -> int:
    parsed = _int(value)
    return parsed if parsed and parsed > 0 else 0


def _sub(payload: dict[str, Any] | None, key: str) -> dict[str, Any]:
    """A nested object, or an empty one — payloads get truncated to markers."""
    nested = (payload or {}).get(key)
    return nested if isinstance(nested, dict) else {}


def from_event(
    event_type: str,
    tool_name: str | None = None,
    payload: dict[str, Any] | None = None,
    *,
    body: schemas.HookEventRequest | None = None,
) -> Derived:
    """Everything the event implies. `body` carries the explicit v1.13 blocks,
    and is absent when the backfill replays a row that predates them."""
    derived = (
        _from_factory(event_type, payload)
        if _is_factory(event_type, payload)
        else _from_hook(event_type, tool_name, payload)
    )
    if body is not None:
        _overlay(derived, body)
    if derived.phase is not None and derived.phase.agent is None:
        # One `agent` naming both the lane and the stage's owner is how every
        # producer actually writes it; only make it explicit if it disagrees.
        derived.phase.agent = derived.lane
    _count_turn(derived, event_type)
    return derived


def _count_turn(derived: Derived, event_type: str) -> None:
    """A lane's turns are counted here rather than in each branch, so the count
    does not depend on which producer the event came from."""
    if event_type not in TURN_EVENTS or not derived.lane:
        return
    for write in derived.agents:
        if write.name == derived.lane:
            write.add_turns = max(write.add_turns, 1)
            return
    derived.agents.append(AgentWrite(name=derived.lane, add_turns=1))


def _overlay(derived: Derived, body: schemas.HookEventRequest) -> None:
    """What the caller stated outranks what we guessed."""
    if body.title:
        # The only producer that states a title is the pipeline runner, and it
        # states the run's actual request — the strongest signal there is.
        derived.title, derived.title_source = body.title, TITLE_FACTORY
    derived.workflow = body.workflow or derived.workflow
    derived.status = body.status or derived.status
    derived.ok = body.ok if body.ok is not None else derived.ok
    derived.duration_ms = body.duration_ms if body.duration_ms is not None else derived.duration_ms

    if body.phase is not None and (body.phase.name or body.phase.seq is not None):
        derived.phase = PhaseWrite(**body.phase.model_dump())
        # An explicit phase block is a statement about the run's shape, so it
        # replaces the synthesized chat turn rather than landing next to it.
        derived.opens_turn = derived.closes_turn = False
        derived.turn_label = None
        # A gate verdict is carried by the event type, not by the phase block —
        # count it here too, or explicit senders lose their gate tallies.
        if body.event_type == "gate_pass":
            derived.phase.add_gates_passed = 1
        elif body.event_type == "gate_fail":
            derived.phase.add_gates_failed = 1
    if body.agent is not None and body.agent.name:
        lane = AgentWrite(**body.agent.model_dump())
        derived.agents = [a for a in derived.agents if a.name != lane.name] + [lane]
        derived.lane = lane.name


# ---------------------------------------------------------------- factory ---


def _is_factory(event_type: str, payload: dict[str, Any] | None) -> bool:
    """The factory echoes its event name and always names a phase; no Claude
    Code hook payload looks like that."""
    return payload is not None and payload.get("event") == event_type and "phase" in payload


def _from_factory(event_type: str, payload: dict[str, Any] | None) -> Derived:
    data = payload or {}
    result = _text(data.get("result"))
    lane = _text(data.get("agent"))
    derived = Derived(
        workflow=WORKFLOW_FACTORY,
        ok=None if result is None else result == "ok",
        duration_ms=_int(data.get("duration_ms")) or None,
        lane=lane,
    )

    if event_type == "run_end":
        derived.status = STATUS_SUCCESS if derived.ok else STATUS_FAILED
        derived.stats = _sub(data, "stats") or None
        return derived

    phase_name = _text(data.get("phase"))
    if phase_name is None or phase_name == RUN_PHASE:
        # The run envelope: its detail is the request, not a stage description.
        derived.title = _text(data.get("detail"))
        derived.title_source = TITLE_FACTORY if derived.title else None
        return derived

    derived.phase = _factory_phase(event_type, phase_name, lane, data, derived.ok)
    if lane:
        derived.agents = _factory_lane(event_type, lane, data)
    return derived


def _factory_phase(
    event_type: str,
    name: str,
    lane: str | None,
    data: dict[str, Any],
    ok: bool | None,
) -> PhaseWrite:
    phase = PhaseWrite(name=name, agent=lane)
    if event_type == "phase_start":
        phase.status = PHASE_RUNNING
        # Only phase_start decides this, so a later gate event cannot flip a
        # code stage into an agent one just by naming its lane.
        phase.kind = KIND_AGENT if lane else KIND_CODE
        phase.description = _text(data.get("detail"))
    elif event_type == "phase_end":
        phase.status = PHASE_PASSED if ok else PHASE_FAILED
        phase.description = _text(data.get("detail"))
        phase.duration_ms = _int(data.get("duration_ms"))
        phase.cost_usd = _float(data.get("cost_usd"))
        phase.corrections = _int(_sub(data, "payload").get("corrections"))
        phase.commit_sha = _text(_sub(data, "payload").get("commit"))
    elif event_type == "agent_turn":
        # Cost lands on the stage once, at phase_end; only tokens accumulate,
        # because no phase_end reports them.
        phase.add_tokens_in = _positive(data.get("tokens_in"))
        phase.add_tokens_out = _positive(data.get("tokens_out"))
    elif event_type == "gate_pass":
        phase.add_gates_passed = 1
    elif event_type == "gate_fail":
        phase.add_gates_failed = 1
    elif event_type == "commit":
        phase.commit_sha = _text(_sub(data, "payload").get("sha"))
    return phase


def _factory_lane(event_type: str, lane: str, data: dict[str, Any]) -> list[AgentWrite]:
    if event_type != "agent_turn":
        return [AgentWrite(name=lane)]
    return [
        AgentWrite(
            name=lane,
            # The turn's input tokens are what its context held when it ran.
            context_tokens=_int(data.get("tokens_in")),
            add_cost_usd=_float(data.get("cost_usd")) or 0.0,
            add_tokens_in=_positive(data.get("tokens_in")),
            add_tokens_out=_positive(data.get("tokens_out")),
        )
    ]


# ------------------------------------------------------------ claude code ---


def _from_hook(event_type: str, tool_name: str | None, payload: dict[str, Any] | None) -> Derived:
    """A plain session names nothing, so its shape is inferred: `main` plus one
    lane per subagent seen, one stage per prompt→Stop round trip on `main`, and
    one per spawn→SubagentStop round trip on the subagent's own lane."""
    derived = Derived()

    if event_type == "SessionEnd":
        derived.status = STATUS_SUCCESS
        return derived

    if event_type == "SubagentStop":
        # `agent_type` is only sometimes sent — 8 of 14 stops in the run that
        # exposed this carried the transcript path and nothing else, so the
        # sidecar is the fallback and `subagent` the honest floor. Dropping the
        # lane instead is what left those turns attributed to nobody.
        derived.lane = assets.subagent_name(payload)
        derived.agents = [AgentWrite(name=derived.lane)]
        derived.closes_turn = True
        return derived

    if event_type == "PreToolUse":
        # Installed with a Task|Agent matcher, so a plain tool call never gets
        # here; anything that does still has its PostToolUse to be counted by.
        return _from_spawn(tool_name, payload)

    if event_type not in ("UserPromptSubmit", "Stop") and not tool_name:
        # SessionStart, Notification, a hook nobody has written yet: nothing has
        # happened in a lane, and a lane is not worth inventing for it.
        return derived

    derived.lane = MAIN_AGENT
    derived.agents = [AgentWrite(name=MAIN_AGENT)]

    if event_type == "UserPromptSubmit":
        derived.opens_turn = True
        prompt = _text((payload or {}).get("prompt"))
        derived.title = prompt[:TITLE_CHARS] if prompt else None
        derived.title_source = TITLE_PROMPT if derived.title else None
        derived.turn_detail = turn_detail(prompt)
    elif event_type == "Stop":
        derived.closes_turn = True
    elif title := marker_title(payload):
        # A shell command carrying the marker — the agent naming its own run.
        derived.title, derived.title_source = title, TITLE_SUMMARY
    elif tool_name in SPAWN_TOOLS:
        # The call that finished naming a subagent type, for sessions recorded
        # before the PreToolUse hook existed. It says nothing about *when* the
        # subagent ran — agents are spawned in the background — so it declares
        # the lane and leaves the span to the spawn/stop pair.
        subagent = _spawn_key(_sub(payload, "tool_input"), "subagent_type")
        if subagent:
            derived.agents.append(AgentWrite(name=subagent))

    return derived


# Not every prompt is a person typing. A background task finishing re-enters the
# session as a `UserPromptSubmit` carrying an XML envelope, and so does a system
# reminder — nine of `d70244ff`'s fourteen turns started that way. Rendered raw
# they read as a wall of markup, and the stage they opened looks unexplained.
NOTIFICATION_PROMPT = "<task-notification>"
REMINDER_PROMPT = "<system-reminder>"


def _tag(text: str, name: str) -> str | None:
    """One named tag's body. Looked up by name rather than scanned for, because
    the tags that matter are nested inside the envelope's own tag."""
    match = re.search(rf"<{name}>(.*?)</{name}>", text, re.DOTALL)
    return _text(match.group(1)) if match else None


def turn_detail(prompt: str | None) -> str | None:
    """One line saying what started a turn, whoever or whatever sent it."""
    if not prompt:
        return None
    text = prompt.strip()

    if text.startswith(NOTIFICATION_PROMPT):
        # The envelope writes its own one-liner; the status is the honest floor
        # when a future version stops doing so.
        summary = _tag(text, "summary") or (f"background task {_tag(text, 'status') or 'finished'}")
        return f"resumed — {summary}"[:TURN_DETAIL_CHARS]

    if text.startswith(REMINDER_PROMPT):
        return "resumed — system reminder"

    # A person: their opening line, which is what they would call this turn.
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return first[:TURN_DETAIL_CHARS] or None


# An agent names its own run by echoing a marker, the way the factory-or-chat
# router already announces its verdict: `echo` exists everywhere, and a shell
# command is the one thing every hook payload carries verbatim. Anchored on the
# `=` so the sentence that *describes* the marker — in a skill file, in a grep —
# never becomes a title; the value stops at the quote that closes the echo.
TITLE_MARKER = re.compile(r"masterwork:title=\s*([^\"'\n]+)")


def marker_title(payload: dict[str, Any] | None) -> str | None:
    """The title a tool call announced, if it announced one."""
    command = _text(_sub(payload, "tool_input").get("command"))
    if not command:
        return None
    match = TITLE_MARKER.search(command)
    if not match:
        return None
    title = _text(match.group(1))
    return title[:SUMMARY_CHARS] if title else None


def _spawn_key(tool_input: dict[str, Any], key: str) -> str | None:
    """A spawn key, recovered from a `_truncated` JSON prefix if need be.

    Older forwarders collapsed a huge spawn call to a truncated string,
    `subagent_type` included — the `SubagentStop` then landed on a lane nobody
    had opened. The keys sit near the front of the JSON, so a complete
    `"key": "value"` pair usually survives the cut; an incomplete one (no
    closing quote) is left unrecovered rather than guessed at. Escaped quotes
    inside a prompt can't false-match: `\\"` breaks the pattern's bare `"`.
    """
    if found := _text(tool_input.get(key)):
        return found
    text = tool_input.get("_truncated")
    if not isinstance(text, str):
        return None
    match = re.search(rf'"{key}"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    if match is None:
        return None
    try:
        return _text(json.loads(f'"{match.group(1)}"'))
    except ValueError:
        return _text(match.group(1))


def _from_spawn(tool_name: str | None, payload: dict[str, Any] | None) -> Derived:
    """A subagent's span opens where it was spawned.

    This is the only event that knows a subagent's start: `PostToolUse` fires
    when the *call* returns, which for a background agent is long before the
    agent is done, and `SubagentStop` only ever reports the end.
    """
    if tool_name not in SPAWN_TOOLS:
        return Derived()
    tool_input = _sub(payload, "tool_input")
    lane = _spawn_key(tool_input, "subagent_type") or UNKNOWN_AGENT
    description = _spawn_key(tool_input, "description")
    return Derived(
        lane=lane,
        agents=[AgentWrite(name=lane)],
        opens_turn=True,
        turn_label=(description or lane)[:SPAN_LABEL_CHARS],
    )
