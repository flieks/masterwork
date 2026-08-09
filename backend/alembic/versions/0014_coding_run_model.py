"""run-centric coding sessions: phases, agent lanes, roll-ups

Revision ID: 0014_coding_run_model
Revises: 0013_launch_mode
Create Date: 2026-08-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014_coding_run_model"
down_revision: str | None = "0013_launch_mode"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Everything a card needs, promoted out of the stats blob. All nullable but
    # `status`, which every row has an answer for: a run is running until told
    # otherwise, including the ones recorded before this column existed.
    op.add_column("coding_sessions", sa.Column("title", sa.Text(), nullable=True))
    op.add_column("coding_sessions", sa.Column("workflow", sa.String(length=50), nullable=True))
    op.add_column(
        "coding_sessions",
        sa.Column("status", sa.String(length=20), server_default="running", nullable=False),
    )
    op.add_column("coding_sessions", sa.Column("cost_usd", sa.Float(), nullable=True))
    op.add_column("coding_sessions", sa.Column("tokens_total", sa.BigInteger(), nullable=True))
    op.add_column("coding_sessions", sa.Column("tokens_in", sa.BigInteger(), nullable=True))
    op.add_column("coding_sessions", sa.Column("tokens_out", sa.BigInteger(), nullable=True))
    op.add_column("coding_sessions", sa.Column("cache_read_tokens", sa.BigInteger(), nullable=True))

    op.create_table(
        "coding_phases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=200), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=True),
        sa.Column("agent", sa.String(length=100), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="running", nullable=False),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("tokens_in", sa.BigInteger(), nullable=True),
        sa.Column("tokens_out", sa.BigInteger(), nullable=True),
        sa.Column("corrections", sa.Integer(), server_default="0", nullable=False),
        sa.Column("commit_sha", sa.String(length=64), nullable=True),
        sa.Column("gates_passed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("gates_failed", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["session_id"], ["coding_sessions.id"], ondelete="CASCADE"),
        # The upsert key: one row per position in the run.
        sa.UniqueConstraint("session_id", "seq", name="uq_coding_phases_session_seq"),
    )

    op.create_table(
        "coding_agents",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=200), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("color", sa.String(length=20), nullable=True),
        sa.Column("context_tokens", sa.BigInteger(), nullable=True),
        sa.Column("context_window", sa.BigInteger(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("tokens_in", sa.BigInteger(), nullable=True),
        sa.Column("tokens_out", sa.BigInteger(), nullable=True),
        sa.Column("turns", sa.Integer(), server_default="0", nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["session_id"], ["coding_sessions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("session_id", "name", name="uq_coding_agents_session_name"),
    )

    # Batch mode because of the new foreign key: SQLite cannot ALTER a constraint
    # into place, so it copies the table instead. On Postgres this is plain ALTERs.
    with op.batch_alter_table("coding_events") as batch:
        batch.add_column(sa.Column("phase_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("agent", sa.String(length=100), nullable=True))
        batch.add_column(sa.Column("ok", sa.Boolean(), nullable=True))
        batch.add_column(sa.Column("duration_ms", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key(
            "fk_coding_events_phase_id",
            "coding_phases",
            ["phase_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("coding_events") as batch:
        batch.drop_constraint("fk_coding_events_phase_id", type_="foreignkey")
        batch.drop_column("ended_at")
        batch.drop_column("duration_ms")
        batch.drop_column("ok")
        batch.drop_column("agent")
        batch.drop_column("phase_id")

    op.drop_table("coding_agents")
    op.drop_table("coding_phases")

    for column in (
        "cache_read_tokens",
        "tokens_out",
        "tokens_in",
        "tokens_total",
        "cost_usd",
        "status",
        "workflow",
        "title",
    ):
        op.drop_column("coding_sessions", column)
