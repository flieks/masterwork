"""work_outbound.perform is a stub — it always refuses."""

from __future__ import annotations

import pytest

from app.services.work_outbound import (
    ACTION_COMMENT,
    ACTION_PR_LINK,
    ACTION_STATE_CHANGE,
    OUTBOUND_REFUSAL,
    ProposedOutboundAction,
    perform,
)


@pytest.mark.parametrize("kind", [ACTION_STATE_CHANGE, ACTION_COMMENT, ACTION_PR_LINK])
def test_perform_always_raises_not_implemented(kind: str) -> None:
    action = ProposedOutboundAction(kind=kind, work_item_id=1, summary="x", payload={})
    with pytest.raises(NotImplementedError, match=OUTBOUND_REFUSAL):
        perform(action)
