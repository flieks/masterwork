"""The typed JSON envelope that is the ONLY thing that moves between stages."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

# Required envelope fields per role. `changed_files` is required of every stage
# that may write, so gate 3 has something to verify for all of them.
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "plan": ("status", "summary", "artifacts", "changed_files"),
    "build": ("status", "summary", "changed_files"),
    "review": ("status", "summary", "approved", "blocking"),
    "document": ("status", "summary", "changed_files"),
}

VALID_STATUS = ("ok", "blocked", "failed")


def owes_a_verdict(stage: str) -> bool:
    """Whether this role's envelope carries a judgement — the roles `approved` binds."""
    return "approved" in REQUIRED_FIELDS.get(stage, ())

# A fenced block: opening fence + info string, body, closing fence on its own line.
_FENCE_RE = re.compile(r"^[ \t]*```([^\n`]*)\r?\n(.*?)\r?\n?^[ \t]*```[ \t]*$", re.S | re.M)

ENVELOPE_REMINDER = (
    "End your reply with exactly one fenced ```json envelope block, nothing after it."
)


@dataclass(frozen=True)
class Envelope:
    status: str
    summary: str
    artifacts: list[str] = field(default_factory=list)
    notes_for_next_agent: str = ""
    changed_files: list[str] = field(default_factory=list)
    approved: bool | None = None
    blocking: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @property
    def summary_line(self) -> str:
        return (self.summary.strip().splitlines() or [""])[0].strip()

    def to_json(self) -> str:
        return json.dumps(self.raw, indent=2, ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True)
class ParseResult:
    envelope: Envelope | None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.envelope is not None


def parse_envelope(text: str, stage: str) -> ParseResult:
    """Parse the LAST fenced block, which must be json with nothing after it."""
    blocks = list(_FENCE_RE.finditer(text or ""))
    if not blocks:
        return ParseResult(None, "no fenced code block found in the reply")

    last = blocks[-1]
    info = last.group(1).strip().lower()
    if info != "json":
        return ParseResult(None, f"the last fenced block is `{info or 'unlabelled'}`, not `json`")

    trailing = text[last.end() :].strip()
    if trailing:
        return ParseResult(None, f"there is text after the envelope block: {trailing[:120]!r}")

    try:
        data = json.loads(last.group(2))
    except (json.JSONDecodeError, ValueError) as exc:
        return ParseResult(None, f"the envelope block is not valid JSON: {exc}")
    if not isinstance(data, dict):
        return ParseResult(None, "the envelope block must be a JSON object")

    required = REQUIRED_FIELDS.get(stage, ("status", "summary"))
    missing = [f for f in required if f not in data]
    if missing:
        return ParseResult(
            None, f"missing required field(s) for the {stage} role: {', '.join(missing)}"
        )

    try:
        envelope = _coerce(data)
    except ValueError as exc:
        return ParseResult(None, str(exc))
    if envelope.status not in VALID_STATUS:
        return ParseResult(
            None, f'"status" must be one of {", ".join(VALID_STATUS)} (got {envelope.status!r})'
        )
    return ParseResult(envelope)


def attempt_block(
    result: ParseResult, *, role: str, attempt: int, raw_text: str
) -> dict[str, Any]:
    """The v1.19 `envelope` block for one agent turn.

    Emitted for failures too — a reply whose envelope did not parse is the row
    worth keeping, and the raw text is the only place the reason is legible.
    """
    block: dict[str, Any] = {
        "role": role,
        "attempt": attempt,
        "parsed": result.ok,
        "raw_text": raw_text,
    }
    if result.error:
        block["parse_error"] = result.error
    if result.envelope is not None:
        block["status"] = result.envelope.status
        block["body"] = result.envelope.raw
    return block


def _str_list(data: dict, key: str) -> list[str]:
    value = data.get(key) or []
    if isinstance(value, str):  # a lone path is a common near-miss; accept it
        return [value]
    if not isinstance(value, list):
        raise ValueError(f'"{key}" must be a list of strings')
    out = []
    for item in value:
        # Reviewers like to emit structured findings; keep them, as text.
        out.append(item if isinstance(item, str) else json.dumps(item, ensure_ascii=False))
    return out


def _coerce(data: dict) -> Envelope:
    status = data.get("status")
    summary = data.get("summary")
    if not isinstance(status, str) or not isinstance(summary, str):
        raise ValueError('"status" and "summary" must be strings')
    approved = data.get("approved")
    if approved is not None and not isinstance(approved, bool):
        raise ValueError('"approved" must be true or false')
    notes = data.get("notes_for_next_agent") or ""
    return Envelope(
        status=status.strip().lower(),
        summary=summary,
        artifacts=_str_list(data, "artifacts"),
        notes_for_next_agent=notes if isinstance(notes, str) else json.dumps(notes),
        changed_files=_str_list(data, "changed_files"),
        approved=approved,
        blocking=_str_list(data, "blocking"),
        assumptions=_str_list(data, "assumptions"),
        raw=data,
    )
