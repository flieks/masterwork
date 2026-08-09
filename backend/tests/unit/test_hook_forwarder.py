"""The Claude Code forwarder: what it posts, and where it posts it.

The script runs outside the app under a bare `python3`, so these tests double as
the check that it stays importable and stdlib-only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.observability.forwarders import claude_code as forwarder

DEFAULT = "http://localhost:8008/api/v1/hooks/events"


def test_event_without_a_session_id_is_dropped() -> None:
    assert forwarder.build_body({"hook_event_name": "Stop"}) is None


def test_prompt_is_carried_and_capped() -> None:
    body = forwarder.build_body(
        {"session_id": "s1", "hook_event_name": "UserPromptSubmit", "prompt": "x" * 5000}
    )
    assert body is not None
    assert body["session_id"] == "s1"
    assert len(body["payload"]["prompt"]) == 4001  # 4000 chars plus the ellipsis


def test_session_end_marks_the_session_ended() -> None:
    body = forwarder.build_body(
        {"session_id": "s1", "hook_event_name": "SessionEnd", "reason": "exit"}
    )
    assert body is not None
    assert body["ended"] is True
    assert body["payload"]["reason"] == "exit"


def test_huge_tool_payloads_collapse_instead_of_being_sent_whole() -> None:
    body = forwarder.build_body(
        {
            "session_id": "s1",
            "hook_event_name": "PostToolUse",
            "tool_name": "Read",
            "tool_response": {"content": "y" * 9000},
        }
    )
    assert body is not None
    assert body["tool_name"] == "Read"
    assert "_truncated" in body["payload"]["tool_response"]


def test_headless_prompts_are_redacted_from_the_process_ancestry() -> None:
    assert forwarder.redact("claude -p 'rewrite my secret prompt'") == "claude -p …"
    assert forwarder.redact("node /usr/bin/masterwork") == "node /usr/bin/masterwork"


def test_ingest_url_falls_back_to_the_default_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MASTERWORK_INGEST_URL", raising=False)
    monkeypatch.setattr(forwarder, "__file__", "/nowhere/claude_code.py")
    assert forwarder.ingest_url() == DEFAULT


def test_ingest_url_comes_from_the_sidecar_connect_wrote(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "config.json").write_text(json.dumps({"ingest_url": "http://localhost:9/x"}))
    monkeypatch.delenv("MASTERWORK_INGEST_URL", raising=False)
    monkeypatch.setattr(forwarder, "__file__", str(tmp_path / "claude_code.py"))
    assert forwarder.ingest_url() == "http://localhost:9/x"


def test_env_beats_the_sidecar(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(json.dumps({"ingest_url": "http://localhost:9/x"}))
    monkeypatch.setenv("MASTERWORK_INGEST_URL", "http://localhost:1/y")
    monkeypatch.setattr(forwarder, "__file__", str(tmp_path / "claude_code.py"))
    assert forwarder.ingest_url() == "http://localhost:1/y"
