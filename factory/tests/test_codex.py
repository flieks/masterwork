"""The Codex backend: `codex exec --json` argv, JSONL normalising, config, record and resume."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
import run as cli
from adw import codex, runs
from adw.agent import RUN_ID_ENV, STAGE_ENV, AgentError
from adw.codex import RUNNER_NOTE, CodexSession, resolve_codex_bin, toml_string
from adw.config import ConfigError, load_config
from adw.pipeline import format_summary
from adw.telemetry import Telemetry, context_window_for
from conftest import FakeCLI, PostSpy, envelope, git
from test_pipeline import BUILD_OK, COLLECTOR, PASSING_CHECK, REVIEW_OK, events, run

RUN_ID = "codexrun"


def session(repo: Path, stage: str = "build", **kwargs) -> CodexSession:
    cfg = load_config(repo, agent="codex", workflow="scout" if stage == "scout" else None)
    resolved = cfg.stages[stage]
    options = {
        "stage": stage,
        "model": resolved.model,
        "cwd": repo,
        "read_only": resolved.read_only,
        "timeout_seconds": 60,
        "run_id": "c0dec0de",
    }
    options.update(kwargs)
    return CodexSession(**options)


def config_value(args: list[str], key: str) -> str | None:
    """The `-c key=value` value, TOML-decoded exactly as Codex would read it."""
    for flag, value in zip(args, args[1:], strict=False):
        if flag == "-c" and value.startswith(f"{key}="):
            return tomllib.loads(f"v = {value.split('=', 1)[1]}")["v"]
    return None


# --- argv -------------------------------------------------------------------


def test_a_first_turn_is_codex_exec_in_the_repo_with_the_write_sandbox(git_repo: Path):
    args = session(git_repo).build_args("do the thing", resume=False)

    assert args[:4] == ["codex", "exec", "--json", "--skip-git-repo-check"]
    assert args[args.index("-C") + 1] == str(git_repo)
    assert args[args.index("-s") + 1] == "workspace-write"
    assert args[-2:] == ["--", "do the thing"]  # a prompt starting with "-" stays a prompt
    assert "resume" not in args
    assert "-m" not in args  # no configured model: ~/.codex/config.toml decides
    disabled = {args[i + 1] for i, a in enumerate(args) if a == "--disable"}
    assert {"plugins", "apps", "multi_agent", "browser_use", "computer_use"} <= disabled
    assert config_value(args, "web_search") == "disabled"
    for rejected in ("--full-auto", "-a", "--search"):  # `codex exec` refuses these
        assert rejected not in args


@pytest.mark.parametrize(
    ("stage", "sandbox"),
    [("review", "read-only"), ("build", "workspace-write"), ("plan", "workspace-write")],
)
def test_the_sandbox_follows_the_write_boundary_not_tool_names(
    git_repo: Path, stage: str, sandbox: str
):
    args = session(git_repo, stage).build_args("x", resume=False)
    assert args[args.index("-s") + 1] == sandbox
    assert "--disallowedTools" not in args


def test_the_developer_instructions_are_re_sent_on_every_turn(git_repo: Path):
    agent = session(git_repo, system_prompt='You are the BUILD stage.\nSay "hi" — `now`.\x7f')

    first = config_value(agent.build_args("x", resume=False), "developer_instructions")
    agent.session_id = "thread-1"
    again = config_value(agent.build_args("x", resume=True), "developer_instructions")

    assert first == again
    assert first is not None and first.startswith('You are the BUILD stage.\nSay "hi" — `now`.\x7f')
    assert RUNNER_NOTE in first


def test_a_resume_names_the_thread_and_repeats_no_sandbox_or_cwd(git_repo: Path):
    agent = session(git_repo, model="gpt-5.6-luna", reasoning_effort="low")
    agent.session_id = "thread-9"
    args = agent.build_args("fix it", resume=True)

    assert args[:3] == ["codex", "exec", "resume"]
    assert args[3] == "thread-9"
    assert "-s" not in args and "-C" not in args
    assert args[args.index("-m") + 1] == "gpt-5.6-luna"
    assert config_value(args, "model_reasoning_effort") == "low"
    assert args[-2:] == ["--", "fix it"]


def test_no_resume_before_a_thread_exists(git_repo: Path):
    args = session(git_repo).build_args("x", resume=True)
    assert "resume" not in args and "-s" in args


@pytest.mark.parametrize(
    "text", ["plain", 'quo"te \\ back', "multi\nline\ttab", "é — → ✓", "del\x7f"]
)
def test_config_values_survive_toml_parsing(text: str):
    assert tomllib.loads(f"v = {toml_string(text)}")["v"] == text


# --- the JSONL stream ---------------------------------------------------------


def test_send_normalises_the_jsonl_stream(git_repo: Path, fake_codex: FakeCLI):
    fake_codex.script(
        [
            {
                "thread_id": "thread-A",
                "messages": ["I'll read the repo first."],
                "text": "I wrote the file.",
                "envelope": envelope(changed_files=["app.py"]),
                "commands": [{"command": "ls"}],
                "write_files": {"app.py": "x = 1\n"},
                "input_tokens": 4321,
                "output_tokens": 99,
                "noise": ["not json at all", "[1, 2]"],
                "errors": ["Reconnecting... 1/5"],
            }
        ]
    )
    emitted: list[tuple[str, dict]] = []
    agent = session(git_repo, on_event=lambda kind, payload: emitted.append((kind, payload)))

    turn = agent.send("build it")

    assert turn.ok, turn.error  # a recovered `error` event does not fail the turn
    assert turn.session_id == agent.session_id == "thread-A"
    assert turn.text.startswith("I wrote the file.")  # the last message, not the first
    assert '"changed_files"' in turn.text
    assert turn.cost_usd is None  # never a silent $0
    assert (turn.input_tokens, turn.output_tokens) == (4321, 99)
    assert turn.num_turns == 1
    assert (git_repo / "app.py").read_text() == "x = 1\n"
    assert [(e.kind, e.name) for e in turn.tool_events] == [
        ("use", "Bash"),
        ("result", "tool_result"),
        ("use", "Edit"),
        ("result", "tool_result"),
    ]
    uses = [payload for kind, payload in emitted if payload["kind"] == "use"]
    assert json.loads(uses[0]["input"]) == {"command": "/bin/zsh -lc 'ls'"}
    assert json.loads(uses[1]["input"]) == {"file_path": "app.py"}  # repo-relative
    assert {kind for kind, _ in emitted} == {"tool_call"}
    assert all("duration_ms" in p for _, p in emitted if p["kind"] == "result")


def test_the_child_gets_devnull_stdin_the_repo_cwd_and_the_run_identity(
    git_repo: Path, fake_codex: FakeCLI
):
    fake_codex.script([{"envelope": envelope()}])
    session(git_repo).send("go")

    call = fake_codex.calls[0]
    assert call["stdin_devnull"]  # an open stdin makes a real `codex exec` hang
    assert Path(call["cwd"]).resolve() == git_repo.resolve()
    assert call["env"][RUN_ID_ENV] == "c0dec0de"
    assert call["env"][STAGE_ENV] == "build"


def test_the_second_send_resumes_the_thread(git_repo: Path, fake_codex: FakeCLI):
    fake_codex.script([{"thread_id": "thread-B", "envelope": envelope()}, {"envelope": envelope()}])
    agent = session(git_repo, system_prompt="IDENTITY")

    agent.send("first")
    agent.send("correction")

    first, second = fake_codex.calls
    assert first["resume"] is None and "-s" in first["argv"]
    assert second["resume"] == "thread-B"
    assert "-s" not in second["argv"] and "-C" not in second["argv"]
    assert second["prompt"] == "correction"
    for call in (first, second):
        assert any(
            c.startswith("developer_instructions=") and "IDENTITY" in c for c in call["configs"]
        )
    assert agent.turns == 2


def test_a_failed_turn_is_reported_with_the_api_sentence(git_repo: Path, fake_codex: FakeCLI):
    fake_codex.script([{"fail": "The 'nope' model is not supported."}])
    turn = session(git_repo).send("go")

    assert not turn.ok
    assert turn.exit_code == 1
    assert turn.error == "codex turn failed: The 'nope' model is not supported."


def test_a_non_zero_exit_without_turn_failed_says_what_codex_said(
    git_repo: Path, fake_codex: FakeCLI
):
    fake_codex.script([{"envelope": envelope(), "errors": ["stream disconnected"], "exit_code": 2}])
    turn = session(git_repo).send("go")

    assert not turn.ok
    assert turn.error == "codex exited with code 2: stream disconnected"


def test_a_failed_command_is_a_tool_error(git_repo: Path, fake_codex: FakeCLI):
    fake_codex.script(
        [{"envelope": envelope(), "commands": [{"command": "false", "exit_code": 1}]}]
    )
    turn = session(git_repo).send("go")

    assert turn.ok
    assert turn.tool_events[1].detail == {"is_error": True}


# --- finding the binary -------------------------------------------------------


def test_the_binary_on_path_wins(fake_codex: FakeCLI):
    assert resolve_codex_bin(None) == str(fake_codex.bin_dir / "codex")


def test_an_app_bundled_binary_is_found_when_path_has_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    bundled = tmp_path / "ChatGPT.app" / "codex"
    bundled.parent.mkdir()
    bundled.write_text("#!/bin/sh\n")
    bundled.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(codex, "CODEX_FALLBACKS", (tmp_path / "missing" / "codex", bundled))

    assert resolve_codex_bin("codex") == str(bundled)


def test_no_binary_anywhere_is_a_clear_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setattr(codex, "CODEX_FALLBACKS", (tmp_path / "nowhere" / "codex",))

    with pytest.raises(AgentError, match="could not find the Codex CLI") as caught:
        resolve_codex_bin(None)
    assert str(tmp_path / "nowhere" / "codex") in str(caught.value)
    assert '"codex_bin"' in str(caught.value)
    with pytest.raises(AgentError, match="not an executable file"):
        resolve_codex_bin(str(tmp_path / "nope" / "codex"))


# --- configuration ------------------------------------------------------------


def write_config(repo: Path, data: dict) -> None:
    (repo / "factory.config.json").write_text(json.dumps(data), encoding="utf-8")


def test_the_agent_defaults_to_claude_and_comes_from_the_file_or_the_flag(tmp_path: Path):
    assert load_config(tmp_path).agent == "claude"
    write_config(tmp_path, {"agent": "codex"})
    assert load_config(tmp_path).agent == "codex"
    assert load_config(tmp_path, agent="claude").agent == "claude"  # the flag wins


def test_an_unknown_agent_is_refused(tmp_path: Path):
    write_config(tmp_path, {"agent": "gemini"})
    with pytest.raises(ConfigError, match="must be one of claude, codex"):
        load_config(tmp_path)


def test_codex_stages_take_codex_models_never_claude_aliases(tmp_path: Path):
    write_config(
        tmp_path,
        {
            "models": {"build": "opus"},
            "codex_models": {"build": "gpt-5.6-luna", "nonsense": "x"},
            "codex_reasoning_effort": {"review": "high"},
        },
    )
    cfg = load_config(tmp_path, agent="codex")

    assert cfg.stages["build"].model == "gpt-5.6-luna"
    assert cfg.stages["plan"].model is None  # role.json's "opus" is a Claude name
    assert cfg.stages["review"].reasoning_effort == "high"
    assert cfg.stages["build"].reasoning_effort is None
    assert all(not s.disallowed_tools for s in cfg.stages.values())
    assert any('unknown stage "nonsense" in "codex_models"' in w for w in cfg.warnings)
    # The same file still drives a claude run exactly as before.
    assert load_config(tmp_path).stages["build"].model == "opus"
    assert (
        load_config(tmp_path, agent="codex", model_override="gpt-6-astra").stages["plan"].model
        == "gpt-6-astra"
    )


def test_one_reasoning_effort_applies_to_every_stage(tmp_path: Path):
    write_config(tmp_path, {"codex_reasoning_effort": "low"})
    cfg = load_config(tmp_path, agent="codex")
    assert {s.reasoning_effort for s in cfg.stages.values() if s.name != "checks"} == {"low"}


@pytest.mark.parametrize("value", [3, "", {"build": 1}, ["low"]])
def test_a_malformed_reasoning_effort_is_refused(tmp_path: Path, value: object):
    write_config(tmp_path, {"codex_reasoning_effort": value})
    with pytest.raises(ConfigError, match="codex_reasoning_effort"):
        load_config(tmp_path, agent="codex")


def test_codex_has_no_assumed_context_window(tmp_path: Path):
    assert load_config(tmp_path).context_window == 200_000
    assert load_config(tmp_path, agent="codex").context_window is None
    write_config(tmp_path, {"context_window": 400_000})
    assert load_config(tmp_path, agent="codex").context_window == 400_000
    assert context_window_for("gpt-5.6-sol", default=None) is None
    assert context_window_for("opus", default=None) == 200_000


def test_a_cost_cap_on_codex_is_warned_about_not_silently_trusted(tmp_path: Path):
    cfg = load_config(tmp_path, agent="codex", max_cost_usd=5.0)
    assert any("cannot be enforced on codex" in w and "uncapped" in w for w in cfg.warnings)
    capped = load_config(tmp_path, agent="codex", max_cost_usd=5.0, max_tokens=1000)
    assert any("only max_tokens caps this run" in w for w in capped.warnings)
    assert not any("codex" in w for w in load_config(tmp_path, max_cost_usd=5.0).warnings)


# --- the CLI ------------------------------------------------------------------


def test_the_flag_only_takes_known_agents(git_repo: Path, capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["--repo", str(git_repo), "--agent", "gpt", "--dry-run", "x"])
    assert caught.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_the_dry_run_shows_the_agent_its_binary_and_each_sandbox(
    git_repo: Path, capsys, fake_codex: FakeCLI
):
    code = cli.main(["--repo", str(git_repo), "--agent", "codex", "--no-checks", "--dry-run", "x"])
    out = capsys.readouterr().out

    assert code == 0
    assert f"agent:    codex ({fake_codex.bin_dir / 'codex'})" in out
    assert "SANDBOX" in out and "DISALLOWED TOOLS" not in out
    table = out.split("\n\n")[1].splitlines()  # the stage table, not the roles section
    rows = {line.split()[0]: line for line in table}
    assert "read-only" in rows["review"] and "workspace-write" in rows["build"]
    assert "(codex default)" in rows["plan"]
    assert fake_codex.calls == []


def test_a_missing_codex_binary_refuses_before_any_branch_exists(
    git_repo: Path, capsys, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(codex, "CODEX_FALLBACKS", ())
    monkeypatch.setattr(codex.shutil, "which", lambda _name: None)
    code = cli.main(
        [
            "--repo",
            str(git_repo),
            "--runs-dir",
            str(tmp_path / "runs"),
            "--agent",
            "codex",
            "--no-checks",
            "--workflow",
            "build_test",
            "x",
        ]
    )

    assert code == 2
    assert "could not find the Codex CLI" in capsys.readouterr().err
    assert "factory/" not in git(git_repo, "branch")


# --- a whole run --------------------------------------------------------------


def test_a_codex_run_is_recorded_reported_and_filed_as_codex(
    git_repo: Path, fake_codex: FakeCLI, fake_cli: FakeCLI, post_spy: PostSpy
):
    result, telemetry = run(
        git_repo,
        fake_codex,
        [BUILD_OK, REVIEW_OK],
        agent="codex",
        workflow=["build", "checks", "review"],
        telemetry_url=COLLECTOR,
    )

    assert result.accepted, result.reason
    assert fake_cli.calls == []  # claude was never launched
    assert [c["argv"][c["argv"].index("-s") + 1] for c in fake_codex.calls] == [
        "workspace-write",
        "read-only",
    ]
    record = runs.read(telemetry.run_dir)
    assert record is not None and record.agent == "codex"

    assert {body["source"] for body in post_spy.bodies} == {"codex"}
    turns = [e for e in events(telemetry) if e["event"] == "agent_turn"]
    assert all(t["payload"]["cost_reported"] is False and t["cost_usd"] == 0 for t in turns)
    end = post_spy.of("run_end")[0]
    assert end["stats"]["agent"] == "codex"
    assert end["stats"]["cost_usd"] is None and end["stats"]["cost_known"] is False
    assert end["stats"]["stages"]["build"]["cost_usd"] is None
    assert all("context_window" not in body.get("agent", {}) for body in post_spy.bodies)
    assert "cost not reported by codex" in format_summary(result)
    assert "$0.0000" not in format_summary(result)


def test_a_claude_run_still_files_as_claude_code(
    git_repo: Path, fake_cli: FakeCLI, post_spy: PostSpy
):
    result, telemetry = run(
        git_repo, fake_cli, [BUILD_OK], workflow="build_test", telemetry_url=COLLECTOR
    )
    assert result.accepted and result.cost_known
    assert {body["source"] for body in post_spy.bodies} == {"claude-code"}
    assert runs.read(telemetry.run_dir).agent == "claude"


def test_an_unpriced_codex_run_is_still_stopped_by_its_token_cap(
    git_repo: Path, fake_codex: FakeCLI
):
    result, telemetry = run(
        git_repo,
        fake_codex,
        [BUILD_OK],
        agent="codex",
        workflow="build_test",
        max_cost_usd=0.01,
        max_tokens=500,
    )

    assert result.budget_stop == "token cap reached: 1,200 of 500 token budget"
    warned = [e for e in events(telemetry) if "cannot be enforced on codex" in e["detail"]]
    assert warned and warned[0]["result"] == "warn"


def test_an_unpriced_codex_run_is_never_stopped_by_a_cost_cap(git_repo: Path, fake_codex: FakeCLI):
    result, _ = run(
        git_repo, fake_codex, [BUILD_OK], agent="codex", workflow="build_test", max_cost_usd=0.01
    )
    assert result.accepted, result.reason
    assert result.budget_stop == ""


# --- resume keeps the agent ---------------------------------------------------


def cli_run(repo: Path, tmp_path: Path, *args: str) -> int:
    config = tmp_path / "outside.config.json"
    config.write_text(
        json.dumps({"checks": [PASSING_CHECK], "telemetry_url": None}), encoding="utf-8"
    )
    base = ["--repo", str(repo), "--runs-dir", str(tmp_path / "runs"), "--config", str(config)]
    return cli.main([*base, "--quiet", *args])


@pytest.fixture
def no_stop_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real run installs a SIGTERM handler that exits the process: not in pytest."""
    monkeypatch.setattr(cli, "install_stop_handler", lambda _run_dir: None)


