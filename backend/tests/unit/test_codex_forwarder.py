"""The Codex forwarder: what it posts, and what it reads out of a rollout file.

The script runs outside the app under a bare `python3`, so these tests double as
the check that it stays importable and stdlib-only. Payloads are shaped the way
Codex's hook docs describe them; rollout lines the way `~/.codex/sessions`
actually holds them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.observability.forwarders import codex as forwarder

CODEX = {
    "cwd": "/Users/dev/Projects/app",
    "transcript_path": "/Users/dev/.codex/sessions/2026/09/08/rollout-x.jsonl",
    "model": "gpt-6-astra",
    "permission_mode": "default",
}


def _event(name: str, **extra: Any) -> dict[str, Any]:
    return {"session_id": "01a0-thread", "hook_event_name": name, **CODEX, **extra}


def test_every_event_says_it_came_from_codex() -> None:
    body = forwarder.build_body(_event("Stop", turn_id="t1", transcript_path=""))
    assert body is not None
    assert body["source"] == "codex"
    assert body["model"] == "gpt-6-astra"
    assert body["cwd"] == CODEX["cwd"]
    assert body["payload"]["turn_id"] == "t1"


def test_event_without_a_session_id_is_dropped() -> None:
    assert forwarder.build_body({"hook_event_name": "Stop"}) is None


def test_session_start_carries_its_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(forwarder, "ancestry", lambda: ["123 codex exec …"])
    body = forwarder.build_body(_event("SessionStart", source="startup"))
    assert body is not None
    payload = body["payload"]
    assert payload["source"] == "startup"
    assert payload["launched_by"] == ["123 codex exec …"]
    assert payload["transcript_path"] == CODEX["transcript_path"]
    assert payload["permission_mode"] == "default"
    assert "stats" not in body


def test_prompt_is_carried_and_capped() -> None:
    body = forwarder.build_body(_event("UserPromptSubmit", turn_id="t1", prompt="x" * 5000))
    assert body is not None
    assert len(body["payload"]["prompt"]) == 4001  # 4000 chars plus the ellipsis


def test_a_shell_call_that_prints_a_skill_keeps_its_command_whole() -> None:
    """The ingest reads the skill name out of the command, so the command must
    arrive intact — not collapsed into a `_truncated` prefix."""
    body = forwarder.build_body(
        _event(
            "PreToolUse",
            turn_id="t1",
            tool_name="exec_command",
            tool_use_id="call_1",
            tool_input={
                "cmd": "sed -n '1,240p' /Users/dev/.agents/skills/code-review/SKILL.md",
                "workdir": "/Users/dev/Projects/app",
            },
        )
    )
    assert body is not None
    assert body["event_type"] == "PreToolUse"
    assert body["tool_name"] == "exec_command"
    assert body["payload"]["tool_input"]["cmd"].endswith("code-review/SKILL.md")


def test_a_custom_tool_string_input_is_filed_under_a_key() -> None:
    """Codex's `exec` tool takes a bare script; the ingest reads objects."""
    body = forwarder.build_body(
        _event("PostToolUse", tool_name="exec", tool_input="tools.exec_command({cmd: 'ls'})")
    )
    assert body is not None
    assert body["payload"]["tool_input"] == {"input": "tools.exec_command({cmd: 'ls'})"}


def test_huge_tool_payloads_collapse_instead_of_being_sent_whole() -> None:
    body = forwarder.build_body(
        _event("PostToolUse", tool_name="exec_command", tool_response={"output": "y" * 9000})
    )
    assert body is not None
    assert "_truncated" in body["payload"]["tool_response"]


def test_a_permission_request_says_what_the_person_is_being_asked() -> None:
    body = forwarder.build_body(
        _event("PermissionRequest", tool_name="exec_command", tool_input={"cmd": "rm -rf build"})
    )
    assert body is not None
    assert body["payload"]["message"] == "Codex needs your permission to run exec_command"
    assert body["payload"]["tool_input"] == {"cmd": "rm -rf build"}


