"""Extract a fenced ```mermaid block from a one-shot diagram reply.

Diagram generation asks the CLI to reply with ONLY a mermaid block; we take the
first such block and strip its fences. A reply with no mermaid block yields None.
"""

from __future__ import annotations

import re

_MERMAID_RE = re.compile(
    r"^[ \t]*```mermaid[^\n]*\n(?P<body>.*?)\n[ \t]*```[ \t]*$",
    re.DOTALL | re.MULTILINE,
)


def extract_mermaid(text: str) -> str | None:
    """Return the first mermaid block's source (fences stripped), or None."""
    match = _MERMAID_RE.search(text)
    if match is None:
        return None
    body = match.group("body").strip()
    return body or None
