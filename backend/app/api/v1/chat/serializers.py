"""Map ORM rows to the contract's Pydantic schemas."""

from __future__ import annotations

from app.api.v1.chat import schemas
from app.api.v1.projects.schemas import ProjectUpdate
from app.db.models.chat import ChatMessage, ChatSession, Proposal


def session_to_schema(session: ChatSession) -> schemas.ChatSession:
    return schemas.ChatSession(
        id=str(session.id),
        title=session.title,
        project_id=str(session.project_id) if session.project_id is not None else None,
        asset_id=session.asset_id,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


def proposal_to_schema(proposal: Proposal) -> schemas.Proposal:
    return schemas.Proposal(
        id=str(proposal.id),
        status=schemas.ProposalStatus(proposal.status),
        summary=proposal.summary,
        changes=[schemas.ProposalChange.model_validate(c) for c in proposal.changes],
        project_update=(
            ProjectUpdate.model_validate(proposal.project_update)
            if proposal.project_update is not None
            else None
        ),
        error=proposal.error,
        created_at=proposal.created_at,
    )


def message_to_schema(message: ChatMessage, proposal: Proposal | None) -> schemas.ChatMessage:
    return schemas.ChatMessage(
        id=str(message.id),
        session_id=str(message.session_id),
        role=schemas.MessageRole(message.role),
        content=message.content,
        proposal=proposal_to_schema(proposal) if proposal is not None else None,
        created_at=message.created_at,
    )
