"""Unit tests for the ```simulation block parser."""

from __future__ import annotations

import json

from app.services.simulation_parser import extract_simulation


def _block(payload: object) -> str:
    return f"Some prose.\n\n```simulation\n{json.dumps(payload)}\n```\n"


def _valid_payload() -> dict:
    return {
        "score": 72,
        "verdict": "Mostly works.",
        "summary": "Step 1 ran skill A.",
        "analysis": "Gap in step 2.",
        "trace_mermaid": 'flowchart TD\n  A["start"]-->B["end"]',
        "suggestions": [
            {
                "title": "Sharpen trigger",
                "impact": "high",
                "rationale": "Trigger text is vague.",
                "changes": [
                    {
                        "path": "/tmp/skills/a/SKILL.md",
                        "action": "update",
                        "new_content": "new content",
                        "description": "rewrite trigger",
                    }
                ],
            }
        ],
    }


def test_valid_block_parses() -> None:
    parsed = extract_simulation(_block(_valid_payload()))
    assert parsed is not None
    assert parsed.score == 72
    assert parsed.verdict == "Mostly works."
    assert parsed.trace_mermaid is not None and parsed.trace_mermaid.startswith("flowchart TD")
    assert len(parsed.suggestions) == 1
    suggestion = parsed.suggestions[0]
    assert suggestion.impact == "high"
    assert suggestion.changes[0].action == "update"


def test_no_block_returns_none() -> None:
    assert extract_simulation("no block here") is None


def test_invalid_json_returns_none() -> None:
    assert extract_simulation("```simulation\n{not json\n```") is None


def test_score_clamped_to_range() -> None:
    payload = _valid_payload()
    payload["score"] = 250
    parsed = extract_simulation(_block(payload))
    assert parsed is not None and parsed.score == 100

    payload["score"] = -5
    parsed = extract_simulation(_block(payload))
    assert parsed is not None and parsed.score == 0


def test_non_int_score_rejected() -> None:
    payload = _valid_payload()
    payload["score"] = "85"
    assert extract_simulation(_block(payload)) is None
    payload["score"] = True  # bool is an int subclass — still not a score
    assert extract_simulation(_block(payload)) is None


def test_missing_summary_rejected() -> None:
    payload = _valid_payload()
    payload["summary"] = ""
    assert extract_simulation(_block(payload)) is None


def test_unknown_impact_defaults_to_medium() -> None:
    payload = _valid_payload()
    payload["suggestions"][0]["impact"] = "critical"
    parsed = extract_simulation(_block(payload))
    assert parsed is not None and parsed.suggestions[0].impact == "medium"


def test_bad_change_action_rejects_block() -> None:
    payload = _valid_payload()
    payload["suggestions"][0]["changes"][0]["action"] = "rename"
    assert extract_simulation(_block(payload)) is None


def test_suggestions_optional() -> None:
    payload = _valid_payload()
    del payload["suggestions"]
    parsed = extract_simulation(_block(payload))
    assert parsed is not None and parsed.suggestions == []


def test_missing_trace_mermaid_allowed() -> None:
    payload = _valid_payload()
    del payload["trace_mermaid"]
    parsed = extract_simulation(_block(payload))
    assert parsed is not None and parsed.trace_mermaid is None


def test_last_block_wins() -> None:
    first = _valid_payload()
    second = _valid_payload()
    second["score"] = 15
    second["checklist"] = []  # keep the holistic score authoritative for this case
    text = _block(first) + "\nmore prose\n" + _block(second)
    parsed = extract_simulation(text)
    assert parsed is not None and parsed.score == 15


def test_checklist_overrides_score() -> None:
    payload = _valid_payload()
    payload["score"] = 5  # ignored once a checklist is present
    payload["checklist"] = [
        {"id": "a", "title": "A", "weight": 3, "status": "pass", "evidence": ""},
        {"id": "b", "title": "B", "weight": 1, "status": "fail", "evidence": ""},
    ]
    parsed = extract_simulation(_block(payload))
    assert parsed is not None
    # 3/(3+1) = 75
    assert parsed.score == 75
    assert len(parsed.checklist) == 2
    assert parsed.checklist[0].id == "a"


def test_partial_counts_half_and_na_excluded() -> None:
    payload = _valid_payload()
    payload["checklist"] = [
        {"id": "a", "title": "A", "weight": 2, "status": "pass", "evidence": ""},
        {"id": "b", "title": "B", "weight": 2, "status": "partial", "evidence": ""},
        {"id": "c", "title": "C", "weight": 5, "status": "na", "evidence": "external DNS"},
    ]
    parsed = extract_simulation(_block(payload))
    # na item excluded entirely; (2*1 + 2*0.5) / (2+2) = 3/4 = 75
    assert parsed is not None and parsed.score == 75


def test_all_na_checklist_falls_back_to_model_score() -> None:
    payload = _valid_payload()
    payload["score"] = 42
    payload["checklist"] = [
        {"id": "a", "title": "A", "weight": 1, "status": "na", "evidence": "human gate"},
    ]
    parsed = extract_simulation(_block(payload))
    assert parsed is not None and parsed.score == 42


def test_missing_checklist_falls_back_and_is_empty() -> None:
    payload = _valid_payload()  # no checklist key
    parsed = extract_simulation(_block(payload))
    assert parsed is not None
    assert parsed.checklist == []
    assert parsed.score == 72


def test_malformed_checklist_item_skipped_not_fatal() -> None:
    payload = _valid_payload()
    payload["checklist"] = [
        {"id": "a", "title": "A", "weight": 1, "status": "pass", "evidence": ""},
        {"title": "no id"},  # skipped
        {"id": "b", "title": "B", "weight": 9, "status": "bogus", "evidence": ""},
    ]
    parsed = extract_simulation(_block(payload))
    assert parsed is not None
    assert [c.id for c in parsed.checklist] == ["a", "b"]
    # weight clamped to 3, invalid status -> fail: (1*1 + 3*0) / (1+3) = 25
    assert parsed.checklist[1].weight == 3
    assert parsed.checklist[1].status == "fail"
    assert parsed.score == 25


def test_link_change_accepted_for_suggestions() -> None:
    payload = _valid_payload()
    payload["suggestions"][0]["changes"] = [
        {
            "path": "/tmp/skills/mobile-dev/SKILL.md",
            "action": "link",
            "new_content": None,
            "description": "link the existing mobile-dev skill",
        }
    ]
    parsed = extract_simulation(_block(payload))
    assert parsed is not None
    assert parsed.suggestions[0].changes[0].action == "link"


def test_unlink_change_accepted_for_suggestions() -> None:
    payload = _valid_payload()
    payload["suggestions"][0]["changes"] = [
        {
            "path": "/tmp/skills/mobile-dev/SKILL.md",
            "action": "unlink",
            "new_content": None,
            "description": "drop the unused mobile-dev skill from the toolkit",
        }
    ]
    parsed = extract_simulation(_block(payload))
    assert parsed is not None
    assert parsed.suggestions[0].changes[0].action == "unlink"


def test_unknown_change_action_still_rejected() -> None:
    payload = _valid_payload()
    payload["suggestions"][0]["changes"][0]["action"] = "symlink"
    assert extract_simulation(_block(payload)) is None
