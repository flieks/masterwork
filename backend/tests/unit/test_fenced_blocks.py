"""The outer-block extractor must survive nested fences — the trigger guide's
example prompts are fenced code blocks inside the ```trigger block."""

from __future__ import annotations

from app.services.fenced_blocks import extract_outer_block
from app.services.summary_parser import extract_summary
from app.services.trigger_parser import extract_trigger

NESTED = """Here is the guide.

```trigger
# Guide

## Prompts

```
I just created an empty repo called foo — take it from zero to live.
```

More text after the nested block.
```
"""


def test_nested_fences_are_not_truncated() -> None:
    body = extract_trigger(NESTED)
    assert body is not None
    assert body.startswith("# Guide")
    assert "zero to live" in body
    assert body.endswith("More text after the nested block.")


def test_four_backtick_outer_fence() -> None:
    text = "````trigger\nbody with ```inner``` fence\n````"
    assert extract_outer_block(text, "trigger") == "body with ```inner``` fence"


def test_unterminated_block_takes_rest() -> None:
    assert extract_trigger("```trigger\nno closing fence") == "no closing fence"


def test_absent_block_is_none() -> None:
    assert extract_trigger("plain reply") is None
    assert extract_summary("```other\nx\n```") is None


def test_summary_block_still_extracts() -> None:
    assert extract_summary("intro\n\n```summary\n## Overview\ntext\n```\n") == "## Overview\ntext"
