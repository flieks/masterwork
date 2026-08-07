"""autopilot columns + project change summary

Revision ID: 0005_autopilot_and_summary
Revises: 0004_project_scenario
Create Date: 2026-07-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_autopilot_and_summary"
down_revision: str | None = "0004_project_scenario"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("simulations", sa.Column("autopilot_run_id", sa.Uuid(), nullable=True))
    op.add_column("simulations", sa.Column("autopilot_iteration", sa.Integer(), nullable=True))
    op.add_column("simulations", sa.Column("autopilot_total", sa.Integer(), nullable=True))
    op.add_column("projects", sa.Column("change_summary", sa.Text(), nullable=True))
    op.add_column(
        "projects", sa.Column("change_summary_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("projects", "change_summary_at")
    op.drop_column("projects", "change_summary")
    op.drop_column("simulations", "autopilot_total")
    op.drop_column("simulations", "autopilot_iteration")
    op.drop_column("simulations", "autopilot_run_id")
