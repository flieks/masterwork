"""Application configuration — the only place that reads the environment."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

from dotenv import dotenv_values
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Kept outside the repo so these survive a re-clone (or an npx cache prune) and
# never land in git.
MASTERWORK_HOME = Path.home() / ".masterwork"
DEFAULT_DB_PATH = MASTERWORK_HOME / "masterwork.db"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "masterwork"
    version: str = "0.1.0"

    # SQLite by default so a fresh install needs no database server. Point
    # DATABASE_URL at Postgres (postgresql+asyncpg://…) to use that instead —
    # both dialects are supported and migrated by the same revisions.
    database_url: str = f"sqlite+aiosqlite:///{DEFAULT_DB_PATH}"
    # NoDecode: keep the raw env string so the validator can split on commas
    # (otherwise pydantic-settings would try to JSON-decode it and fail).
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5192"]

    # Claude Code CLI used by the chat runner.
    claude_bin: str = "claude"
    # Authoring work — chat, simulations, audits. These write the skills and
    # subagents, so quality matters more than cost.
    claude_model: str = "fable"
    # Derivative work — summaries, diagrams. Reads what already exists.
    claude_light_model: str = "opus"
    claude_timeout_seconds: int = 300
    # Simulations read every linked asset before answering — allow more time.
    simulation_timeout_seconds: int = 900

    # Provider roots. Default to the real ~/.claude locations; tests override these
    # to point at a temporary tree instead of the user's real assets.
    claude_skills_root: Path = Path.home() / ".claude" / "skills"
    claude_agents_root: Path = Path.home() / ".claude" / "agents"
    claude_plugins_root: Path = Path.home() / ".claude" / "plugins"
    # Claude Code's own settings file — where the observability hooks are written.
    claude_settings_file: Path = Path.home() / ".claude" / "settings.json"
    # Everything masterwork installs on disk (database, forwarder scripts).
    masterwork_home: Path = MASTERWORK_HOME
    # Vendor-neutral role store the factory pipeline reads its stage prompts from
    # (<role>/system.md + user.md + role.json). Seeded by the factory on first
    # run, so it may not exist yet.
    masterwork_agents_root: Path = MASTERWORK_HOME / "agents"
    # Images a hook pulled out of a tool response before it was truncated, one
    # directory per session. Written by the forwarder, served back by the events
    # API — the only bytes in this app that live outside the database.
    masterwork_media_root: Path = MASTERWORK_HOME / "media"
    # The port uvicorn was actually started on, so the hook command a connected
    # agent runs posts to this backend and not to a stale default. The launcher
    # passes it through; running uvicorn by hand on another port needs it set.
    api_port: int = Field(8008, validation_alias="MASTERWORK_API_PORT")
    # Global instructions file — not an asset (it sits outside the provider
    # roots, so chat proposals can never write it), edited through its own
    # endpoint.
    claude_instructions_file: Path = Path.home() / ".claude" / "CLAUDE.md"
    # Repo root for the factory pipeline runner (factory/run.py), derived from
    # this file's own location rather than an env var.
    masterwork_repo_root: Path = Path(__file__).resolve().parents[2]
    # Interpreter the launcher spawns factory/run.py with.
    factory_python: str = "python3"
    # Default projects_root before any app_settings row overrides it.
    default_projects_root: Path = Path.home() / "Projects"
    # Mirrors the factory's own runs-root default (factory/adw/config.py
    # DEFAULT_RUNS_ROOT) so an interview run's questions/answers files land
    # exactly where every other run of that repo lands.
    factory_runs_root: Path = MASTERWORK_HOME / "runs"

    @property
    def ingest_url(self) -> str:
        """Where a connected agent's hooks post their events."""
        return f"http://localhost:{self.api_port}/api/v1/hooks/events"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors(cls, value: object) -> object:
        """Accept a comma-separated string from the environment."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


settings = Settings()


def read_secret(name: str) -> str | None:
    """The named env var's value, or None. `secret_ref` (e.g. a work source's
    PAT variable name) is dynamic and so can't be a Settings field — this
    keeps config.py the only module that touches the environment regardless.

    Falls back to backend/.env because the launchd-run backend never sees
    shell env. Anchored to this file, not cwd — launchd's WorkingDirectory
    is the repo root.
    """
    value = os.environ.get(name)
    if not value:
        value = dotenv_values(_ENV_PATH).get(name)
    return value or None


_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
