"""Outbound Azure DevOps actions — a stub only.

Describes the shape a future outbound action would take (state change,
comment, PR link) so it stays reviewable, while guaranteeing nothing writes
to DevOps in v1: `perform` always raises. Nothing outside this module's own
test imports `perform`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, NoReturn

ACTION_STATE_CHANGE = "state_change"
ACTION_COMMENT = "comment"
ACTION_PR_LINK = "pr_link"

OUTBOUND_REFUSAL = "outbound requires per-action user approval"


@dataclass(frozen=True)
class ProposedOutboundAction:
    """A proposed write to DevOps — described, never executed, by this module."""

    kind: str  # ACTION_STATE_CHANGE | ACTION_COMMENT | ACTION_PR_LINK
    work_item_id: int
    summary: str
    payload: dict[str, Any]


def perform(action: ProposedOutboundAction) -> NoReturn:
    raise NotImplementedError(OUTBOUND_REFUSAL)
