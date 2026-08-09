"""The envelope contract: last fenced json block, nothing after it, role fields present."""

from __future__ import annotations

import json

from adw.envelopes import parse_envelope
from conftest import envelope


def fence(payload: dict) -> str:
    return "```json\n" + json.dumps(payload) + "\n```"


def test_parses_a_valid_build_envelope():
    text = "I built the thing.\n\n" + fence(envelope(changed_files=["app/main.py"]))
    result = parse_envelope(text, "build")
    assert result.ok
    assert result.envelope.changed_files == ["app/main.py"]
    assert result.envelope.status == "ok"


def test_last_fenced_block_wins():
    text = (
        "Here is an earlier example:\n\n"
        + fence(envelope(summary="DECOY", changed_files=["decoy.py"]))
        + "\n\nAnd the real envelope:\n\n"
        + fence(envelope(summary="REAL", changed_files=["real.py"]))
    )
    result = parse_envelope(text, "build")
    assert result.ok
    assert result.envelope.summary == "REAL"
    assert result.envelope.changed_files == ["real.py"]


def test_trailing_prose_after_the_envelope_fails():
    text = fence(envelope(changed_files=[])) + "\n\nLet me know if you want changes!"
    result = parse_envelope(text, "build")
    assert not result.ok
    assert "text after the envelope" in result.error


def test_a_non_json_final_fence_fails():
    text = fence(envelope(changed_files=[])) + "\n\n```bash\nuv run pytest\n```"
    result = parse_envelope(text, "build")
    assert not result.ok
    assert "not `json`" in result.error


def test_no_fence_at_all_fails():
    result = parse_envelope("I finished the work, all tests pass.", "build")
    assert not result.ok
    assert "no fenced code block" in result.error


def test_unparseable_json_fails():
    result = parse_envelope("```json\n{status: ok,}\n```", "build")
    assert not result.ok
    assert "not valid JSON" in result.error


def test_build_requires_changed_files():
    payload = envelope()
    payload.pop("changed_files")
    result = parse_envelope(fence(payload), "build")
    assert not result.ok
    assert "changed_files" in result.error


def test_plan_requires_artifacts():
    payload = envelope(changed_files=["plan.md"])
    payload.pop("artifacts")
    result = parse_envelope(fence(payload), "plan")
    assert not result.ok
    assert "artifacts" in result.error


def test_review_requires_approved_and_blocking():
    payload = envelope()
    payload.pop("approved")
    payload.pop("blocking")
    result = parse_envelope(fence(payload), "review")
    assert not result.ok
    assert "approved" in result.error and "blocking" in result.error


def test_review_does_not_require_changed_files():
    payload = {"status": "ok", "summary": "Reviewed.", "approved": True, "blocking": []}
    result = parse_envelope(fence(payload), "review")
    assert result.ok
    assert result.envelope.changed_files == []


def test_unknown_status_fails():
    result = parse_envelope(fence(envelope(status="done", changed_files=[])), "build")
    assert not result.ok
    assert "status" in result.error


def test_structured_blocking_entries_become_strings():
    payload = {
        "status": "ok",
        "summary": "Reviewed.",
        "approved": False,
        "blocking": [{"file": "app/main.py", "issue": "no auth"}],
    }
    result = parse_envelope(fence(payload), "review")
    assert result.ok
    assert "app/main.py" in result.envelope.blocking[0]


def test_summary_line_is_the_first_line_only():
    payload = envelope(
        summary="Add health endpoint\n\nAlso refactored the router.", changed_files=[]
    )
    result = parse_envelope(fence(payload), "build")
    assert result.envelope.summary_line == "Add health endpoint"


def test_a_json_array_envelope_is_rejected():
    result = parse_envelope("```json\n[1, 2]\n```", "build")
    assert not result.ok
    assert "JSON object" in result.error
