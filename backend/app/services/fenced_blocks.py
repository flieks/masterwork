"""Fence-tolerant extraction of one outer ```<info> block from a reply.

For replies that are instructed to be a single fenced block whose body may
itself contain fenced code blocks: take everything from the first
```<info> line to the LAST closing fence in the text. Accepts 3+ backticks on
either fence; an unterminated block takes the rest of the reply.
"""

from __future__ import annotations

import re


def extract_outer_block(text: str, info: str) -> str | None:
    """Return the outer block's body (fences stripped), or None when absent."""
    lines = text.splitlines()
    start = next(
        (i for i, line in enumerate(lines) if re.match(rf"^[ \t]*`{{3,}}{info}\b", line)),
        None,
    )
    if start is None:
        return None
    closing = re.compile(r"^[ \t]*`{3,}[ \t]*$")
    end = next((j for j in range(len(lines) - 1, start, -1) if closing.match(lines[j])), None)
    body = "\n".join(lines[start + 1 : end] if end is not None else lines[start + 1 :])
    return body.strip() or None
