"""Project API schemas — names and fields match the frozen API contract v1.1."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Project(BaseModel):
    # Response model: every field is always present (no defaults), so the
    # OpenAPI schema marks them required and the generated TS types are exact.
    id: str = Field(..., description="Project uuid.")
    name: str
    goal: str = Field(..., description="Scenario description, markdown.")
    flow_mermaid: str | None = Field(
        ..., description="Mermaid source: how the linked assets work together."
    )
    asset_ids: list[str] = Field(
        ..., description='Linked asset ids, e.g. "claude:skill:azure-deploy".'
    )
    scenario: str = Field(
        ..., description="Last simulation scenario used/generated; markdown/plain text."
    )
    change_summary: str | None = Field(
        ..., description="Last generated summary of all applied asset changes; markdown."
    )
    change_summary_at: datetime | None = Field(
        ..., description="When change_summary was generated; null when never."
    )
    trigger_guide: str | None = Field(
        ..., description="Last generated guide on how to trigger this toolkit; markdown."
    )
    trigger_guide_at: datetime | None = Field(
        ..., description="When trigger_guide was generated; null when never."
    )
    generality_report: str | None = Field(
        ..., description="Last generality audit of the linked assets; markdown."
    )
    generality_report_at: datetime | None = Field(
        ..., description="When generality_report was generated; null when never."
    )
    created_at: datetime
    updated_at: datetime


class ProjectSummaryResponse(BaseModel):
    summary: str = Field(..., description="The generated change summary, saved on the project.")
    generated_at: datetime


class ProjectTriggerResponse(BaseModel):
    trigger_guide: str = Field(..., description="The generated guide, saved on the project.")
    generated_at: datetime


class CrossChange(BaseModel):
    """A modification another project made to an asset this project links."""

    asset_id: str
    action: str
    source: Literal["simulation", "proposal"]
    project_id: str | None = Field(..., description="Null when a global (unscoped) chat did it.")
    project_name: str | None
    title: str = Field(..., description="The applied suggestion's title or proposal's summary.")
    applied_at: datetime


class ProjectCrossChangesResponse(BaseModel):
    since: datetime | None = Field(
        ..., description="This project's last completed run; null = no runs, nothing to invalidate."
    )
    changes: list[CrossChange] = Field(..., description="Newest first.")


class SuggestedLink(BaseModel):
    asset_id: str
    reason: str = Field("", description="One line: why the goal needs this asset.")
    confidence: int = Field(
        70,
        ge=0,
        le=100,
        description="How strongly the goal exercises this asset. >=60 is a recommended link; "
        "40-59 is a borderline candidate listed for the user to judge.",
    )


class ProjectSuggestLinksResponse(BaseModel):
    """The complete recommended toolkit; nothing is saved until the user does."""

    suggestions: list[SuggestedLink] = Field(..., description="Highest confidence first.")


class ProjectGeneralityResponse(BaseModel):
    generality_report: str = Field(..., description="The generated audit, saved on the project.")
    generated_at: datetime


class ProjectCreateRequest(BaseModel):
    name: str
    goal: str = Field("", description="Optional scenario description; defaults to empty.")


class ProjectUpdateRequest(BaseModel):
    """Partial update. Only fields present in the request body are changed;
    `flow_mermaid` is explicitly nullable (send null to clear the diagram).
    """

    name: str | None = None
    goal: str | None = None
    flow_mermaid: str | None = None
    asset_ids: list[str] | None = None
    scenario: str | None = None


class ProjectUpdate(BaseModel):
    """A project update proposed by the chatbot (proposal payload).

    `null` on any field means leave it unchanged. `asset_ids`, when present, is
    the COMPLETE new list of linked assets (not a delta).
    """

    project_id: str
    name: str | None = None
    goal: str | None = None
    flow_mermaid: str | None = None
    asset_ids: list[str] | None = None
    description: str = Field("", description="Human-readable summary of the update.")