def stopped_codex_run(repo: Path, tmp_path: Path, fake_codex: FakeCLI) -> None:
    fake_codex.script([BUILD_OK], default={"envelope": envelope(changed_files=[])})
    code = cli_run(
        repo,
        tmp_path,
        "--agent",
        "codex",
        "--workflow",
        "build_test",
        "--run-id",
        RUN_ID,
        "--max-tokens",
        "500",
        "Add a /health endpoint",
    )
    assert code == 1
    record = runs.read(tmp_path / "runs" / RUN_ID)
    assert record is not None and record.state == runs.STOPPED and record.agent == "codex"


def test_resume_runs_on_the_recorded_agent_without_being_told(
    git_repo: Path, tmp_path: Path, fake_codex: FakeCLI, fake_cli: FakeCLI, no_stop_handler, capsys
):
    stopped_codex_run(git_repo, tmp_path, fake_codex)
    fake_codex.script([BUILD_OK])
    fake_codex.state_path.unlink()

    assert cli_run(git_repo, tmp_path, "--resume", RUN_ID, "--max-tokens", "1000000") == 0
    assert fake_cli.calls == []  # the config's default agent (claude) was never used
    assert len(fake_codex.calls) == 2
    record = runs.read(tmp_path / "runs" / RUN_ID)
    assert record.agent == "codex" and record.attempt == 2 and record.accepted

    capsys.readouterr()
    cli.main(["--repo", str(git_repo), "--runs-dir", str(tmp_path / "runs"), "--list-runs"])
    row = next(line.split() for line in capsys.readouterr().out.splitlines() if RUN_ID in line)
    assert row[:5] == [RUN_ID, "finished", "-", "2", "codex"]


