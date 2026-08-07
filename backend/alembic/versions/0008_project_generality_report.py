"""project generality audit report

Revision ID: 0008_project_generality_report
Revises: 0007_simulation_checklist
Create Date: 2026-07-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_project_generality_report"
down_revision: str | None = "0007_simulation_checklist"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("generality_report", sa.Text(), nullable=True))
    op.add_column(
        "projects", sa.Column("generality_report_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("projects", "generality_report_at")
    op.drop_column("projects", "generality_report")