def test_a_subagent_start_names_the_agent() -> None:
    body = forwarder.build_body(
        _event("SubagentStart", agent_type="spec_review", agent_id="agent-7")
    )
    assert body is not None
    assert body["event_type"] == "SubagentStart"
    assert body["payload"] == {"agent_type": "spec_review", "agent_id": "agent-7"}


def test_session_end_marks_the_session_ended() -> None:
    body = forwarder.build_body(_event("SessionEnd", transcript_path="", reason="exit"))
    assert body is not None
    assert body["ended"] is True
    assert body["payload"]["reason"] == "exit"


def test_stop_carries_the_answer_capped() -> None:
    body = forwarder.build_body(
        _event("Stop", transcript_path="", last_assistant_message="done " * 1000)
    )
    assert body is not None
    assert len(body["payload"]["last_assistant_message"]) == forwarder.ANSWER_CHARS


def test_a_headless_prompt_is_redacted_from_the_ancestry() -> None:
    assert forwarder.redact('codex exec --full-auto "rewrite my secret"') == "codex exec …"
    assert forwarder.redact("/usr/local/bin/codex exec 'x'") == "/usr/local/bin/codex exec …"
    assert forwarder.redact("claude -p 'secret'") == "claude -p …"
    assert forwarder.redact("node /usr/bin/masterwork") == "node /usr/bin/masterwork"


# ------------------------------------------------------------- rollout files ---


def _usage(**counts: int) -> dict[str, int]:
    base = {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
    }
    base.update(counts)
    base["total_tokens"] = base["input_tokens"] + base["output_tokens"]
    return base


def _token_count(ordinal: int, total: dict[str, int], last: dict[str, int]) -> str:
    """One `token_count` line, as Codex writes it after every model response."""
    return json.dumps(
        {
            "timestamp": f"2026-09-08T07:47:{ordinal:02d}.000Z",
            "ordinal": ordinal,
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": total,
                    "last_token_usage": last,
                    "model_context_window": 258400,
                },
            },
        }
    )


def _call(ordinal: int, name: str, kind: str = "custom_tool_call") -> str:
    return json.dumps(
        {
            "timestamp": "2026-09-08T07:47:00.000Z",
            "ordinal": ordinal,
            "type": "response_item",
            "payload": {"type": kind, "name": name, "call_id": f"call_{ordinal}"},
        }
    )


def _settings(model: str) -> str:
    return json.dumps(
        {
            "type": "event_msg",
            "payload": {"type": "thread_settings_applied", "thread_settings": {"model": model}},
        }
    )


def _rollout(tmp_path: Path, *lines: str) -> str:
    path = tmp_path / "rollout.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def test_totals_are_the_last_running_count_not_a_sum(tmp_path: Path) -> None:
    """Every `token_count` restates the thread's total, so summing them would
    bill the first response on every later line."""
    path = _rollout(
        tmp_path,
        _token_count(1, _usage(input_tokens=100, output_tokens=10), _usage(input_tokens=100)),
        _token_count(
            2,
            _usage(input_tokens=350, cached_input_tokens=200, output_tokens=40),
            _usage(input_tokens=250, cached_input_tokens=200, output_tokens=30),
        ),
    )
    assert forwarder.transcript_usage(path, "gpt-6-astra") == {
        "tokens_in": 350,
        "tokens_out": 40,
        "tokens_total": 390,
        "cache_read_tokens": 200,
    }


def test_an_unpriced_model_reports_tokens_and_no_cost(tmp_path: Path) -> None:
    path = _rollout(tmp_path, _token_count(1, _usage(input_tokens=100), _usage(input_tokens=100)))
    stats = forwarder.transcript_usage(path, "gpt-6-astra")
    assert "cost_usd" not in stats
    assert stats["tokens_in"] == 100


def test_a_priced_model_bills_cached_input_at_a_tenth(tmp_path: Path) -> None:
    path = _rollout(
        tmp_path,
        _token_count(
            1,
            _usage(input_tokens=1_000_000, cached_input_tokens=500_000, output_tokens=100_000),
            _usage(),
        ),
    )
    # $1.25/M in: 500k plain + 500k cached at 10%; $10/M out.
    assert forwarder.transcript_usage(path, "gpt-5-codex")["cost_usd"] == 0.625 + 0.0625 + 1.0


