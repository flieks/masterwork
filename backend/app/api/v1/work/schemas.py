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
    current_iteration: str | None = Field(
        ..., description="The team's current sprint (iteration path), refreshed on sync."
    )
    owner_display_name: str | None = Field(
        ..., description="Display name of the PAT owner, refreshed on sync; what @Me matches."
    )
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
    assigned_to: str | None
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


class WorkPullRequest(BaseModel):
    id: int
    source_id: str
    external_id: int = Field(..., description="DevOps pullRequestId.")
    repository_id: str
    repository_name: str
    repository_remote_url: str = Field(
        ..., description="What delegatePullRequest resolves to a local checkout."
    )
    title: str
    description: str
    source_branch: str = Field(..., description="refs/heads/ prefix stripped.")
    target_branch: str = Field(..., description="refs/heads/ prefix stripped.")
    status: str = Field(..., description='DevOps status, e.g. "active".')
    is_draft: bool
    created_by: str | None
    external_url: str
    external_changed_at: datetime
    synced_at: datetime


class WorkPrThreadComment(BaseModel):
    id: int | None
    author: str | None
    content: str
    comment_type: str | None
    published_at: str | None


class WorkPrThread(BaseModel):
    id: int
    pull_request_id: int
    external_id: int = Field(..., description="DevOps thread id.")
    status: str | None
    is_resolved: bool = Field(
        ..., description="Derived from status being fixed/closed/wontFix/byDesign."
    )
    file_path: str | None = Field(..., description="Null for a PR-level thread.")
    right_file_line: int | None
    comments: list[WorkPrThreadComment]
    synced_at: datetime


class WorkRepoPath(BaseModel):
    id: int
    remote_url: str = Field(..., description="The normalized remote — the key, not the name.")
    local_path: str
    created_at: datetime


class WorkRepoPathCreateRequest(BaseModel):
    remote_url: str
    local_path: str = Field(..., description="Absolute path to an existing directory.")


class PullRequestDelegateResponse(BaseModel):
    resolved: bool = Field(..., description="False means nothing was launched.")
    remote_url: str = Field(..., description="The PR's repository_remote_url, as stored.")
    local_path: str | None
    reason: str | None = Field(None, description="Set exactly when resolved is false.")
    launch_id: int | None
    run_id: str | None
    prompt: str | None = Field(None, description="What the launched session received.")
    unresolved_thread_count: int
