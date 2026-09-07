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
        description='Owning store: "claude", "claude-plugin" (read-only), "codex", '
        '"generic" (the cross-agent ~/.agents/skills folder), or "masterwork" '
        "(the factory role store).",
    )
    agents: list[str] = Field(
        ...,
        description='Coding agents that load this asset ("claude", "codex"). A generic '
        "skill lists every agent whose skills dir links to it; a factory role lists none.",
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


class AssetMigrateRequest(BaseModel):
    replace_generic: bool = Field(
        False,
        description="Throw away a differing copy already in the generic folder. Never "
        "needed when that copy is identical — it is adopted as is.",
    )


class AssetMigrationResult(BaseModel):
    asset: AssetDetail = Field(..., description="The skill at its new generic id.")
    previous_id: str = Field(..., description="The id the skill had before the move.")
    linked_agents: list[str] = Field(
        ..., description="Agents whose skills dir now links to the generic copy."
    )
    skipped_agents: list[str] = Field(
        ...,
        description="Agents that already had an unrelated skill of this name; left alone.",
    )
    claude_only_keys: list[str] = Field(
        ...,
        description="Frontmatter keys kept that only Claude Code honours; other agents "
        "ignore them.",
    )
    name_rewritten: bool = Field(
        ..., description="True when `name:` was added or changed to match the folder."
    )
    relinked_projects: int = Field(
        ..., description="Project links that were re-pointed from the old id to the new one."
    )
    adopted: bool = Field(
        ...,
        description="The generic folder already held an identical copy: nothing was copied, "
        "the source just became a link to it.",
    )
    replaced_generic: bool = Field(
        ..., description="A differing generic copy was replaced by this one on request."
    )
