"""The Claude Code forwarder: what it posts, and where it posts it.

The script runs outside the app under a bare `python3`, so these tests double as
the check that it stays importable and stdlib-only.
"""

from __future__ import annotations

import base64
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


def test_a_truncated_spawn_keeps_its_identity_keys() -> None:
    body = forwarder.build_body(
        {
            "session_id": "s1",
            "hook_event_name": "PreToolUse",
            "tool_name": "Task",
            "tool_input": {
                "description": "Design the feature",
                "prompt": "x" * 9000,
                "subagent_type": "Plan",
            },
        }
    )
    assert body is not None
    tool_input = body["payload"]["tool_input"]
    assert "_truncated" in tool_input
    assert tool_input["subagent_type"] == "Plan"
    assert tool_input["description"] == "Design the feature"


PIXEL = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQ"
    "AAAABJRU5ErkJggg=="
)


def _screenshot(data: str = PIXEL, media_type: str = "image/png") -> dict[str, object]:
    """A tool response shaped the way a screenshot tool answers."""
    return {
        "content": [
            {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}
        ]
    }


def test_an_image_is_written_to_disk_instead_of_riding_the_payload(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The whole point: base64 in the payload is truncated to an unreadable
    prefix, so the bytes have to leave the payload before any cap sees them."""
    monkeypatch.setenv("MASTERWORK_MEDIA_DIR", str(tmp_path))

    body = forwarder.build_body(
        {
            "session_id": "s1",
            "hook_event_name": "PostToolUse",
            "tool_name": "mcp__Claude_Browser__computer",
            "tool_response": _screenshot(),
        }
    )

    assert body is not None
    ref = body["payload"]["tool_response"]["content"][0]
    assert ref["type"] == "image_ref"
    assert ref["media_type"] == "image/png"
    assert ref["media_id"].endswith(".png")
    written = tmp_path / "s1" / ref["media_id"]
    assert written.read_bytes() == base64.b64decode(PIXEL)
    assert PIXEL not in json.dumps(body)


def test_the_same_image_twice_is_one_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MASTERWORK_MEDIA_DIR", str(tmp_path))
    raw = {"session_id": "s1", "hook_event_name": "PostToolUse", "tool_response": _screenshot()}

    first = forwarder.build_body(raw)
    second = forwarder.build_body(raw)

    assert first is not None and second is not None
    assert first["payload"] == second["payload"]  # content addressed, so identical
    assert len(list((tmp_path / "s1").iterdir())) == 1


def test_a_session_id_never_becomes_a_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MASTERWORK_MEDIA_DIR", str(tmp_path))

    forwarder.build_body(
        {
            "session_id": "../../escaped",
            "hook_event_name": "PostToolUse",
            "tool_response": _screenshot(),
        }
    )

    assert (tmp_path / "escaped").is_dir()
    assert not (tmp_path.parent / "escaped").exists()


def test_an_undecodable_image_costs_its_bytes_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MASTERWORK_MEDIA_DIR", str(tmp_path))

    body = forwarder.build_body(
        {
            "session_id": "s1",
            "hook_event_name": "PostToolUse",
            "tool_response": _screenshot(data="not base64 at all!!"),
        }
    )

    assert body is not None
    assert body["payload"]["tool_response"]["content"][0] == {
        "type": "image_omitted",
        "media_type": "image/png",
    }


def test_an_image_held_by_url_is_left_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Nothing to write, and the reference it already carries is small."""
    monkeypatch.setenv("MASTERWORK_MEDIA_DIR", str(tmp_path))
    block = {"type": "image", "source": {"type": "url", "url": "https://example.test/a.png"}}

    body = forwarder.build_body(
        {"session_id": "s1", "hook_event_name": "PostToolUse", "tool_response": {"c": [block]}}
    )

    assert body is not None
    assert body["payload"]["tool_response"]["c"][0] == block
    assert not tmp_path.exists() or not any(tmp_path.iterdir())


def test_media_dir_comes_from_the_sidecar_connect_wrote(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "config.json").write_text(json.dumps({"media_dir": "/var/pics"}))
    monkeypatch.delenv("MASTERWORK_MEDIA_DIR", raising=False)
    monkeypatch.setattr(forwarder, "__file__", str(tmp_path / "claude_code.py"))
    assert forwarder.media_dir() == Path("/var/pics")


def test_a_stage_child_forwards_what_the_runner_told_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(forwarder.FACTORY_RUN_ID_ENV, "abc123")
    monkeypatch.setenv(forwarder.FACTORY_STAGE_ENV, "build")
    monkeypatch.setattr(forwarder, "ancestry", lambda: [])

    body = forwarder.build_body({"session_id": "s1", "hook_event_name": "SessionStart"})

    assert body is not None
    assert body["payload"]["factory_run_id"] == "abc123"
    assert body["payload"]["factory_stage"] == "build"


def test_an_ordinary_session_says_nothing_about_a_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    """The signal is only meaningful because it is absent everywhere else."""
    monkeypatch.delenv(forwarder.FACTORY_RUN_ID_ENV, raising=False)
    monkeypatch.delenv(forwarder.FACTORY_STAGE_ENV, raising=False)
    monkeypatch.setattr(forwarder, "ancestry", lambda: [])

    body = forwarder.build_body({"session_id": "s1", "hook_event_name": "SessionStart"})

    assert body is not None
    assert "factory_run_id" not in body["payload"]
    assert "factory_stage" not in body["payload"]


def test_a_stage_name_that_went_missing_still_leaves_the_run_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(forwarder.FACTORY_RUN_ID_ENV, "abc123")
    monkeypatch.setenv(forwarder.FACTORY_STAGE_ENV, "   ")
    monkeypatch.setattr(forwarder, "ancestry", lambda: [])

    body = forwarder.build_body({"session_id": "s1", "hook_event_name": "SessionStart"})

    assert body is not None
    assert body["payload"]["factory_run_id"] == "abc123"
    assert "factory_stage" not in body["payload"]


def test_the_signal_only_rides_session_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repeating it on every tool call would bloat the stream to say the same thing."""
    monkeypatch.setenv(forwarder.FACTORY_RUN_ID_ENV, "abc123")
    body = forwarder.build_body(
        {"session_id": "s1", "hook_event_name": "PostToolUse", "tool_name": "Read"}
    )
    assert body is not None
    assert "factory_run_id" not in body["payload"]


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


def _assistant(message_id: str, model: str, **usage: object) -> str:
    """One transcript line, shaped as Claude Code writes it."""
    return json.dumps(
        {"type": "assistant", "message": {"id": message_id, "model": model, "usage": usage}}
    )


def test_cost_is_computed_from_the_transcript_since_claude_code_records_none(
    tmp_path: Path,
) -> None:
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        _assistant("msg_1", "claude-opus-5", input_tokens=1_000_000, output_tokens=1_000_000) + "\n"
    )
    assert forwarder.transcript_usage(str(transcript)) == {
        "cost_usd": 30.0,  # $5 in + $25 out
        "tokens_in": 1_000_000,
        "tokens_out": 1_000_000,
        "tokens_total": 2_000_000,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }


def test_cache_is_billed_at_its_own_rates(tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        _assistant(
            "msg_1",
            "claude-opus-5",
            input_tokens=0,
            output_tokens=0,
            cache_read_input_tokens=1_000_000,
            cache_creation_input_tokens=2_000_000,
            cache_creation={
                "ephemeral_5m_input_tokens": 1_000_000,
                "ephemeral_1h_input_tokens": 1_000_000,
            },
        )
    )
    # $5/M input × (0.1 to read, 1.25 to write for 5m, 2 for 1h).
    assert forwarder.transcript_usage(str(transcript))["cost_usd"] == 0.5 + 6.25 + 10.0


def test_a_write_with_no_ttl_breakdown_bills_as_the_5m_default(tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        _assistant(
            "msg_1",
            "claude-opus-5",
            input_tokens=0,
            output_tokens=0,
            cache_creation_input_tokens=1_000_000,
        )
    )
    assert forwarder.transcript_usage(str(transcript))["cost_usd"] == 6.25


def test_fast_mode_bills_at_its_premium(tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        _assistant("msg_1", "claude-opus-5", input_tokens=1_000_000, output_tokens=0, speed="fast")
    )
    assert forwarder.transcript_usage(str(transcript))["cost_usd"] == 10.0


def test_one_response_split_across_lines_is_counted_once(tmp_path: Path) -> None:
    """Text and a tool call are separate lines carrying the same cumulative
    usage — summing them would double the bill."""
    line = _assistant("msg_1", "claude-opus-5", input_tokens=1_000_000, output_tokens=0)
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(line + "\n" + line + "\n")
    assert forwarder.transcript_usage(str(transcript))["cost_usd"] == 5.0


def test_a_half_written_line_does_not_lose_the_rest(tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        _assistant("msg_1", "claude-opus-5", input_tokens=1_000_000, output_tokens=0)
        + "\n"
        + '{"type": "assis'
    )
    assert forwarder.transcript_usage(str(transcript))["cost_usd"] == 5.0


def test_an_unpriced_model_still_reports_its_tokens(tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(_assistant("msg_1", "gpt-something", input_tokens=100, output_tokens=1))
    stats = forwarder.transcript_usage(str(transcript))
    assert stats["cost_usd"] == 0.0
    assert stats["tokens_total"] == 101


def test_a_transcript_that_is_not_there_reports_nothing(tmp_path: Path) -> None:
    assert forwarder.transcript_usage(str(tmp_path / "gone.jsonl")) == {}


def test_stop_carries_the_totals_and_other_events_do_not(tmp_path: Path) -> None:
    transcript = tmp_path / "session.jsonl"
    transcript.write_text(
        _assistant("msg_1", "claude-opus-5", input_tokens=1_000_000, output_tokens=0)
    )
    stop = forwarder.build_body(
        {"session_id": "s1", "hook_event_name": "Stop", "transcript_path": str(transcript)}
    )
    assert stop is not None
    assert stop["stats"]["cost_usd"] == 5.0

    mid_run = forwarder.build_body(
        {"session_id": "s1", "hook_event_name": "PostToolUse", "transcript_path": str(transcript)}
    )
    assert mid_run is not None
    assert "stats" not in mid_run
