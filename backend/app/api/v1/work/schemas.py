"""Azure DevOps work-item API schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class WorkSource(BaseModel):
    id: str = Field(..., description="Work source uuid.")
    provider: str = Field(..., description='Always "azuredevops" today.')
    org_url: str
    project: str
    team: str | None
    query_wiql: str | None = Field(
        ..., description="Overrides the default assigned-to-me WIQL when set."
    )
    secret_ref: str = Field(..., description="Env var naming the PAT — never the PAT itself.")
    last_sync_at: datetime | None
    created_at: datetime
    updated_at: datetime


class WorkSourceCreateRequest(BaseModel):
    org_url: str = Field(..., description="e.g. https://dev.azure.com/myorg")
    project: str
    team: str | None = None
    query_wiql: str | None = Field(None, description="Overrides the default assigned-to-me WIQL.")
    secret_ref: str = Field("AZURE_DEVOPS_PAT", description="Env var naming the PAT.")


class WorkItem(BaseModel):
    id: int
    source_id: str
    external_id: int
    parent_external_id: int | None
    pulled_as_parent: bool
    external_url: str
    item_type: str
    title: str
    description_md: str
    acceptance_md: str | None
    state: str
    iteration: str | None
    priority: int | None
    tags: list[str] | None
    external_changed_at: datetime
    synced_at: datetime


class WorkSyncResult(BaseModel):
    fetched: int = Field(..., description="Items returned by the WIQL + batch fetch.")
    inserted: int
    updated: int


class WorkItemStartResponse(BaseModel):
    prompt: str = Field(..., description="The assembled session prompt.")
    launched: bool = Field(
        ..., description="Always false today — no reusable session-launch path exists yet."
    )
    session_id: str | None = Field(..., description="Null until a launched session is linked.")
    link_id: int = Field(..., description="The work_item_sessions row id.")
