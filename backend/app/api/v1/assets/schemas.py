"""Asset API schemas — names and fields match the frozen API contract."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class AssetKind(StrEnum):
    skill = "skill"
    agent = "agent"


class AssetSummary(BaseModel):
    id: str = Field(..., description='Stable slug, e.g. "claude:skill:frontend-dev".')
    kind: AssetKind
    provider: str = Field(
        ...,
        description='Owning store: "claude", "claude-plugin" (read-only), or '
        '"masterwork" (the factory role store).',
    )
    name: str = Field(..., description="Filename/dir-derived asset name.")
    title: str = Field(..., description="Frontmatter name/title, or the name as fallback.")
    description: str = Field(..., description='Frontmatter description, "" if none.')
    model: str | None = Field(
        None, description="Frontmatter model, null when the asset inherits the session model."
    )
    path: str = Field(..., description="Absolute path to the file on disk.")
    created_at: datetime | None = Field(
        None,
        description="Filesystem birth time, never later than updated_at. Null where the "
        "platform records none (Linux) — an absent date rather than a wrong one.",
    )
    updated_at: datetime = Field(..., description="File modification time.")
    read_only: bool = Field(
        ..., description="True for plugin-provided assets; PUT is rejected with 403."
    )


class AssetDetail(AssetSummary):
    content: str = Field(..., description="Full markdown, including frontmatter.")


class AssetUpdateRequest(BaseModel):
    content: str = Field(..., description="Full new file content to write.")


class AssetDiagram(BaseModel):
    asset_id: str
    mermaid: str = Field(..., description="Mermaid flowchart source.")
    generated_at: datetime
    stale: bool = Field(..., description="True when the file changed since generation.")
