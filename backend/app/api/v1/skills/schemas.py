"""Skill catalog API schemas."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from app.services.skill_install import DriftStatus, FileChange, SkillTarget


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
        ...,
        description="A skill with this slug exists in ~/.claude/skills, ~/.codex/skills or "
        "~/.agents/skills (live or switched off).",
    )
    installed_in: list[SkillTarget] = Field(
        default_factory=list,
        description="Which of those folders hold a copy, generic first. An agent folder that "
        "only links to the generic copy is not listed separately.",
    )


class CatalogSourceError(BaseModel):
    registry: SkillRegistry
    message: str = Field(..., description="Why this source's results are missing.")


class CatalogSearchResponse(BaseModel):
    skills: list[CatalogSkill]
    errors: list[CatalogSourceError] = Field(
        ..., description="Per-source failures; a partial result still returns 200."
    )
    search_type: Literal["semantic", "fuzzy", "unknown"] = Field(
        ..., description="skills.sh's own ranking signal; results are ordered by it when known."
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
        ...,
        description="A skill with this slug exists in any of the three skills folders.",
    )
    installed_in: list[SkillTarget] = Field(
        default_factory=list, description="Which folders hold a copy, generic first."
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
    overwrite: bool = Field(
        False,
        description="Replace the existing copy at this slug — where it already lives, "
        "whatever `target` says.",
    )
    target: SkillTarget = Field(
        SkillTarget.claude,
        description='Skills folder for a new install: "claude" (~/.claude/skills), "codex" '
        '(~/.codex/skills) or "generic" (~/.agents/skills, which Codex loads itself, then '
        "linked into ~/.claude/skills when that has no entry of that name).",
    )


class InstalledSkill(BaseModel):
    asset_id: str = Field(
        ...,
        description='"<location>:skill:<name>" ("claude", "codex" or "generic"), the id the '
        "assets API also uses; from `target` when the skill is gone from disk.",
    )
    target: SkillTarget = Field(
        SkillTarget.claude, description="The folder masterwork last wrote the skill into."
    )
    location: SkillTarget | None = Field(
        None,
        description="The folder the copy update and uninstall act on now — `generic` once the "
        "skill was made generic. Null when no copy is on disk.",
    )
    name: str
    owner: str
    repo: str
    license: str | None = None
    registry: SkillRegistry
    installed_at: datetime
    source_url: str = Field(..., description="Link to the skill's folder on GitHub.")
    installed_sha: str | None = Field(
        None, description="Upstream commit for the folder at install time; null when unknown."
    )
    root_path: str | None = Field(
        None, description='Folder inside the repo the skill came from; "" for the repo root.'
    )
    last_checked_at: datetime | None = Field(
        None, description="When the upstream check last ran; null means never."
    )
    upstream_sha: str | None = Field(None, description="Upstream commit seen by the last check.")
    drift_status: DriftStatus | None = Field(
        None, description="Cached result of the last check; null until one has run."
    )


class UpstreamFileChange(BaseModel):
    path: str = Field(..., description="Relative to the skill folder.")
    change: FileChange


class UpstreamCheckResult(BaseModel):
    name: str
    status: DriftStatus
    checked_at: datetime
    source_url: str | None = Field(
        None, description="Link to the skill's folder on GitHub; null with no install row."
    )
    installed_sha: str | None = None
    upstream_sha: str | None = Field(None, description="Newest upstream commit for the folder.")
    upstream_last_modified_at: datetime | None = Field(
        None, description="Date of that commit; null if the lookup failed."
    )
    upstream_last_change_summary: str | None = Field(
        None, description="Subject of that commit — third-party text, render as plain text."
    )
    skill_md_diff: str = Field(
        ..., description='Unified diff, installed SKILL.md -> upstream; "" when identical.'
    )
    other_changes: list[UpstreamFileChange] = Field(
        ..., description="Companion files that differ; SKILL.md is in the diff instead."
    )


class SkillUpdateRequest(BaseModel):
    force: bool = Field(
        False, description="Overwrite local edits: required when status is edited_locally/diverged."
    )


class SkillMatchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="What the user is trying to do.")


class SkillMatch(BaseModel):
    name: str
    reason: str = Field(..., description="One line: why this skill fits the query.")


class SkillMatchResponse(BaseModel):
    matches: list[SkillMatch] = Field(..., description="Best first, capped at 8.")
