"""project scenario

Revision ID: 0004_project_scenario
Revises: 0003_simulations
Create Date: 2026-07-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_project_scenario"
down_revision: str | None = "0003_simulations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("scenario", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("projects", "scenario")
