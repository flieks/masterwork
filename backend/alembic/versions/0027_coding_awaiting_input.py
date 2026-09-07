"""coding_sessions: when a run went blocked on its human

Revision ID: 0027_coding_awaiting_input
Revises: 0026_launch_checks_run
Create Date: 2026-08-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0027_coding_awaiting_input"
down_revision: str | None = "0026_launch_checks_run"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "coding_sessions",
        sa.Column("awaiting_input_since", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("coding_sessions", "awaiting_input_since")
