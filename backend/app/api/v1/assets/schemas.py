"""Asset API schemas — names and fields match the frozen API contract."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

GenericTwin = Literal["identical", "differs"]
DisabledBy = Literal["folder", "codex-config"]


class AssetKind(StrEnum):
    skill = "skill"
    agent = "agent"


class AssetSummary(BaseModel):
    id: str = Field(..., description='Stable slug, e.g. "claude:skill:frontend-dev".')
    kind: AssetKind
    provider: str = Field(
        ...,
        description='Owning store: "claude", "claude-plugin" (read-only), "codex" (skills '
        'and ~/.codex/agents/*.toml custom agents), "codex-plugin" (read-only), "generic" '
        '(the cross-agent ~/.agents/skills folder), or "masterwork" (the factory role store).',
    )
    agents: list[str] = Field(
        ...,
        description='Coding agents that load this asset ("claude", "codex"). A generic '
        'skill lists "codex" (it loads ~/.agents/skills itself) unless switched off in '
        "~/.codex/config.toml, plus each other agent whose skills dir links to it (Claude); "
        "a factory role lists none.",
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
    disabled: bool = Field(
        ...,
        description="True when no coding agent loads the skill: parked under its folder's "
        "`.disabled/`, or switched off in ~/.codex/config.toml. Still readable; `agents` "
        "is empty.",
    )
    disabled_by: DisabledBy | None = Field(
        None,
        description='Why `disabled` is true: "folder" (parked under `.disabled/`, which '
        'setAssetEnabled reverses) or "codex-config" (a `[[skills.config]]` entry, or a '
        "plugin not enabled, in ~/.codex/config.toml — masterwork never writes that file, "
        "so setAssetEnabled refuses). Null when enabled.",
    )
    generic_twin: GenericTwin | None = Field(
        None,
        description="Set on a Claude or Codex skill that is a real folder (not a link) while "
        '~/.agents/skills holds a same-named skill: "identical" when the two trees match '
        'byte for byte, "differs" otherwise. Null for everything else. For Codex it is a '
        "genuine duplicate (Codex loads both folders); for Claude the generic copy is "
        "shadowed. Migrating the agent copy merges the pair (an identical twin is adopted; "
        "a differing one needs `replace_generic`).",
    )


class AssetDetail(AssetSummary):
    content: str = Field(
        ...,
        description="Full file content: markdown with frontmatter, or TOML for a Codex "
        "custom agent.",
    )


class AssetUpdateRequest(BaseModel):
    content: str = Field(
        ...,
        description="Full new file content to write. A Codex custom agent must parse as "
        "TOML with string `name`, `description` and `developer_instructions` (else 400).",
    )


class AssetEnabledRequest(BaseModel):
    enabled: bool = Field(
        ...,
        description="False parks the skill under `.disabled/` so no agent loads it; true "
        "brings it back. A generic skill parks as one folder, so it goes off for every "
        "agent at once (Codex loads ~/.agents/skills itself, so no per-agent link can switch "
        "it off for Codex alone). Setting the state it already has is a no-op.",
    )


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
        ...,
        description="Agents whose skills dir holds a link to the generic copy (made now, or "
        "already there). Claude only for a fresh move: Codex loads ~/.agents/skills itself and "
        "gets no new link. Who loads the skill is `asset.agents`.",
    )
    skipped_agents: list[str] = Field(
        ...,
        description="Agents that already had an unrelated skill of this name; left alone. "
        "A skipped Codex loads both copies.",
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
        "the source just became a link to it (a Codex source is removed instead).",
    )
    replaced_generic: bool = Field(
        ..., description="A differing generic copy was replaced by this one on request."
    )
