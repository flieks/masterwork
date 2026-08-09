"""claude code session observability

Revision ID: 0012_coding_sessions
Revises: 0011_interrupted_status
Create Date: 2026-08-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.types import JSONColumn

revision: str = "0012_coding_sessions"
down_revision: str | None = "0011_interrupted_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "coding_sessions",
        # The Claude Code session_id, supplied by the hook — not a uuid we mint.
        sa.Column("id", sa.String(length=200), nullable=False),
        sa.Column("cwd", sa.Text(), server_default="", nullable=False),
        sa.Column("git_repo", sa.String(length=200), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("source", sa.String(length=50), server_default="claude-code", nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "last_event_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stats", JSONColumn, nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_coding_sessions_last_event_at", "coding_sessions", ["last_event_at"])

    op.create_table(
        "coding_events",
        # Autoincrementing so it can serve as the frontend's poll cursor.
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=200), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("tool_name", sa.String(length=200), nullable=True),
        sa.Column("payload", JSONColumn, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["session_id"], ["coding_sessions.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_coding_events_session_id_id", "coding_events", ["session_id", "id"])


def downgrade() -> None:
    op.drop_index("ix_coding_events_session_id_id", table_name="coding_events")
    op.drop_table("coding_events")
    op.drop_index("ix_coding_sessions_last_event_at", table_name="coding_sessions")
    op.drop_table("coding_sessions")
