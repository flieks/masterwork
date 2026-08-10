"""What an agent claimed, and what the gates said about it.

A stage's `gates_passed`/`gates_failed` are counters, and you cannot improve an
agent from a counter. The artefact worth keeping is the sentence a check wrote —
*changed_files: claimed but not changed on disk: README.md* — and the envelope
that made the claim. This module turns one event into those two things.

Two producers, so two readers:

* `from_body` reads the v1.19 `envelope` / `gate` blocks a runner states on the
  hook body. Everything it returns is `reported` — including an envelope body,
  which nothing else can supply.
* `from_event` mines a `gate_pass` / `gate_fail` event for what its payload
  still proves. Everything it returns is `recovered`: the gate, the note and the
  verdict survive in `detail`, the envelope's *body* never does.

Like `derive` and `assets`, nothing here touches the database — which is what
lets the backfill replay stored events through the code path a live hook takes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.api.v1.coding import schemas
from app.db.models.coding import ENVELOPE_GATE, UNNAMED_GATE

# Column widths and blob caps — clipped rather than 422'd, like every other
# hook-fed field. A gate note carries the tail of a failing command's output, so
# it gets far more room than a parse error, which is one sentence.
MAX_ROLE = 100
MAX_GATE = 100
MAX_ITEM = 500
MAX_STATUS = 20
MAX_NOTE_CHARS = 8_000
MAX_PARSE_ERROR_CHARS = 2_000
MAX_RAW_TEXT_CHARS = 32 * 1024
# A retry counter, not an identifier: a producer that sends a nonsense one gets
# it clamped instead of overflowing a 32-bit column on Postgres.
MAX_ATTEMPT = 1_000_000

# The two event types that carry a verdict, and what each one means.
GATE_VERDICTS = {"gate_pass": True, "gate_fail": False}


@dataclass(frozen=True, slots=True)
class EnvelopeWrite:
    """One envelope attempt, before it is written down."""

    role: str | None = None
    # None means "count it" — the writer numbers it within (session, phase, role).
    attempt: int | None = None
    parsed: bool = False
    parse_error: str | None = None
    status: str | None = None
    body: dict[str, Any] | None = None
    raw_text: str | None = None


@dataclass(frozen=True, slots=True)
class GateCheckWrite:
    """One check a gate ran, before it is written down."""

    gate: str
    ok: bool
    attempt: int | None = None
    item: str | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class Evidence:
    """Everything one event says about claims and verdicts."""

    envelope: EnvelopeWrite | None = None
    checks: tuple[GateCheckWrite, ...] = ()

    @property
    def empty(self) -> bool:
        return self.envelope is None and not self.checks


def _text(value: str | None, limit: int) -> str | None:
    """A non-empty clipped string, or nothing. Hooks send "" where they mean null."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped[:limit] if stripped else None


def _attempt(value: int | None) -> int | None:
    return min(max(value, 1), MAX_ATTEMPT) if value is not None else None


def _capped_text(value: str | None, limit: int) -> str | None:
    """Like `_text`, but says so when it had to cut — a truncated reply that
    looks complete is worse than no reply at all."""
    clipped = _text(value, limit)
    if clipped is None or value is None or len(value.strip()) <= limit:
        return clipped
    return f"{clipped}\n… [truncated, {len(value.strip())} chars]"


def _sub(payload: dict[str, Any] | None, key: str) -> dict[str, Any]:
    nested = (payload or {}).get(key)
    return nested if isinstance(nested, dict) else {}


# --------------------------------------------------------------- reported ---


def from_body(body: schemas.HookEventRequest, *, lane: str | None) -> Evidence:
    """The blocks the producer stated. Absent or unusable blocks record nothing."""
    return Evidence(
        envelope=_envelope_from_block(body.envelope, lane=lane),
        checks=tuple(_checks_from_block(body.gate, event_type=body.event_type)),
    )


def _envelope_from_block(
    block: schemas.EnvelopeIn | None, *, lane: str | None
) -> EnvelopeWrite | None:
    if block is None:
        return None
    parse_error = _text(block.parse_error, MAX_PARSE_ERROR_CHARS)
    body = block.body if isinstance(block.body, dict) else None
    raw_text = _capped_text(block.raw_text, MAX_RAW_TEXT_CHARS)
    # An attempt nobody can say anything about is not an attempt. `parsed` sent
    # explicitly counts as saying something: "the reply had no envelope at all".
    if body is None and raw_text is None and parse_error is None and block.parsed is None:
        return None
    return EnvelopeWrite(
        role=_text(block.role, MAX_ROLE) or _text(lane, MAX_ROLE),
        attempt=_attempt(block.attempt),
        # A producer that states only an error has stated the verdict too.
        parsed=block.parsed if block.parsed is not None else parse_error is None,
        parse_error=parse_error,
        status=_text(block.status, MAX_STATUS),
        body=body,
        raw_text=raw_text,
    )


def _checks_from_block(block: schemas.GateIn | None, *, event_type: str) -> list[GateCheckWrite]:
    """One row per check. `checks` absent means the block *is* the one check."""
    gate = _text(block.name, MAX_GATE) if block is not None else None
    if block is None or gate is None:
        return []
    # A producer that leaves the verdict off is answered by the event it rode in
    # on, which is how the runner already spells `gate_pass` / `gate_fail`.
    fallback_ok = block.ok if block.ok is not None else GATE_VERDICTS.get(event_type)
    entries = block.checks if block.checks else [None]
    rows = []
    for entry in entries:
        ok = fallback_ok if entry is None or entry.ok is None else entry.ok
        if ok is None:
            continue  # no verdict anywhere: nothing to record
        note = block.note if entry is None or entry.note is None else entry.note
        item = block.item if entry is None or entry.item is None else entry.item
        rows.append(
            GateCheckWrite(
                gate=gate,
                ok=ok,
                attempt=_attempt(block.attempt),
                item=_text(item, MAX_ITEM),
                note=_capped_text(note, MAX_NOTE_CHARS),
            )
        )
    return rows


# -------------------------------------------------------------- recovered ---


def from_event(event_type: str, payload: dict[str, Any] | None, *, lane: str | None) -> Evidence:
    """What a stored `gate_pass` / `gate_fail` line still proves.

    The runner writes `detail` as `"<gate>: <note>"` and puts the gate's name in
    `payload.gate`, so the verdict, the gate and the note all survive a replay.
    The envelope gate's line proves an *attempt* happened and whether it parsed;
    its body was never posted and stays null rather than being invented.
    """
    ok = GATE_VERDICTS.get(event_type)
    if ok is None:
        return Evidence()
    data = payload or {}
    gate = _text(_str(_sub(data, "payload").get("gate")), MAX_GATE) or UNNAMED_GATE
    note = _capped_text(_strip_gate_prefix(data.get("detail"), gate), MAX_NOTE_CHARS)
    check = GateCheckWrite(gate=gate, ok=ok, item=None, note=note)
    envelope = None
    if gate == ENVELOPE_GATE:
        envelope = EnvelopeWrite(
            role=_text(lane, MAX_ROLE) or _text(_str(data.get("agent")), MAX_ROLE),
            parsed=ok,
            parse_error=None if ok else note,
        )
    return Evidence(envelope=envelope, checks=(check,))


def _str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _strip_gate_prefix(detail: Any, gate: str) -> str | None:
    """Undo the `"<gate>: "` the runner prepends, so the note is just the note."""
    text = _str(detail)
    if text is None:
        return None
    prefix = f"{gate}: "
    return text[len(prefix) :] if text.startswith(prefix) else text
