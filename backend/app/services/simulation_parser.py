"""Extract the fenced ```simulation JSON block from a simulation run's reply.

Same conventions as the proposal parser: last block wins, malformed JSON or an
invalid structure yields None. Suggestions reuse the proposal change validator
so an accepted suggestion can flow through the same apply machinery.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.services.proposal_parser import _VALID_ACTIONS, ParsedChange, _parse_change

# Simulations may additionally suggest (un)linking an existing asset —
# no file write, just adds/removes it in the project's toolkit on apply.
_SUGGESTION_ACTIONS = frozenset(_VALID_ACTIONS) | {"link", "unlink"}

_SIMULATION_RE = re.compile(
    r"^[ \t]*```simulation[^\n]*\n(?P<body>.*?)\n[ \t]*```[ \t]*$",
    re.DOTALL | re.MULTILINE,
)

_VALID_IMPACTS = {"high", "medium", "low"}

# Score contribution of each checklist status; `na` items are excluded entirely
# (environmental / human-gated capabilities the toolkit can't control).
_STATUS_VALUE = {"pass": 1.0, "partial": 0.5, "fail": 0.0}
_VALID_STATUSES = frozenset(_STATUS_VALUE) | {"na"}


@dataclass(frozen=True)
class ParsedChecklistItem:
    id: str
    title: str
    weight: int  # 1-3, importance to the goal
    status: str  # pass | partial | fail | na
    evidence: str


@dataclass(frozen=True)
class ParsedSuggestion:
    title: str
    impact: str
    rationale: str
    changes: list[ParsedChange] = field(default_factory=list)


@dataclass(frozen=True)
class ParsedSimulation:
    score: int
    verdict: str
    summary: str
    analysis: str
    trace_mermaid: str | None
    suggestions: list[ParsedSuggestion]
    checklist: list[ParsedChecklistItem] = field(default_factory=list)


def score_from_checklist(items: list[ParsedChecklistItem]) -> int | None:
    """Weighted coverage over gradable items, 0-100. None if nothing to grade
    (no checklist, or every item is `na`) — the caller falls back to the model's
    holistic score. This is what makes the score comparable run-to-run: applying
    a fix flips an item to pass and the number provably rises."""
    gradable = [item for item in items if item.status != "na"]
    total_weight = sum(item.weight for item in gradable)
    if total_weight == 0:
        return None
    earned = sum(item.weight * _STATUS_VALUE[item.status] for item in gradable)
    return round(100 * earned / total_weight)


def extract_simulation(text: str) -> ParsedSimulation | None:
    matches = list(_SIMULATION_RE.finditer(text))
    if not matches:
        return None
    return _parse_body(matches[-1].group("body"))


def _parse_body(body: str) -> ParsedSimulation | None:
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    score = data.get("score")
    if isinstance(score, bool) or not isinstance(score, int):
        return None
    score = max(0, min(100, score))

    verdict = data.get("verdict")
    summary = data.get("summary")
    analysis = data.get("analysis")
    if not isinstance(summary, str) or not summary.strip():
        return None
    trace = data.get("trace_mermaid")
    if trace is not None and not isinstance(trace, str):
        return None

    raw_suggestions = data.get("suggestions", [])
    if not isinstance(raw_suggestions, list):
        return None
    suggestions: list[ParsedSuggestion] = []
    for raw in raw_suggestions:
        suggestion = _parse_suggestion(raw)
        if suggestion is None:
            return None
        suggestions.append(suggestion)

    # Checklist is best-effort: malformed items are skipped, not fatal, so a
    # judge that fumbles one item still yields a scored run.
    checklist: list[ParsedChecklistItem] = []
    raw_checklist = data.get("checklist")
    if isinstance(raw_checklist, list):
        for raw in raw_checklist:
            item = _parse_checklist_item(raw)
            if item is not None:
                checklist.append(item)

    # Prefer the deterministic coverage score; fall back to the model's holistic
    # number for old-shape replies with no checklist.
    computed = score_from_checklist(checklist)
    final_score = computed if computed is not None else score

    return ParsedSimulation(
        score=final_score,
        verdict=verdict if isinstance(verdict, str) else "",
        summary=summary,
        analysis=analysis if isinstance(analysis, str) else "",
        trace_mermaid=trace,
        suggestions=suggestions,
        checklist=checklist,
    )


def _parse_checklist_item(raw: object) -> ParsedChecklistItem | None:
    if not isinstance(raw, dict):
        return None
    item_id = raw.get("id")
    title = raw.get("title")
    if not isinstance(item_id, str) or not item_id.strip():
        return None
    if not isinstance(title, str) or not title.strip():
        return None

    weight = raw.get("weight")
    if isinstance(weight, bool) or not isinstance(weight, int):
        weight = 1
    weight = max(1, min(3, weight))

    status = raw.get("status")
    if status not in _VALID_STATUSES:
        status = "fail"  # conservative: an ungradable item counts against the score

    evidence = raw.get("evidence")
    return ParsedChecklistItem(
        id=item_id.strip(),
        title=title.strip(),
        weight=weight,
        status=status,
        evidence=evidence if isinstance(evidence, str) else "",
    )


def _parse_suggestion(raw: object) -> ParsedSuggestion | None:
    if not isinstance(raw, dict):
        return None
    title = raw.get("title")
    if not isinstance(title, str) or not title.strip():
        return None

    impact = raw.get("impact")
    if impact not in _VALID_IMPACTS:
        impact = "medium"

    rationale = raw.get("rationale")

    raw_changes = raw.get("changes", [])
    if not isinstance(raw_changes, list):
        return None
    changes: list[ParsedChange] = []
    for raw_change in raw_changes:
        change = _parse_change(raw_change, valid_actions=_SUGGESTION_ACTIONS)
        if change is None:
            return None
        changes.append(change)

    return ParsedSuggestion(
        title=title,
        impact=impact,
        rationale=rationale if isinstance(rationale, str) else "",
        changes=changes,
    )
