"""Unit tests for the ```links block parser."""

from __future__ import annotations

import json

from app.services.links_parser import extract_links


def _block(payload: object) -> str:
    return f"I read the catalog.\n\n```links\n{json.dumps(payload)}\n```\n"


def test_valid_block_parses() -> None:
    links = extract_links(
        _block(
            {
                "links": [
                    {
                        "asset_id": "claude:skill:backend-dev",
                        "reason": "the API half",
                        "confidence": 92,
                    },
                    {"asset_id": "claude:agent:architect"},
                ]
            }
        )
    )
    assert links is not None
    assert [link.asset_id for link in links] == [
        "claude:skill:backend-dev",
        "claude:agent:architect",
    ]
    assert links[0].reason == "the API half"
    assert links[0].confidence == 92
    assert links[1].reason == ""  # missing reason is fine
    assert links[1].confidence == 70  # missing confidence lands in the recommended band


def test_confidence_is_clamped_and_coerced() -> None:
    links = extract_links(
        _block(
            {
                "links": [
                    {"asset_id": "a", "confidence": 140},
                    {"asset_id": "b", "confidence": -20},
                    {"asset_id": "c", "confidence": 64.6},
                    {"asset_id": "d", "confidence": "high"},
                    {"asset_id": "e", "confidence": True},
                ]
            }
        )
    )
    assert links is not None
    assert [link.confidence for link in links] == [100, 0, 65, 70, 70]


def test_no_block_or_malformed_yields_none() -> None:
    assert extract_links("no block here") is None
    assert extract_links("```links\nnot json\n```") is None
    assert extract_links(_block({"links": "not-a-list"})) is None
    assert extract_links(_block({"links": [{"reason": "no id"}]})) is None


def test_last_block_wins_and_empty_list_is_valid() -> None:
    text = _block({"links": [{"asset_id": "a"}]}) + _block({"links": []})
    assert extract_links(text) == []
