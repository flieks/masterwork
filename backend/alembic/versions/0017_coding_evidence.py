"""evidence tables: the envelope an agent returned, and every gate check's note

Revision ID: 0017_coding_evidence
Revises: 0016_coding_asset_use_log
Create Date: 2026-08-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.types import JSONColumn

revision: str = "0017_coding_evidence"
down_revision: str | None = "0016_coding_asset_use_log"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # No backfill here: `POST /coding-sessions/backfill` recovers what the stored
    # event stream can still prove, and an envelope body was never in it.
    op.create_table(
        "coding_envelopes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=200), nullable=False),
        sa.Column("phase_id", sa.Integer(), nullable=True),
        sa.Column("event_id", sa.Integer(), nullable=True),
        sa.Column("role", sa.String(length=100), nullable=True),
        sa.Column("attempt", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("parsed", sa.Boolean(), nullable=False),
        sa.Column("parse_error", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("body", JSONColumn, nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=True),
        sa.Column("origin", sa.String(length=20), server_default="reported", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["session_id"], ["coding_sessions.id"], ondelete="CASCADE"),
        # SET NULL, not CASCADE: a backfill drops and rebuilds every phase, and
        # a reported envelope must outlive the stage row it was linked to — the
        # replay re-points it by `event_id`.
        sa.ForeignKeyConstraint(["phase_id"], ["coding_phases.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["event_id"], ["coding_events.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_coding_envelopes_session_phase", "coding_envelopes", ["session_id", "phase_id"]
    )
    op.create_index("ix_coding_envelopes_event_id", "coding_envelopes", ["event_id"])

    op.create_table(
        "coding_gate_checks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=200), nullable=False),
        sa.Column("phase_id", sa.Integer(), nullable=True),
        sa.Column("event_id", sa.Integer(), nullable=True),
        sa.Column("gate", sa.String(length=100), nullable=False),
        sa.Column("attempt", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("item", sa.String(length=500), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("origin", sa.String(length=20), server_default="reported", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["session_id"], ["coding_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["phase_id"], ["coding_phases.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["event_id"], ["coding_events.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_coding_gate_checks_session_phase", "coding_gate_checks", ["session_id", "phase_id"]
    )
    op.create_index("ix_coding_gate_checks_event_id", "coding_gate_checks", ["event_id"])


def downgrade() -> None:
    op.drop_index("ix_coding_gate_checks_event_id", table_name="coding_gate_checks")
    op.drop_index("ix_coding_gate_checks_session_phase", table_name="coding_gate_checks")
    op.drop_table("coding_gate_checks")
    op.drop_index("ix_coding_envelopes_event_id", table_name="coding_envelopes")
    op.drop_index("ix_coding_envelopes_session_phase", table_name="coding_envelopes")
    op.drop_table("coding_envelopes")
