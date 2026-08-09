"""Application configuration — the only place that reads the environment."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

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
    # The port uvicorn was actually started on, so the hook command a connected
    # agent runs posts to this backend and not to a stale default. The launcher
    # passes it through; running uvicorn by hand on another port needs it set.
    api_port: int = Field(8008, validation_alias="MASTERWORK_API_PORT")
    # Global instructions file — not an asset (it sits outside the provider
    # roots, so chat proposals can never write it), edited through its own
    # endpoint.
    claude_instructions_file: Path = Path.home() / ".claude" / "CLAUDE.md"

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
