"""Session launches: a log of factory runs started from the in-app launcher.

Attribution rides the existing MASTERWORK_FACTORY_RUN_ID handshake
(factory/adw/agent.py) — this table is just a launch record, not a FK to
coding_sessions.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UTCDateTime

MODE_AUTONOMOUS = "autonomous"
MODE_INTERVIEW = "interview"


class SessionLaunch(Base):
    """One `factory/run.py` spawn requested through the launcher."""

    __tablename__ = "session_launches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_path: Mapped[str] = mapped_column(Text)
    request_text: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(20))
    launched_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
