"""Extract fenced ```proposal and ```project JSON blocks from assistant replies.

The assistant may end a reply with a ``proposal`` block (concrete file changes)
and/or a ``project`` block (a project update). We take the LAST block of each
kind, JSON-parse it, and strip it from the visible text. Malformed JSON (or a
structurally invalid block) yields no value for that kind.

`extract_proposal` is the v1 single-block helper (proposal only, text kept as-is
on failure). `extract_reply_blocks` is the v1.1 combined extractor used by the
chat service: it parses both kinds and strips every occurrence of both.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

_VALID_ACTIONS = {"update", "create", "delete"}

# Fenced block: a line whose info string is `proposal`, its body, then a closing fence.
_PROPOSAL_RE = re.compile(
    r"^[ \t]*```proposal[^\n]*\n(?P<body>.*?)\n[ \t]*```[ \t]*$",
    re.DOTALL | re.MULTILINE,
)
_PROJECT_RE = re.compile(
    r"^[ \t]*```project[^\n]*\n(?P<body>.*?)\n[ \t]*```[ \t]*$",
    re.DOTALL | re.MULTILINE,
)


@dataclass(frozen=True)
class ParsedChange:
    path: str
    action: str
    new_content: str | None
    description: str


@dataclass(frozen=True)
class ParsedProposal:
    summary: str
    changes: list[ParsedChange]


@dataclass(frozen=True)
class ParsedProjectUpdate:
    name: str | None
    goal: str | None
    flow_mermaid: str | None
    asset_ids: list[str] | None
    description: str


def extract_proposal(text: str) -> tuple[str, ParsedProposal | None]:
    """Return (visible_text, proposal_or_none).

    On success the proposal block is stripped from `visible_text`. On any parse
    or validation failure the original `text` is returned unchanged.
    """
    matches = list(_PROPOSAL_RE.finditer(text))
    if not matches:
        return text, None

    match = matches[-1]
    proposal = _parse_body(match.group("body"))
    if proposal is None:
        return text, None

    stripped = (text[: match.start()] + text[match.end() :]).strip()
    return stripped, proposal


def _parse_body(body: str) -> ParsedProposal | None:
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    raw_changes = data.get("changes")
    if not isinstance(raw_changes, list):
        return None

    changes: list[ParsedChange] = []
    for raw in raw_changes:
        change = _parse_change(raw)
        if change is None:
            return None
        changes.append(change)

    summary = data.get("summary")
    return ParsedProposal(
        summary=summary if isinstance(summary, str) else "",
        changes=changes,
    )


def extract_reply_blocks(
    text: str, *, include_project: bool
) -> tuple[str, ParsedProposal | None, ParsedProjectUpdate | None]:
    """Return (visible_text, proposal_or_none, project_update_or_none).

    The LAST valid block of each kind wins; a malformed block yields None for
    that kind. Every occurrence of both block kinds is stripped from the visible
    text. When `include_project` is False, ``project`` blocks are still stripped
    but never produce a project update (a global session ignores them).
    """
    proposal_matches = list(_PROPOSAL_RE.finditer(text))
    proposal = _parse_body(proposal_matches[-1].group("body")) if proposal_matches else None

    project = None
    if include_project:
        project_matches = list(_PROJECT_RE.finditer(text))
        if project_matches:
            project = _parse_project_body(project_matches[-1].group("body"))

    stripped = _PROJECT_RE.sub("", _PROPOSAL_RE.sub("", text)).strip()
    return stripped, proposal, project


def _parse_project_body(body: str) -> ParsedProjectUpdate | None:
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    name = data.get("name")
    goal = data.get("goal")
    flow = data.get("flow_mermaid")
    asset_ids = data.get("asset_ids")

    for value in (name, goal, flow):
        if value is not None and not isinstance(value, str):
            return None
    if asset_ids is not None and (
        not isinstance(asset_ids, list) or not all(isinstance(a, str) for a in asset_ids)
    ):
        return None

    description = data.get("description")
    return ParsedProjectUpdate(
        name=name,
        goal=goal,
        flow_mermaid=flow,
        asset_ids=list(asset_ids) if asset_ids is not None else None,
        description=description if isinstance(description, str) else "",
    )


def _parse_change(
    raw: object, *, valid_actions: frozenset[str] | set[str] = _VALID_ACTIONS
) -> ParsedChange | None:
    if not isinstance(raw, dict):
        return None
    path = raw.get("path")
    action = raw.get("action")
    if not isinstance(path, str) or not path:
        return None
    if not isinstance(action, str) or action not in valid_actions:
        return None
    new_content = raw.get("new_content")
    if new_content is not None and not isinstance(new_content, str):
        return None
    description = raw.get("description")
    return ParsedChange(
        path=path,
        action=action,
        new_content=new_content,
        description=description if isinstance(description, str) else "",
    )
