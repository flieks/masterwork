"""Shared FastAPI dependencies.

All are overridable in tests: `get_db` points at the test database,
`get_providers` points at a temp asset tree, and the three runner
dependencies are replaced with fakes so endpoints never shell out to the
real CLI.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.db.session import AsyncSessionLocal, get_db
from app.observability.base import Integration
from app.observability.registry import build_integrations
from app.providers.base import Provider
from app.providers.registry import build_providers
from app.services.claude_runner import ClaudeRunner

__all__ = [
    "get_db",
    "get_integrations",
    "get_providers",
    "get_instructions_path",
    "get_claude_runner",
    "get_light_runner",
    "get_simulation_runner",
    "get_session_factory",
]


def get_providers() -> list[Provider]:
    return build_providers(settings)


def get_integrations() -> list[Integration]:
    """Agents that can be wired to report their sessions; tests point these at a
    temp config file so no test ever edits the real ~/.claude/settings.json."""
    return build_integrations(settings)


def get_instructions_path() -> Path:
    """The global CLAUDE.md; tests point it at a temp file."""
    return settings.claude_instructions_file


def get_claude_runner() -> ClaudeRunner:
    """Authoring model: chat proposes the edits that shape skills and subagents."""
    return ClaudeRunner(
        bin=settings.claude_bin,
        model=settings.claude_model,
        timeout_seconds=settings.claude_timeout_seconds,
    )


def get_light_runner() -> ClaudeRunner:
    """Cheaper model for derivative output (summaries, diagrams) that only
    restates an asset rather than deciding how to improve it."""
    return ClaudeRunner(
        bin=settings.claude_bin,
        model=settings.claude_light_model,
        timeout_seconds=settings.claude_timeout_seconds,
    )


def get_simulation_runner() -> ClaudeRunner:
    """Authoring model, longer leash: a simulation reads many files first."""
    return ClaudeRunner(
        bin=settings.claude_bin,
        model=settings.claude_model,
        timeout_seconds=settings.simulation_timeout_seconds,
    )


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Session factory for background tasks that outlive the request session."""
    return AsyncSessionLocal
