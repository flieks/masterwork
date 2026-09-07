"""Session launches: a log of factory runs started from the in-app launcher.

Attribution rides the existing MASTERWORK_FACTORY_RUN_ID handshake
(factory/adw/agent.py) — this table is just a launch record, not a FK to
coding_sessions.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Integer, String, Text, UniqueConstraint, func
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
    # Only interview launches carry one — the factory run id, server-generated
    # at launch time so it is on the row before the child is even spawned.
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Set when this launch exists to check another run — the only record that
    # a check belongs to the run it was asked about.
    checks_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class DismissedRun(Base):
    """A factory run the user has waved away, so the runs list stops offering it.

    Masterwork's own bookkeeping, not the factory's: the run dir stays exactly
    as the factory wrote it, and a dismissal is undone by deleting this row.
    """

    __tablename__ = "dismissed_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_path: Mapped[str] = mapped_column(Text)
    run_id: Mapped[str] = mapped_column(String(64))
    dismissed_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("project_path", "run_id", name="uq_dismissed_runs_project_run"),
    )
