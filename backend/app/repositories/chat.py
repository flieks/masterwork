"""Data access for chat sessions and messages."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models.chat import DEFAULT_SESSION_TITLE, ChatMessage, ChatSession


async def create_session(
    db: AsyncSession,
    title: str | None,
    project_id: uuid.UUID | None = None,
    asset_id: str | None = None,
) -> ChatSession:
    session = ChatSession(
        title=title or DEFAULT_SESSION_TITLE, project_id=project_id, asset_id=asset_id
    )
    db.add(session)
    await db.flush()
    await db.refresh(session)
    return session


async def list_sessions(
    db: AsyncSession,
    *,
    scoped: bool = False,
    project_id: uuid.UUID | None = None,
    asset_id: str | None = None,
) -> list[ChatSession]:
    """List sessions, newest first. `asset_id` filters to one asset's sessions.
    Otherwise `scoped=False` returns all and `scoped=True` filters to
    `project_id` (None → global sessions where project_id IS NULL).
    """
    stmt = select(ChatSession).order_by(ChatSession.updated_at.desc())
    if asset_id is not None:
        stmt = stmt.where(ChatSession.asset_id == asset_id)
    elif scoped:
        if project_id is None:
            stmt = stmt.where(ChatSession.project_id.is_(None), ChatSession.asset_id.is_(None))
        else:
            stmt = stmt.where(ChatSession.project_id == project_id)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_session(db: AsyncSession, session_id: uuid.UUID) -> ChatSession | None:
    return await db.get(ChatSession, session_id)


async def delete_session(db: AsyncSession, session: ChatSession) -> None:
    await db.delete(session)
    await db.flush()


async def add_message(
    db: AsyncSession, session_id: uuid.UUID, role: str, content: str
) -> ChatMessage:
    message = ChatMessage(session_id=session_id, role=role, content=content)
    db.add(message)
    await db.flush()
    await db.refresh(message)
    return message


async def list_messages(db: AsyncSession, session_id: uuid.UUID) -> list[ChatMessage]:
    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at.asc())
        .options(selectinload(ChatMessage.proposal))
    )
    return list(result.scalars().all())