def test_a_dated_snapshot_bills_as_its_base_model() -> None:
    assert forwarder.price_of("gpt-5-2025-08-07") == forwarder.PRICES["gpt-5"]
    assert forwarder.price_of("GPT-5-mini") == forwarder.PRICES["gpt-5-mini"]
    # A variant this list has not heard of is not priced off its neighbour.
    assert forwarder.price_of("gpt-5-codex-max") is None
    assert forwarder.price_of("gpt-6-astra") is None


def test_the_rollouts_own_model_is_the_fallback(tmp_path: Path) -> None:
    path = _rollout(
        tmp_path,
        _settings("gpt-5-nano"),
        _token_count(1, _usage(input_tokens=1_000_000), _usage()),
    )
    assert forwarder.transcript_usage(path, None)["cost_usd"] == 0.05
    # What the hook said beats what the file says.
    assert "cost_usd" not in forwarder.transcript_usage(path, "gpt-6-astra")


def test_a_half_written_line_does_not_lose_the_rest(tmp_path: Path) -> None:
    path = tmp_path / "rollout.jsonl"
    path.write_text(
        _token_count(1, _usage(input_tokens=100), _usage()) + '\n{"type": "event_m',
        encoding="utf-8",
    )
    assert forwarder.transcript_usage(str(path), "x")["tokens_in"] == 100


def test_a_rollout_with_no_counts_reports_nothing(tmp_path: Path) -> None:
    assert forwarder.transcript_usage(_rollout(tmp_path, _settings("gpt-5")), None) == {}
    assert forwarder.transcript_usage(str(tmp_path / "gone.jsonl"), None) == {}


def test_context_samples_follow_the_last_response_and_carry_its_tools(tmp_path: Path) -> None:
    path = _rollout(
        tmp_path,
        _token_count(
            1,
            _usage(input_tokens=100, output_tokens=10),
            _usage(input_tokens=100, output_tokens=10),
        ),
        _call(2, "exec"),
        _call(3, "spawn_agent", kind="function_call"),
        _token_count(
            4,
            _usage(input_tokens=350, output_tokens=40),
            _usage(input_tokens=250, output_tokens=30),
        ),
    )
    samples = forwarder.context_samples(path)
    assert [s["message_id"] for s in samples] == ["turn-1", "turn-4"]
    assert [s["total_tokens"] for s in samples] == [100, 250]
    assert samples[1]["output_tokens"] == 30
    assert samples[0]["tools"] == []
    assert samples[1]["tools"] == ["exec", "spawn_agent"]
    assert samples[1]["at"] == "2026-09-08T07:47:04.000Z"
    assert all(s["is_sidechain"] is False for s in samples)


def test_stop_carries_the_totals_and_the_curve_when_the_rollout_has_them(tmp_path: Path) -> None:
    path = _rollout(tmp_path, _token_count(1, _usage(input_tokens=100), _usage(input_tokens=100)))
    stop = forwarder.build_body(_event("Stop", transcript_path=path))
    assert stop is not None
    assert stop["stats"]["tokens_in"] == 100
    assert stop["context_samples"][0]["message_id"] == "turn-1"

    mid_run = forwarder.build_body(_event("PostToolUse", tool_name="exec", transcript_path=path))
    assert mid_run is not None
    assert "stats" not in mid_run


def test_stop_carries_no_totals_when_the_rollout_is_unreadable(tmp_path: Path) -> None:
    body = forwarder.build_body(_event("Stop", transcript_path=str(tmp_path / "gone.jsonl")))
    assert body is not None
    assert "stats" not in body
    assert "context_samples" not in body


def test_ingest_url_comes_from_the_sidecar_connect_wrote(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / "config.json").write_text(json.dumps({"ingest_url": "http://localhost:9/x"}))
    monkeypatch.delenv("MASTERWORK_INGEST_URL", raising=False)
    monkeypatch.setattr(forwarder, "__file__", str(tmp_path / "codex.py"))
    assert forwarder.ingest_url() == "http://localhost:9/x"
