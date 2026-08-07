"""Extract a fenced ```summary block from a one-shot summary-generation reply.

Runs to the LAST closing fence so a body that happens to contain nested fenced
code blocks is never truncated (see fenced_blocks). A reply with no summary
block yields None — the caller then falls back to the stripped reply.
"""

from __future__ import annotations

from app.services.fenced_blocks import extract_outer_block


def extract_summary(text: str) -> str | None:
    """Return the summary block's text (fences stripped), or None."""
    return extract_outer_block(text, "summary")
