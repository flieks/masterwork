"""simulation run stats + project trigger guide

Revision ID: 0006_sim_stats_and_trigger
Revises: 0005_autopilot_and_summary
Create Date: 2026-07-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from app.db.types import JSONColumn

from alembic import op

revision: str = "0006_sim_stats_and_trigger"
down_revision: str | None = "0005_autopilot_and_summary"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "simulations",
        sa.Column("stats", JSONColumn, nullable=True),
    )
    op.add_column("projects", sa.Column("trigger_guide", sa.Text(), nullable=True))
    op.add_column(
        "projects", sa.Column("trigger_guide_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("projects", "trigger_guide_at")
    op.drop_column("projects", "trigger_guide")
    op.drop_column("simulations", "stats")
