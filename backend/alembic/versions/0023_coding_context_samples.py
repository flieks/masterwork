"""coding_context_samples: the reported context-growth series per session

Revision ID: 0023_coding_context_samples
Revises: 0022_session_launch_run_id
Create Date: 2026-08-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.types import JSONColumn

revision: str = "0023_coding_context_samples"
down_revision: str | None = "0022_session_launch_run_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # No backfill here: like coding_envelopes, this is reported on the hook body,
    # which is never stored — nothing in the event stream can reconstruct it.
    op.create_table(
        "coding_context_samples",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=200), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.String(length=200), nullable=False),
        sa.Column("is_sidechain", sa.Boolean(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_tokens", sa.BigInteger(), nullable=False),
        sa.Column("output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("delta_tokens", sa.Integer(), nullable=True),
        sa.Column("tools", JSONColumn, nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["session_id"], ["coding_sessions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "session_id", "message_id", name="uq_coding_context_samples_session_message"
        ),
    )
    op.create_index(
        "ix_coding_context_samples_session_seq",
        "coding_context_samples",
        ["session_id", "seq"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_coding_context_samples_session_seq", table_name="coding_context_samples"
    )
    op.drop_table("coding_context_samples")
