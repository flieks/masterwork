"""Global-instructions API schemas: each agent's own file (CLAUDE.md, AGENTS.md)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.services.agent_cli import AgentId


class InstructionsDoc(BaseModel):
    agent: AgentId = Field(..., description="Whose global instructions these are.")
    file_name: str = Field(..., description='"CLAUDE.md" for Claude Code, "AGENTS.md" for Codex.')
    path: str = Field(..., description="Absolute path to the agent's global instructions file.")
    content: str = Field(..., description='Full markdown, "" when the file does not exist yet.')
    exists: bool = Field(..., description="False when no file is on disk; a PUT creates it.")
    updated_at: datetime | None = Field(None, description="File mtime, null when absent.")
    shadowed_by: str | None = Field(
        None,
        description="Codex only: a non-empty AGENTS.override.md that Codex reads instead, so "
        "edits to this file have no effect. Null when nothing shadows it.",
    )
    same_file_as: str | None = Field(
        None,
        description="The other agent's instructions path when it resolves to this same file "
        "(e.g. a symlink), so an edit here changes both. Null otherwise.",
    )


class InstructionsUpdateRequest(BaseModel):
    content: str = Field(..., description="Full new file content to write.")
