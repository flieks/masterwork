"""Proposal-block extraction from assistant replies."""

from __future__ import annotations

from app.services.proposal_parser import extract_proposal

_BLOCK = """Sure, here's my suggestion.

```proposal
{"summary": "tighten the intro", "changes": [
  {"path": "/a/b/SKILL.md", "action": "update", "new_content": "hi", "description": "shorten"}
]}
```"""


def test_no_block_returns_text_unchanged() -> None:
    text, proposal = extract_proposal("Just a plain answer, no changes.")
    assert proposal is None
    assert text == "Just a plain answer, no changes."


def test_valid_block_is_parsed_and_stripped() -> None:
    text, proposal = extract_proposal(_BLOCK)
    assert proposal is not None
    assert proposal.summary == "tighten the intro"
    assert len(proposal.changes) == 1
    change = proposal.changes[0]
    assert change.path == "/a/b/SKILL.md"
    assert change.action == "update"
    assert change.new_content == "hi"
    assert "```proposal" not in text
    assert text == "Sure, here's my suggestion."


def test_last_block_wins() -> None:
    text = (
        "```proposal\n"
        '{"summary": "first", "changes": []}\n'
        "```\n\nthen\n\n"
        "```proposal\n"
        '{"summary": "second", "changes": []}\n'
        "```"
    )
    _, proposal = extract_proposal(text)
    assert proposal is not None
    assert proposal.summary == "second"


def test_malformed_json_keeps_text_as_is() -> None:
    text = "Look:\n\n```proposal\n{not valid json}\n```"
    out, proposal = extract_proposal(text)
    assert proposal is None
    assert out == text  # unchanged, block NOT stripped


def test_invalid_shape_is_rejected() -> None:
    # Missing "changes" list => not a proposal.
    text = '```proposal\n{"summary": "x"}\n```'
    _, proposal = extract_proposal(text)
    assert proposal is None


def test_delete_action_allows_null_content() -> None:
    text = (
        "```proposal\n"
        '{"summary": "remove", "changes": ['
        '{"path": "/x.md", "action": "delete", "new_content": null, "description": "gone"}'
        "]}\n```"
    )
    _, proposal = extract_proposal(text)
    assert proposal is not None
    assert proposal.changes[0].new_content is None


def test_link_action_is_simulation_only_and_rejected_here() -> None:
    text = (
        "```proposal\n"
        '{"summary": "link", "changes": ['
        '{"path": "/x.md", "action": "link", "new_content": null, "description": "link it"}'
        "]}\n```"
    )
    _, proposal = extract_proposal(text)
    assert proposal is None
