"""The transcript a chat carries into another agent's fresh CLI session."""

from __future__ import annotations

from app.api.v1.chat.service import TRANSCRIPT_MAX_CHARS, _transcript
from app.db.models.chat import ChatMessage


def _msg(role: str, content: str) -> ChatMessage:
    return ChatMessage(role=role, content=content)


def test_errors_are_left_out_and_order_is_kept() -> None:
    text = _transcript(
        [_msg("user", "one"), _msg("error", "boom"), _msg("assistant", "two"), _msg("user", "3")]
    )
    assert "boom" not in text
    assert text.index("User: one") < text.index("Assistant: two") < text.index("User: 3")
    assert "omitted" not in text


def test_over_the_cap_keeps_the_newest_turns() -> None:
    messages = [
        _msg("user" if i % 2 == 0 else "assistant", f"turn-{i:03d} " + "x" * 1000)
        for i in range(60)
    ]
    text = _transcript(messages)

    assert len(text) < TRANSCRIPT_MAX_CHARS + 500
    assert "turn-059" in text
    assert "turn-000" not in text
    assert "(earlier messages omitted)" in text


def test_a_single_oversized_newest_turn_is_clipped_not_dropped() -> None:
    text = _transcript([_msg("assistant", "y" * (TRANSCRIPT_MAX_CHARS * 2))])
    assert "Assistant: yyy" in text
    assert len(text) < TRANSCRIPT_MAX_CHARS + 500


def test_secrets_in_earlier_replies_are_redacted() -> None:
    text = _transcript([_msg("user", "q"), _msg("assistant", "key AKIAIOSFODNN7EXAMPLE")])
    assert "AKIAIOSFODNN7EXAMPLE" not in text
