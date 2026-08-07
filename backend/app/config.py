"""Application configuration — the only place that reads the environment."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Kept outside the repo so the database survives a re-clone and never lands in git.
DEFAULT_DB_PATH = Path.home() / ".masterwork" / "masterwork.db"


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
    # Global instructions file — not an asset (it sits outside the provider
    # roots, so chat proposals can never write it), edited through its own
    # endpoint.
    claude_instructions_file: Path = Path.home() / ".claude" / "CLAUDE.md"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors(cls, value: object) -> object:
        """Accept a comma-separated string from the environment."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


settings = Settings()
