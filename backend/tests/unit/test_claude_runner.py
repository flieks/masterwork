"""ClaudeRunner against a mocked asyncio subprocess (never the real CLI)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.services.claude_runner import APP_SYSTEM_PROMPT, ClaudeRunner, ClaudeRunnerError
from app.services.proposal_parser import extract_proposal


class FakeProc:
    def __init__(
        self,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        hang: bool = False,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self._hang = hang
        self.killed = False

    async def communicate(self) -> tuple[bytes, bytes]:
        if self._hang:
            await asyncio.sleep(3600)
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return self.returncode


def _patch(monkeypatch: pytest.MonkeyPatch, proc: FakeProc) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    async def fake_exec(*args: str, **kwargs: Any) -> FakeProc:
        captured["args"] = list(args)
        captured["kwargs"] = kwargs
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return captured


def _runner(timeout: int = 300) -> ClaudeRunner:
    return ClaudeRunner(bin="claude", model="opus", timeout_seconds=timeout)


async def test_first_message_builds_system_prompt_args(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = FakeProc(stdout=json.dumps({"result": "hello", "session_id": "s1"}).encode())
    captured = _patch(monkeypatch, proc)

    result = await _runner().run("hi there", system_prompt=APP_SYSTEM_PROMPT)

    assert result.reply == "hello"
    assert result.session_id == "s1"
    args = captured["args"]
    assert args[0] == "claude"
    assert "-p" in args and "hi there" in args
    assert args[args.index("--model") + 1] == "opus"
    assert "--output-format" in args and "json" in args
    # allowedTools passes the three read-only tools as separate tokens.
    idx = args.index("--allowedTools")
    assert args[idx + 1 : idx + 4] == ["Read", "Glob", "Grep"]
    # Write-capable tools are hard-denied (user settings may auto-approve them),
    # and the user's global MCP servers are excluded.
    idx = args.index("--disallowedTools")
    assert args[idx + 1 : idx + 7] == ["Bash", "Edit", "MultiEdit", "Write", "NotebookEdit", "Task"]
    assert "--strict-mcp-config" in args
    assert "--append-system-prompt" in args
    assert "--resume" not in args
    assert captured["kwargs"]["cwd"].endswith("/.claude")


async def test_resume_message_builds_resume_args(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = FakeProc(stdout=json.dumps({"result": "again", "session_id": "s2"}).encode())
    captured = _patch(monkeypatch, proc)

    result = await _runner().run("follow up", resume_session_id="prev-session")

    assert result.session_id == "s2"
    args = captured["args"]
    assert args[args.index("--resume") + 1] == "prev-session"
    assert "--append-system-prompt" not in args


async def test_reply_with_proposal_block_is_returned_intact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reply = (
        "Here you go.\n\n```proposal\n"
        '{"summary": "x", "changes": ['
        '{"path": "/a.md", "action": "update", "new_content": "z", "description": "d"}'
        "]}\n```"
    )
    proc = FakeProc(stdout=json.dumps({"result": reply, "session_id": "s3"}).encode())
    _patch(monkeypatch, proc)

    result = await _runner().run("please edit")

    # The runner returns the raw reply; parsing happens downstream.
    assert "```proposal" in result.reply
    text, proposal = extract_proposal(result.reply)
    assert proposal is not None
    assert proposal.changes[0].path == "/a.md"
    assert text == "Here you go."


async def test_nonzero_exit_raises_with_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = FakeProc(stdout=b"", stderr=b"boom happened", returncode=1)
    _patch(monkeypatch, proc)

    with pytest.raises(ClaudeRunnerError) as exc:
        await _runner().run("hi")
    assert "boom happened" in str(exc.value)
    assert "code 1" in str(exc.value)


async def test_timeout_kills_process(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = FakeProc(hang=True)
    _patch(monkeypatch, proc)

    with pytest.raises(ClaudeRunnerError) as exc:
        await _runner(timeout=0).run("hi")
    assert "timed out" in str(exc.value)
    assert proc.killed is True


async def test_unparseable_stdout_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = FakeProc(stdout=b"not json at all")
    _patch(monkeypatch, proc)

    with pytest.raises(ClaudeRunnerError) as exc:
        await _runner().run("hi")
    assert "not valid JSON" in str(exc.value)


async def test_missing_keys_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = FakeProc(stdout=json.dumps({"result": "hi"}).encode())  # no session_id
    _patch(monkeypatch, proc)

    with pytest.raises(ClaudeRunnerError):
        await _runner().run("hi")
