"""Extract a fenced ```scenario block from a one-shot scenario-generation reply.

Same conventions as the mermaid parser: take the first such block and strip its
fences. A reply with no scenario block yields None — the caller then falls back
to the stripped reply.
"""

from __future__ import annotations

import re

_SCENARIO_RE = re.compile(
    r"^[ \t]*```scenario[^\n]*\n(?P<body>.*?)\n[ \t]*```[ \t]*$",
    re.DOTALL | re.MULTILINE,
)


def extract_scenario(text: str) -> str | None:
    """Return the first scenario block's text (fences stripped), or None."""
    match = _SCENARIO_RE.search(text)
    if match is None:
        return None
    body = match.group("body").strip()
    return body or None
