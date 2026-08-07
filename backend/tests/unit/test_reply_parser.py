"""Combined proposal + project block extraction (v1.1 chat semantics)."""

from __future__ import annotations

import json

from app.services.proposal_parser import extract_reply_blocks

_PROPOSAL = {
    "summary": "tidy the intro",
    "changes": [
        {
            "path": "/a/b/SKILL.md",
            "action": "update",
            "new_content": "hi",
            "description": "shorten",
        }
    ],
}
_PROJECT = {
    "name": "Deploy pipeline",
    "goal": None,
    "flow_mermaid": "flowchart TD\n  A-->B",
    "asset_ids": ["claude:skill:azure-deploy"],
    "description": "link the azure skill",
}


def _proposal_block() -> str:
    return f"```proposal\n{json.dumps(_PROPOSAL)}\n```"


def _project_block() -> str:
    return f"```project\n{json.dumps(_PROJECT)}\n```"


def test_both_blocks_parsed_and_stripped() -> None:
    text = f"Here is my plan.\n\n{_proposal_block()}\n\n{_project_block()}"
    visible, proposal, project = extract_reply_blocks(text, include_project=True)

    assert proposal is not None
    assert proposal.summary == "tidy the intro"
    assert proposal.changes[0].path == "/a/b/SKILL.md"

    assert project is not None
    assert project.name == "Deploy pipeline"
    assert project.goal is None
    assert project.asset_ids == ["claude:skill:azure-deploy"]
    assert project.description == "link the azure skill"

    assert "```proposal" not in visible
    assert "```project" not in visible
    assert visible == "Here is my plan."


def test_project_only_reply() -> None:
    text = f"Linking assets.\n\n{_project_block()}"
    visible, proposal, project = extract_reply_blocks(text, include_project=True)
    assert proposal is None
    assert project is not None
    assert visible == "Linking assets."


def test_malformed_project_block_ignored_but_stripped() -> None:
    text = "Look:\n\n```project\n{not valid json}\n```"
    visible, proposal, project = extract_reply_blocks(text, include_project=True)
    assert proposal is None
    assert project is None
    assert "```project" not in visible
    assert visible == "Look:"


def test_global_session_ignores_project_block() -> None:
    text = f"Answer.\n\n{_project_block()}"
    visible, proposal, project = extract_reply_blocks(text, include_project=False)
    assert proposal is None
    assert project is None  # ignored for a global (unscoped) session
    assert "```project" not in visible  # still stripped from the visible text
    assert visible == "Answer."


def test_last_project_block_wins() -> None:
    first = {"name": "first", "description": "a"}
    second = {"name": "second", "description": "b"}
    text = f"```project\n{json.dumps(first)}\n```\n\nthen\n\n```project\n{json.dumps(second)}\n```"
    _, _, project = extract_reply_blocks(text, include_project=True)
    assert project is not None
    assert project.name == "second"


def test_invalid_asset_ids_type_rejected() -> None:
    bad = {"name": "x", "asset_ids": "not-a-list", "description": "d"}
    text = f"```project\n{json.dumps(bad)}\n```"
    _, _, project = extract_reply_blocks(text, include_project=True)
    assert project is None
