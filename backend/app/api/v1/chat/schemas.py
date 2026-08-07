"""Chat + proposal API schemas — names and fields match the frozen API contract."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.api.v1.projects.schemas import ProjectUpdate


class MessageRole(StrEnum):
    user = "user"
    assistant = "assistant"
    error = "error"


class ChangeAction(StrEnum):
    update = "update"
    create = "create"
    delete = "delete"


class ProposalStatus(StrEnum):
    pending = "pending"
    applied = "applied"
    rejected = "rejected"
    failed = "failed"


class ProposalChange(BaseModel):
    path: str = Field(..., description="Absolute target path.")
    action: ChangeAction
    new_content: str | None = Field(None, description="Full new file content; null for a delete.")
    description: str = Field("", description="What this change does.")
    asset_id: str | None = Field(None, description="Set when the path maps to a known asset.")


class Proposal(BaseModel):
    id: str
    status: ProposalStatus
    summary: str
    changes: list[ProposalChange]
    project_update: ProjectUpdate | None = Field(
        None, description="A proposed project update; carried alongside or instead of file changes."
    )
    error: str | None = Field(None, description="Set when status is 'failed'.")
    created_at: datetime


class ChatSession(BaseModel):
    id: str
    title: str
    project_id: str | None = Field(None, description="Owning project, or null for global chat.")
    asset_id: str | None = Field(
        None, description="Owning asset, or null when the chat is not asset-scoped."
    )
    created_at: datetime
    updated_at: datetime


class ChatSessionCreateRequest(BaseModel):
    title: str | None = Field(
        None, description='Defaults to "New chat"; retitled from the first message.'
    )
    project_id: str | None = Field(None, description="Scope this session to a project.")
    asset_id: str | None = Field(
        None, description="Scope this session to one skill/agent, e.g. 'claude:agent:architect'."
    )


class ChatSessionUpdateRequest(BaseModel):
    title: str


class ChatMessage(BaseModel):
    id: str
    session_id: str
    role: MessageRole
    content: str = Field(..., description="Markdown; proposal block already stripped.")
    proposal: Proposal | None = Field(
        None, description="Only on assistant messages that propose changes."
    )
    created_at: datetime


class ChatMessageCreateRequest(BaseModel):
    content: str


class ChatExchange(BaseModel):
    user_message: ChatMessage
    assistant_message: ChatMessage
