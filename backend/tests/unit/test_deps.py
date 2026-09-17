"""The runner dependencies split work across two models per agent: authoring
endpoints get the authoring model, derivative ones the cheaper light model, and
the effective agent decides which CLI builds the runner.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.api import deps
from app.config import settings
from app.services.agent_cli import AgentId
from app.services.claude_runner import ClaudeRunner
from app.services.codex_runner import CodexRunner

_BINS = {AgentId.CLAUDE: "/opt/claude", AgentId.CODEX: "/Applications/Codex.app/codex"}


@pytest.fixture(autouse=True)
def _models(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "claude_model", "authoring")
    monkeypatch.setattr(settings, "claude_light_model", "cheap")
    monkeypatch.setattr(settings, "codex_model", "gpt-authoring")
    monkeypatch.setattr(settings, "codex_light_model", None)
    monkeypatch.setattr(settings, "claude_skills_root", tmp_path / "claude-home" / "skills")
    monkeypatch.setattr(settings, "masterwork_home", tmp_path / "masterwork-home")


def _args(runner: object) -> list[str]:
    assert isinstance(runner, ClaudeRunner | CodexRunner)
    return runner._build_args("p", resume_session_id=None, system_prompt=None)


async def test_chat_runner_uses_the_authoring_model() -> None:
    args = _args(await deps.get_authoring_runner(AgentId.CLAUDE, _BINS))
    assert args[0] == "/opt/claude"
    assert args[args.index("--model") + 1] == "authoring"


async def test_simulation_runner_uses_the_authoring_model_with_a_longer_timeout() -> None:
    runner = await deps.get_simulation_runner(AgentId.CLAUDE, _BINS)
    assert isinstance(runner, ClaudeRunner)
    assert _args(runner)[_args(runner).index("--model") + 1] == "authoring"
    assert runner._timeout == settings.simulation_timeout_seconds


async def test_light_runner_uses_the_cheap_model() -> None:
    args = _args(await deps.get_light_runner(AgentId.CLAUDE, _BINS))
    assert args[args.index("--model") + 1] == "cheap"


async def test_codex_authoring_runner_uses_codex_model_and_its_resolved_binary() -> None:
    runner = await deps.get_authoring_runner(AgentId.CODEX, _BINS)
    assert isinstance(runner, CodexRunner)
    args = _args(runner)
    assert args[:2] == ["/Applications/Codex.app/codex", "exec"]
    assert args[args.index("-m") + 1] == "gpt-authoring"
    assert not any("model_reasoning_effort" in a for a in args)


async def test_codex_light_runner_omits_the_model_and_lowers_the_effort() -> None:
    args = _args(await deps.get_light_runner(AgentId.CODEX, _BINS))
    assert "-m" not in args  # None = the user's ~/.codex/config.toml default
    assert 'model_reasoning_effort="low"' in args


def test_an_uninstalled_agent_keeps_its_configured_name() -> None:
    runner = deps.build_runner(
        AgentId.CODEX, {AgentId.CLAUDE: None, AgentId.CODEX: None}, light=False, timeout_seconds=5
    )
    assert _args(runner)[0] == settings.codex_bin


def test_both_runners_fall_back_to_masterwork_home_without_a_claude_home() -> None:
    for agent in AgentId:
        runner = deps.build_runner(agent, _BINS, light=False, timeout_seconds=5)
        assert isinstance(runner, ClaudeRunner | CodexRunner)
        assert runner._cwd == settings.masterwork_home


def test_both_runners_start_in_the_claude_home_when_it_exists() -> None:
    claude_home = settings.claude_skills_root.parent
    claude_home.mkdir(parents=True)
    for agent in AgentId:
        runner = deps.build_runner(agent, _BINS, light=True, timeout_seconds=5)
        assert isinstance(runner, ClaudeRunner | CodexRunner)
        assert runner._cwd == claude_home
