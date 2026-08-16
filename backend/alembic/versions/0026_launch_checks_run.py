"""session_launches: which run a check was started for

Revision ID: 0026_launch_checks_run
Revises: 0025_work_source_owner
Create Date: 2026-08-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0026_launch_checks_run"
down_revision: str | None = "0025_work_source_owner"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("session_launches", sa.Column("checks_run_id", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("session_launches", "checks_run_id")
