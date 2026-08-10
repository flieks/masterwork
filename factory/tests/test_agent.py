"""The `claude -p` dispatch: flag spelling, stream-json parsing, session resume."""

from __future__ import annotations

from pathlib import Path

from adw.agent import AgentSession
from adw.config import load_config
from conftest import FakeCLI, envelope


def session(repo: Path, stage: str = "build", **kwargs) -> AgentSession:
    cfg = load_config(repo)
    resolved = cfg.stages[stage]
    return AgentSession(
        stage=stage,
        model=resolved.model or "sonnet",
        cwd=repo,
        disallowed_tools=resolved.disallowed_tools,
        timeout_seconds=60,
        **kwargs,
    )


def test_build_args_spelling(git_repo: Path):
    agent = session(git_repo)
    args = agent.build_args("do the thing", resume=False)
    assert args[0] == "claude"
    assert args[1:3] == ["-p", "do the thing"]
    assert args[args.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in args
    assert args[args.index("--model") + 1] == "sonnet"
    assert args[args.index("--permission-mode") + 1] == "acceptEdits"
    assert "--strict-mcp-config" in args
    disallowed = args[args.index("--disallowedTools") + 1 :]
    assert disallowed[:4] == ["Bash", "Task", "WebFetch", "WebSearch"]
    assert "--resume" not in args


def test_the_system_prompt_is_appended_and_re_sent_on_resume(git_repo: Path):
    agent = session(git_repo)
    assert "--append-system-prompt" not in agent.build_args("x", resume=False)

    agent.system_prompt = "You are the BUILD stage."
    agent.session_id = "abc-123"
    args = agent.build_args("x", resume=True)
    # Each CLI process rebuilds its own system prompt, so a resume must carry it too.
    assert args[args.index("--append-system-prompt") + 1] == "You are the BUILD stage."
    assert args[args.index("--resume") + 1] == "abc-123"


def test_reviewer_args_are_read_only(git_repo: Path):
    args = session(git_repo, "review").build_args("review it", resume=False)
    tools = set(args[args.index("--disallowedTools") + 1 :])
    assert {"Edit", "Write", "NotebookEdit", "MultiEdit"} <= tools


def test_resume_only_after_a_session_exists(git_repo: Path):
    agent = session(git_repo)
    assert "--resume" not in agent.build_args("x", resume=True)
    agent.session_id = "abc-123"
    args = agent.build_args("x", resume=True)
    assert args[args.index("--resume") + 1] == "abc-123"


def test_send_parses_stream_json(git_repo: Path, fake_cli: FakeCLI):
    fake_cli.script(
        [
            {
                "session_id": "sess-A",
                "text": "I wrote the file.",
                "envelope": envelope(changed_files=["app.py"]),
                "write_files": {"app.py": "x = 1\n"},
                "tools": [{"name": "Write", "input": {"file_path": "app.py"}}],
                "cost_usd": 0.25,
                "input_tokens": 4321,
                "output_tokens": 99,
            }
        ]
    )
    events: list[tuple[str, dict]] = []
    agent = session(git_repo, on_event=lambda kind, payload: events.append((kind, payload)))

    turn = agent.send("build it")

    assert turn.ok
    assert turn.session_id == "sess-A"
    assert agent.session_id == "sess-A"
    assert "I wrote the file." in turn.text
    assert '"changed_files"' in turn.text
    assert turn.cost_usd == 0.25
    assert turn.input_tokens == 4321
    assert turn.output_tokens == 99
    assert turn.duration_ms == 1200
    assert (git_repo / "app.py").read_text() == "x = 1\n"
    assert [e.kind for e in turn.tool_events] == ["use", "result"]
    assert [k for k, _ in events] == ["tool_call", "tool_call"]
    assert events[0][1]["name"] == "Write"


def test_context_tokens_are_the_last_prompt_not_the_billing_sum(git_repo: Path, fake_cli: FakeCLI):
    fake_cli.script(
        [
            {
                "session_id": "sess-C",
                "envelope": envelope(changed_files=[]),
                "cache_read_tokens": 60_000,
                "input_tokens": 900,
            }
        ]
    )
    turn = session(git_repo).send("build it")

    assert turn.input_tokens == 60_030  # 10 + (20 + 60_000): what the run is billed for
    assert turn.context_tokens == 60_020  # the last message's prompt: what a context bar reads


def test_tool_results_carry_a_measured_duration(git_repo: Path, fake_cli: FakeCLI):
    fake_cli.script(
        [{"envelope": envelope(changed_files=[]), "tools": [{"name": "Read", "input": {}}]}]
    )
    payloads: list[dict] = []
    session(git_repo, on_event=lambda _kind, payload: payloads.append(payload)).send("go")

    use, result = payloads
    assert "duration_ms" not in use  # nothing to measure yet
    assert isinstance(result["duration_ms"], int)
    assert result["duration_ms"] >= 0


def test_second_send_resumes_the_same_session(git_repo: Path, fake_cli: FakeCLI):
    spec = {"session_id": "sess-B", "envelope": envelope(changed_files=[])}
    fake_cli.script([spec, dict(spec)])
    agent = session(git_repo)

    agent.send("first")
    agent.send("correction")

    calls = fake_cli.calls
    assert calls[0]["resume"] is None
    assert calls[1]["resume"] == "sess-B"
    assert agent.turns == 2


def test_a_non_zero_exit_is_reported_not_raised(git_repo: Path, fake_cli: FakeCLI):
    fake_cli.script([{"envelope": envelope(changed_files=[]), "exit_code": 2}])
    turn = session(git_repo).send("build it")
    assert not turn.ok
    assert turn.exit_code == 2
    assert "exited with code 2" in (turn.error or "")


def test_an_error_result_marks_the_turn_failed(git_repo: Path, fake_cli: FakeCLI):
    fake_cli.script([{"raw_reply": "context window exceeded", "is_error": True}])
    turn = session(git_repo).send("build it")
    assert not turn.ok
    assert "error result" in (turn.error or "")


def test_the_agent_runs_with_the_repo_as_cwd(git_repo: Path, fake_cli: FakeCLI):
    fake_cli.script([{"envelope": envelope(changed_files=[])}])
    session(git_repo).send("build it")
    assert Path(fake_cli.calls[0]["cwd"]).resolve() == git_repo.resolve()
