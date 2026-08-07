"""Fenced ```scenario block extraction for scenario generation."""

from __future__ import annotations

from app.services.scenario_parser import extract_scenario


def test_extracts_fenced_block() -> None:
    text = "Here you go:\n\n```scenario\nDeploy the API to Azure and fix the CORS error.\n```"
    assert extract_scenario(text) == "Deploy the API to Azure and fix the CORS error."


def test_first_block_wins_and_fences_stripped() -> None:
    text = "```scenario\nfirst\n```\n\n```scenario\nsecond\n```"
    assert extract_scenario(text) == "first"


def test_multiline_body_preserved() -> None:
    text = "```scenario\nline one.\nline two.\n```"
    assert extract_scenario(text) == "line one.\nline two."


def test_no_block_returns_none() -> None:
    assert extract_scenario("Just prose, no fenced block here.") is None


def test_empty_block_returns_none() -> None:
    assert extract_scenario("```scenario\n\n```") is None
