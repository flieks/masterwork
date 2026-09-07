"""Shared FastAPI dependencies.

All are overridable in tests: `get_db` points at the test database,
`get_providers` points at a temp asset tree, and the three runner
dependencies are replaced with fakes so endpoints never shell out to the
real CLI.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.db.models.work import WorkSource
from app.db.session import AsyncSessionLocal, get_db
from app.observability.base import Integration
from app.observability.registry import build_integrations
from app.providers.azuredevops import AzureDevOpsClient
from app.providers.base import Provider
from app.providers.registry import build_providers
from app.services import factory_launcher
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
    "get_devops_client_factory",
    "DevOpsClientFactory",
    "get_skill_catalog_transport",
    "get_launch_spawner",
    "LaunchSpawner",
    "get_resume_spawner",
    "ResumeSpawner",
]

# Short alias — the full Callable[[WorkSource], AzureDevOpsClient] spelling is
# repeated at every call site that injects this dependency.
DevOpsClientFactory = Callable[[WorkSource], AzureDevOpsClient]


class LaunchSpawner(Protocol):
    """Spawns a fresh factory run; -> pid. Keyword-only so `run_id`/`interview`
    can be added without breaking every call site's positional order. Tests
    override `get_launch_spawner` with a fake so no test ever forks a
    real subprocess."""

    def __call__(
        self,
        *,
        project_path: Path,
        request_text: str,
        log_path: Path,
        run_id: str | None = None,
        interview: bool = False,
        workflow: str | None = None,
    ) -> int: ...


class ResumeSpawner(Protocol):
    """Spawns the detached `--resume` subprocess once answers.json is written;
    -> pid. Tests override `get_resume_spawner` the same way."""

    def __call__(self, *, project_path: Path, run_id: str, log_path: Path) -> int: ...


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


def get_devops_client_factory() -> DevOpsClientFactory:
    """Builds a client for one work source; tests override this to inject a
    MockTransport so no test ever reaches the network."""

    def _factory(source: WorkSource) -> AzureDevOpsClient:
        return AzureDevOpsClient(
            org_url=source.org_url,
            project=source.project,
            secret_ref=source.secret_ref,
            team=source.team,
        )

    return _factory


def get_skill_catalog_transport() -> httpx.AsyncBaseTransport | None:
    """None means the real network; tests override this with a MockTransport
    so no test ever reaches skills.sh or GitHub."""
    return None


def get_launch_spawner() -> LaunchSpawner:
    """Binds the repo root and interpreter from settings; tests override this
    with a fake that records its args and returns a fake pid."""

    def _spawn(
        *,
        project_path: Path,
        request_text: str,
        log_path: Path,
        run_id: str | None = None,
        interview: bool = False,
        workflow: str | None = None,
    ) -> int:
        return factory_launcher.spawn_factory_run(
            repo_root=settings.masterwork_repo_root,
            python_bin=settings.factory_python,
            project_path=project_path,
            request_text=request_text,
            log_path=log_path,
            run_id=run_id,
            interview=interview,
            workflow=workflow,
        )

    return _spawn


def get_resume_spawner() -> ResumeSpawner:
    """Same binding as get_launch_spawner, for the detached `--resume` spawn."""

    def _spawn(*, project_path: Path, run_id: str, log_path: Path) -> int:
        return factory_launcher.spawn_factory_resume(
            repo_root=settings.masterwork_repo_root,
            python_bin=settings.factory_python,
            project_path=project_path,
            run_id=run_id,
            log_path=log_path,
        )

    return _spawn
