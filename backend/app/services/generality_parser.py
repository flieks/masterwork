"""Extract a fenced ```generality block from a one-shot audit reply.

Like the trigger guide, the body may itself contain fenced code blocks (quoted
offending passages), so the block must run to the LAST closing fence — see
extract_outer_block.
"""

from __future__ import annotations

from app.services.fenced_blocks import extract_outer_block


def extract_generality(text: str) -> str | None:
    """Return the generality block's text (fences stripped), or None."""
    return extract_outer_block(text, "generality")
