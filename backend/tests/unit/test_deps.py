"""The runner dependencies split work across two models: authoring endpoints
get `claude_model`, derivative ones get the cheaper `claude_light_model`.
"""

from __future__ import annotations

import pytest

from app.api import deps
from app.config import settings
from app.services.claude_runner import ClaudeRunner


@pytest.fixture(autouse=True)
def _models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "claude_model", "authoring")
    monkeypatch.setattr(settings, "claude_light_model", "cheap")


def _model(runner: ClaudeRunner) -> str:
    args = runner._build_args("p", resume_session_id=None, system_prompt=None)
    return args[args.index("--model") + 1]


def test_chat_runner_uses_the_authoring_model() -> None:
    assert _model(deps.get_claude_runner()) == "authoring"


def test_simulation_runner_uses_the_authoring_model_with_a_longer_timeout() -> None:
    runner = deps.get_simulation_runner()
    assert _model(runner) == "authoring"
    assert runner._timeout == settings.simulation_timeout_seconds


def test_light_runner_uses_the_cheap_model() -> None:
    assert _model(deps.get_light_runner()) == "cheap"