def test_resume_refuses_an_agent_that_contradicts_the_record(
    git_repo: Path, tmp_path: Path, fake_codex: FakeCLI, no_stop_handler, capsys
):
    stopped_codex_run(git_repo, tmp_path, fake_codex)
    capsys.readouterr()
    calls_before = len(fake_codex.calls)

    assert cli_run(git_repo, tmp_path, "--resume", RUN_ID, "--agent", "claude") == 2
    err = capsys.readouterr().err
    assert "on the agent it recorded (codex)" in err and "--agent claude cannot switch it" in err
    assert len(fake_codex.calls) == calls_before
    # Naming the recorded agent again is not a contradiction.
    assert (
        cli_run(
            git_repo, tmp_path, "--resume", RUN_ID, "--agent", "codex", "--max-tokens", "1000000"
        )
        == 0
    )


def test_a_run_record_from_before_agents_reads_as_claude(tmp_path: Path):
    run_dir = tmp_path / "old"
    run_dir.mkdir()
    (run_dir / runs.RECORD_FILENAME).write_text(json.dumps({"run_id": "old", "repo": "/x"}))
    assert runs.read(run_dir).agent == "claude"


def test_a_telemetry_with_no_known_window_draws_no_percentage(tmp_path: Path):
    tel = Telemetry(run_id="r", repo=tmp_path, run_dir=tmp_path / "runs" / "r", context_window=None)
    assert tel.note_input_tokens(50_000) == 0.0
    tel.close()
