"""simulation capability checklist

Revision ID: 0007_simulation_checklist
Revises: 0006_sim_stats_and_trigger
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from app.db.types import JSONColumn

from alembic import op

revision: str = "0007_simulation_checklist"
down_revision: str | None = "0006_sim_stats_and_trigger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "simulations",
        sa.Column("checklist", JSONColumn, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("simulations", "checklist")
