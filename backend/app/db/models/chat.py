"""Chat persistence: sessions, messages, and proposals.

Assets are NOT modelled here — the file on disk is their source of truth. Only
chat state lives in Postgres.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.types import JSONColumn, UTCDateTime

DEFAULT_SESSION_TITLE = "New chat"


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(200), default=DEFAULT_SESSION_TITLE)
    # Null = global (unscoped) chat. Deleting a project cascades its sessions.
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # Null = not asset-scoped. Asset ids ("claude:agent:architect") live on disk,
    # so this is a plain string, not an FK.
    asset_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    # Internal: the claude CLI session id used for --resume. Never exposed in the API.
    claude_session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now()
    )

    messages: Mapped[list[ChatMessage]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ChatMessage.created_at",
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # "user" | "assistant" | "error"
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    session: Mapped[ChatSession] = relationship(back_populates="messages")
    proposal: Mapped[Proposal | None] = relationship(
        back_populates="message",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )


class Proposal(Base):
    __tablename__ = "proposals"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("chat_messages.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="pending")
    summary: Mapped[str] = mapped_column(Text, default="")
    # List of ProposalChange dicts (path, action, new_content, description, asset_id).
    changes: Mapped[list[dict[str, Any]]] = mapped_column(JSONColumn, default=list)
    # ProjectUpdate dict (project_id, name, goal, flow_mermaid, asset_ids, description)
    # or null. A proposal carries file changes, a project update, or both.
    project_update: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    message: Mapped[ChatMessage] = relationship(back_populates="proposal")
