"""Skill catalog API schemas."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class SkillRegistry(StrEnum):
    skills_sh = "skills_sh"
    github = "github"


class CatalogSkill(BaseModel):
    owner: str = Field(..., description="GitHub owner/org that publishes the source repo.")
    repo: str = Field(..., description="GitHub repo name.")
    skill: str = Field(
        ..., description="Skill slug within the repo — a GitHub hit uses the repo name."
    )
    name: str = Field(..., description="Display name.")
    description: str = Field(..., description='"" when the registry supplied none.')
    registry: SkillRegistry = Field(..., description="Which source this record came from.")
    installs: int | None = Field(None, description="Install count; only skills.sh reports one.")
    license: str | None = Field(None, description="SPDX id when known at search time, else null.")
    license_resolved: bool = Field(
        ...,
        description='Unresolved (skills.sh) — preview to resolve; null is not "unlicensed".',
    )
    url: str = Field(..., description="Link to the source repository on GitHub.")
    installed: bool = Field(
        ..., description="A skill directory with this slug already exists on disk."
    )


class CatalogSourceError(BaseModel):
    registry: SkillRegistry
    message: str = Field(..., description="Why this source's results are missing.")


class CatalogSearchResponse(BaseModel):
    skills: list[CatalogSkill]
    errors: list[CatalogSourceError] = Field(
        ..., description="Per-source failures; a partial result still returns 200."
    )


class CatalogSkillDetail(BaseModel):
    owner: str
    repo: str
    skill: str
    name: str
    registry: SkillRegistry
    license: str | None = Field(None, description="SPDX id, or null when all rights reserved.")
    all_rights_reserved: bool = Field(
        ...,
        description="True when license is null — explicit, so the UI never reads null as unknown.",
    )
    url: str = Field(..., description="Link to the skill's folder on GitHub.")
    version: str | None = Field(
        None, description="Version from the registry copy's frontmatter; most skills declare none."
    )
    installed_version: str | None = Field(
        None, description="Version from the copy on disk, when it declares one."
    )
    created_at: datetime | None = Field(
        None, description="First commit that touched the skill folder; null if unavailable."
    )
    last_modified_at: datetime | None = Field(
        None, description="Most recent commit that touched the skill folder."
    )
    last_change_summary: str | None = Field(
        None, description="Subject of that commit — third-party text, render as plain text."
    )
    differs_from_installed: bool | None = Field(
        None,
        description="Null when nothing is installed; true when the disk SKILL.md differs.",
    )
    installed: bool = Field(
        ..., description="A skill directory with this slug already exists on disk."
    )
    installed_by_masterwork: bool = Field(
        ...,
        description="True only when masterwork wrote it; a hand-installed one is not removable.",
    )
    skill_md: str = Field(..., description="Full SKILL.md text, rendered as plain text only.")
    files: list[str] = Field(..., description="Companion file paths, relative to the skill folder.")


class SkillInstallRequest(BaseModel):
    owner: str
    repo: str
    skill: str
    overwrite: bool = Field(False, description="Replace an existing directory at this slug.")


class InstalledSkill(BaseModel):
    asset_id: str = Field(
        ..., description='"claude:skill:<name>", the id the assets API also uses.'
    )
    name: str
    owner: str
    repo: str
    license: str | None = None
    registry: SkillRegistry
    installed_at: datetime
