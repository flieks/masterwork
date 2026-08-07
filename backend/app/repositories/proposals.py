"""Data access for proposals."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.chat import ChatMessage, ChatSession, Proposal


async def create_proposal(
    db: AsyncSession,
    *,
    message_id: uuid.UUID,
    summary: str,
    changes: list[dict[str, Any]],
    project_update: dict[str, Any] | None = None,
    status: str = "pending",
    error: str | None = None,
) -> Proposal:
    proposal = Proposal(
        message_id=message_id,
        summary=summary,
        changes=changes,
        project_update=project_update,
        status=status,
        error=error,
    )
    db.add(proposal)
    await db.flush()
    await db.refresh(proposal)
    return proposal


async def get_proposal(db: AsyncSession, proposal_id: uuid.UUID) -> Proposal | None:
    return await db.get(Proposal, proposal_id)


async def list_applied_for_project(db: AsyncSession, project_id: uuid.UUID) -> list[Proposal]:
    """Applied chat proposals of a project's sessions, oldest first."""
    result = await db.execute(
        select(Proposal)
        .join(ChatMessage, Proposal.message_id == ChatMessage.id)
        .join(ChatSession, ChatMessage.session_id == ChatSession.id)
        .where(ChatSession.project_id == project_id, Proposal.status == "applied")
        .order_by(Proposal.applied_at.asc().nulls_last())
    )
    return list(result.scalars().all())


async def list_applied_excluding_project(
    db: AsyncSession, project_id: uuid.UUID
) -> list[tuple[Proposal, uuid.UUID | None]]:
    """Applied proposals from OTHER projects' sessions and from global (unscoped)
    chats, with the owning project id (None = global)."""
    result = await db.execute(
        select(Proposal, ChatSession.project_id)
        .join(ChatMessage, Proposal.message_id == ChatMessage.id)
        .join(ChatSession, ChatMessage.session_id == ChatSession.id)
        .where(
            Proposal.status == "applied",
            (ChatSession.project_id != project_id) | (ChatSession.project_id.is_(None)),
        )
    )
    return [(row[0], row[1]) for row in result.all()]
