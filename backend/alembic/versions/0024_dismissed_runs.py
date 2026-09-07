"""dismissed_runs: factory runs the user waved away from the runs list

Revision ID: 0024_dismissed_runs
Revises: 0023_coding_context_samples
Create Date: 2026-08-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0024_dismissed_runs"
down_revision: str | None = "0023_coding_context_samples"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dismissed_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("project_path", sa.Text(), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column(
            "dismissed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_path", "run_id", name="uq_dismissed_runs_project_run"),
    )


def downgrade() -> None:
    op.drop_table("dismissed_runs")
