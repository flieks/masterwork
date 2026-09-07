"""session_launches: nullable run_id, set for interview launches only

Revision ID: 0022_session_launch_run_id
Revises: 0021_work_assignee_sprint
Create Date: 2026-08-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0022_session_launch_run_id"
down_revision: str | None = "0021_work_assignee_sprint"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("session_launches", sa.Column("run_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("session_launches", "run_id")
