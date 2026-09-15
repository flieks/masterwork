"""skill_match_parser.extract_matches — same conventions as links_parser."""

from __future__ import annotations

from app.services.skill_match_parser import ParsedMatch, extract_matches


def test_bare_json_array_parses() -> None:
    text = '[{"name": "frontend-dev", "reason": "React work"}]'
    assert extract_matches(text) == [ParsedMatch(name="frontend-dev", reason="React work")]


def test_fenced_json_array_parses() -> None:
    text = 'Here you go:\n\n```json\n[{"name": "frontend-dev", "reason": "React work"}]\n```\n'
    assert extract_matches(text) == [ParsedMatch(name="frontend-dev", reason="React work")]


def test_array_of_junk_returns_none() -> None:
    assert extract_matches('[{"not_a_name": "frontend-dev"}]') is None
    assert extract_matches("[1, 2, 3]") is None


def test_non_json_prose_returns_none() -> None:
    assert extract_matches("Sorry, I can't help with that.") is None


def test_missing_reason_degrades_to_empty_string() -> None:
    assert extract_matches('[{"name": "frontend-dev"}]') == [
        ParsedMatch(name="frontend-dev", reason="")
    ]


def test_non_list_json_returns_none() -> None:
    assert extract_matches('{"name": "frontend-dev", "reason": "React work"}') is None
