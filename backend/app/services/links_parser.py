"""Extract the fenced ```links JSON block from a suggest-links reply.

Same conventions as the simulation parser: last block wins, malformed JSON or
an invalid structure yields None.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

_LINKS_RE = re.compile(
    r"^[ \t]*```links[^\n]*\n(?P<body>.*?)\n[ \t]*```[ \t]*$",
    re.DOTALL | re.MULTILINE,
)


# Where an omitted/unparseable confidence lands: inside the recommended band, so
# a model that skips the field still yields a usable (pre-checked) toolkit.
_DEFAULT_CONFIDENCE = 70


@dataclass(frozen=True)
class ParsedLink:
    asset_id: str
    reason: str
    confidence: int  # 0-100, how strongly the goal exercises this asset


def _parse_confidence(raw: object) -> int:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return _DEFAULT_CONFIDENCE
    return max(0, min(100, round(raw)))


def extract_links(text: str) -> list[ParsedLink] | None:
    matches = list(_LINKS_RE.finditer(text))
    if not matches:
        return None
    try:
        data = json.loads(matches[-1].group("body"))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("links"), list):
        return None

    links: list[ParsedLink] = []
    for raw in data["links"]:
        if not isinstance(raw, dict):
            return None
        asset_id = raw.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id.strip():
            return None
        reason = raw.get("reason")
        links.append(
            ParsedLink(
                asset_id=asset_id.strip(),
                reason=reason if isinstance(reason, str) else "",
                confidence=_parse_confidence(raw.get("confidence")),
            )
        )
    return links
