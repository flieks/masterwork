"""Simulation persistence.

A simulation is one dry-run evaluation of a project: claude -p reads the linked
asset files, walks a scenario against the project goal, and returns a scored
report. Suggestions (with concrete file changes and per-suggestion apply state)
live denormalized in JSONColumn — they are only ever read/mutated through their
parent simulation.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import JSONColumn, UTCDateTime


class Simulation(Base):
    __tablename__ = "simulations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|completed|failed
    scenario: Mapped[str] = mapped_column(Text, default="")

    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    verdict: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    analysis: Mapped[str | None] = mapped_column(Text, nullable=True)
    trace_mermaid: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggestions: Mapped[list[dict[str, Any]]] = mapped_column(JSONColumn, default=list)
    # Per-scenario capability checklist the score is computed from; null for pre-v1.7 runs.
    checklist: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONColumn, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # CLI-reported run metadata (model, duration_ms, tokens, cost_usd); null for old runs.
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSONColumn, nullable=True)

    # True when the run re-derived its checklist from scratch instead of re-grading
    # the previous run's — breaks the frozen-rubric ratchet that tops runs out at 100.
    control_run: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))

    # Set when the run is one iteration of an autopilot chain; null for manual runs.
    autopilot_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    autopilot_iteration: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-based
    autopilot_total: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
