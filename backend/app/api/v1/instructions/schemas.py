"""Global-instructions API schemas (`~/.claude/CLAUDE.md`)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class InstructionsDoc(BaseModel):
    path: str = Field(..., description="Absolute path to the global CLAUDE.md.")
    content: str = Field(..., description='Full markdown, "" when the file does not exist yet.')
    exists: bool = Field(..., description="False when no file is on disk; a PUT creates it.")
    updated_at: datetime | None = Field(None, description="File mtime, null when absent.")


class InstructionsUpdateRequest(BaseModel):
    content: str = Field(..., description="Full new file content to write.")
