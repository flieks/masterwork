"""CodexRunner against a fake `codex` executable on a tmp PATH — never the real CLI.

The fake records its argv, cwd and stdin, then replays scripted JSONL, so these
tests cover the real subprocess path: argument building, the DEVNULL stdin that
keeps `codex exec` from waiting on input, parsing, exit codes and the timeout kill.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest

from app.services.agent_runner import AgentRunnerError
from app.services.codex_runner import CodexRunner, toml_string

_FAKE = """\
import json, os, sys, time
record = {"argv": sys.argv[1:], "cwd": os.getcwd(), "stdin": sys.stdin.read(), "pid": os.getpid()}
with open(os.environ["FAKE_CODEX_LOG"], "w") as fh:
    json.dump(record, fh)
script = json.loads(os.environ["FAKE_CODEX_SCRIPT"])
time.sleep(script.get("sleep", 0))
for line in script.get("stdout", []):
    print(line)
sys.stderr.write(script.get("stderr", ""))
sys.exit(script.get("exit", 0))
"""

_THREAD = {"type": "thread.started", "thread_id": "01a0ae2c-thread"}
_TURN = {"type": "turn.started"}
_DONE = {
    "type": "turn.completed",
    "usage": {
        "input_tokens": 17242,
        "cached_input_tokens": 9984,
        "cache_write_input_tokens": 12,
        "output_tokens": 8,
        "reasoning_output_tokens": 0,
    },
}


def _message(text: str, item_id: str = "item_0") -> dict[str, Any]:
    return {
        "type": "item.completed",
        "item": {"id": item_id, "type": "agent_message", "text": text},
    }


@pytest.fixture
def fake_codex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "codex"
    exe.write_text(f"#!{sys.executable}\n{_FAKE}", encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    # Only the fake is reachable by name.
    monkeypatch.setenv("PATH", str(bin_dir))
    log = tmp_path / "codex-call.json"
    monkeypatch.setenv("FAKE_CODEX_LOG", str(log))
    return log


def _script(monkeypatch: pytest.MonkeyPatch, **script: Any) -> None:
    lines = [json.dumps(e) if isinstance(e, dict) else e for e in script.pop("events", [])]
    monkeypatch.setenv("FAKE_CODEX_SCRIPT", json.dumps({**script, "stdout": lines}))


def _runner(
    tmp_path: Path, *, model: str | None = "gpt-test", timeout: int = 30, **kw: Any
) -> CodexRunner:
    return CodexRunner(
        bin="codex", model=model, timeout_seconds=timeout, cwd=tmp_path / "cwd", **kw
    )


def _call(log: Path) -> dict[str, Any]:
    return json.loads(log.read_text(encoding="utf-8"))


def _configs(argv: list[str]) -> dict[str, Any]:
    """Every `-c key=value`, parsed as TOML the way codex parses it."""
    values: dict[str, Any] = {}
    for flag, value in zip(argv, argv[1:], strict=False):
        if flag == "-c":
            values.update(tomllib.loads(value))
    return values


async def test_first_call_builds_read_only_exec_args_and_parses_the_reply(
    fake_codex: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _script(
        monkeypatch,
        events=[_THREAD, _TURN, _message("thinking…"), _message("ok ZEBRA", "item_1"), _DONE],
    )
    system = 'You are "Masterwork" 🦓\nline two\ttab \\ backslash \x7f del'

    result = await _runner(tmp_path).run("-starts with a dash", system_prompt=system)

    assert result.reply == "ok ZEBRA"  # the LAST agent message
    assert result.session_id == "01a0ae2c-thread"
    assert result.stats["model"] == "gpt-test"
    assert result.stats["num_turns"] == 1
    assert result.stats["cost_usd"] is None
    assert result.stats["input_tokens"] == 17242
    assert result.stats["output_tokens"] == 8
    assert result.stats["cache_read_tokens"] == 9984
    assert result.stats["cache_creation_tokens"] == 12
    assert isinstance(result.stats["duration_ms"], int)

    call = _call(fake_codex)
    argv = call["argv"]
    assert argv[:1] == ["exec"]
    assert "--json" in argv and "--skip-git-repo-check" in argv
    assert argv[argv.index("-s") + 1] == "read-only"
    assert argv[argv.index("-m") + 1] == "gpt-test"
    for feature in (
        "plugins",
        "apps",
        "image_generation",
        "browser_use",
        "computer_use",
        "multi_agent",
    ):
        assert argv[argv.index(feature) - 1] == "--disable"
    configs = _configs(argv)
    assert configs["web_search"] == "disabled"
    assert configs["developer_instructions"] == system  # survives TOML intact
    assert "model_reasoning_effort" not in configs
    assert argv[-2:] == ["--", "-starts with a dash"]
    assert call["stdin"] == ""  # DEVNULL, so exec never waits for more input
    assert call["cwd"] == os.path.realpath(tmp_path / "cwd")


async def test_resume_uses_the_resume_subcommand_without_a_sandbox_flag(
    fake_codex: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _script(monkeypatch, events=[_THREAD, _message("again"), _DONE])

    await _runner(tmp_path, model=None, reasoning_effort="low").run(
        "follow up", resume_session_id="thread-1", system_prompt="be brief"
    )

    argv = _call(fake_codex)["argv"]
    assert argv[:3] == ["exec", "resume", "thread-1"]
    assert "-s" not in argv  # `exec resume` rejects it
    assert "-m" not in argv  # None leaves the user's configured model
    configs = _configs(argv)
    assert configs["sandbox_mode"] == "read-only"
    assert configs["developer_instructions"] == "be brief"  # re-sent on every turn
    assert configs["model_reasoning_effort"] == "low"
    assert argv[-2:] == ["--", "follow up"]


async def test_turn_failed_raises_with_its_message(
    fake_codex: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failed = {"type": "turn.failed", "error": {"message": "usage limit reached"}}
    _script(monkeypatch, events=[_THREAD, _TURN, failed])

    with pytest.raises(AgentRunnerError) as exc:
        await _runner(tmp_path).run("hi")
    assert "usage limit reached" in str(exc.value)


async def test_nonzero_exit_reports_the_error_event(
    fake_codex: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _script(
        monkeypatch,
        events=[{"type": "error", "message": "model not supported"}],
        stderr="some stderr",
        exit=1,
    )

    with pytest.raises(AgentRunnerError) as exc:
        await _runner(tmp_path).run("hi")
    assert "code 1" in str(exc.value)
    assert "model not supported" in str(exc.value)


async def test_a_retry_notice_before_a_completed_turn_is_not_fatal(
    fake_codex: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    retry = {"type": "error", "message": "Reconnecting... 1/5"}
    _script(monkeypatch, events=[_THREAD, retry, _message("fine"), _DONE])

    result = await _runner(tmp_path).run("hi")
    assert result.reply == "fine"


async def test_non_json_noise_is_ignored(
    fake_codex: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _script(
        monkeypatch,
        events=[
            "Reading additional input from stdin...",
            _THREAD,
            "[2026-09-17T10:00:00] warning: something",
            "[1, 2, 3]",
            _message("hello"),
            "",
            _DONE,
        ],
    )

    result = await _runner(tmp_path).run("hi")
    assert result.reply == "hello"
    assert result.session_id == "01a0ae2c-thread"


async def test_no_agent_message_raises(
    fake_codex: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _script(monkeypatch, events=[_THREAD, _DONE])

    with pytest.raises(AgentRunnerError) as exc:
        await _runner(tmp_path).run("hi")
    assert "no reply" in str(exc.value)


async def test_timeout_kills_the_process(
    fake_codex: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _script(monkeypatch, events=[_THREAD], sleep=30)

    with pytest.raises(AgentRunnerError) as exc:
        await _runner(tmp_path, timeout=1).run("hi")
    assert "timed out after 1s" in str(exc.value)
    pid = _call(fake_codex)["pid"]
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_missing_binary_raises_a_launch_error(tmp_path: Path) -> None:
    runner = CodexRunner(
        bin=str(tmp_path / "nope" / "codex"), model=None, timeout_seconds=5, cwd=tmp_path
    )
    with pytest.raises(AgentRunnerError) as exc:
        await runner.run("hi")
    assert "could not launch" in str(exc.value)


def test_toml_string_round_trips_what_json_escaping_would_break() -> None:
    for value in ["plain", 'quote " and \\ slash', "emoji 🦓 astral", "del \x7f", "ctrl \x01\n"]:
        assert tomllib.loads("k=" + toml_string(value))["k"] == value
