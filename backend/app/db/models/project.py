"""Project persistence.

A project is a persistent workspace: a name, a goal, a set of linked asset ids,
and a Mermaid flow diagram. Its chat sessions live in `chat_sessions` and
cascade on delete via the FK there.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import JSONColumn, UTCDateTime


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    goal: Mapped[str] = mapped_column(Text, default="")
    flow_mermaid: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Last scenario used or generated for this project's simulations.
    scenario: Mapped[str] = mapped_column(Text, default="")
    # Linked asset ids, e.g. ["claude:skill:azure-deploy"].
    asset_ids: Mapped[list[str]] = mapped_column(JSONColumn, default=list)
    # Last generated summary of all applied asset changes (markdown).
    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    change_summary_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # Last generated guide on how to trigger this toolkit from Claude Code (markdown).
    trigger_guide: Mapped[str | None] = mapped_column(Text, nullable=True)
    trigger_guide_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # Last generality audit: whether the linked assets stayed general or leaked
    # scenario-specific product logic into shared skills/agents (markdown).
    generality_report: Mapped[str | None] = mapped_column(Text, nullable=True)
    generality_report_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now()
    )
