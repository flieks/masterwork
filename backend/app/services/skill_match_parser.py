"""Extract the JSON array of {name, reason} matches from a describe-to-find reply.

Same conventions as links_parser: a bare JSON array is tried first (the prompt
asks for ONLY that), falling back to the last fenced ```json/```matches block
when the model wraps it anyway. Malformed JSON or an invalid structure yields
None.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

_FENCE_RE = re.compile(
    r"^[ \t]*```(?:json|matches)[^\n]*\n(?P<body>.*?)\n[ \t]*```[ \t]*$",
    re.DOTALL | re.MULTILINE,
)


@dataclass(frozen=True)
class ParsedMatch:
    name: str
    reason: str


def _parse_array(data: object) -> list[ParsedMatch] | None:
    if not isinstance(data, list):
        return None
    matches: list[ParsedMatch] = []
    for raw in data:
        if not isinstance(raw, dict):
            return None
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            return None
        reason = raw.get("reason")
        matches.append(
            ParsedMatch(name=name.strip(), reason=reason if isinstance(reason, str) else "")
        )
    return matches


def extract_matches(text: str) -> list[ParsedMatch] | None:
    try:
        data = json.loads(text.strip())
    except (json.JSONDecodeError, ValueError):
        data = None
    if data is not None:
        parsed = _parse_array(data)
        if parsed is not None:
            return parsed

    fence_matches = list(_FENCE_RE.finditer(text))
    if not fence_matches:
        return None
    try:
        data = json.loads(fence_matches[-1].group("body"))
    except (json.JSONDecodeError, ValueError):
        return None
    return _parse_array(data)
