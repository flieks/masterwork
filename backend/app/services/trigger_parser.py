"""Extract a fenced ```trigger block from a one-shot trigger-guide reply.

Unlike the scenario parser, the body here legitimately CONTAINS nested fenced
code blocks (the example prompts), so the block must run to the LAST closing
fence in the reply — a non-greedy match would stop at the first nested fence
and truncate the guide. An unterminated block takes the rest of the reply.
"""

from __future__ import annotations

from app.services.fenced_blocks import extract_outer_block


def extract_trigger(text: str) -> str | None:
    """Return the trigger block's text (fences stripped), or None."""
    return extract_outer_block(text, "trigger")
