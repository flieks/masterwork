"""work items: assignee display name; work sources: current iteration path

Revision ID: 0021_work_assignee_sprint
Revises: 0020_app_settings_and_launches
Create Date: 2026-08-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0021_work_assignee_sprint"
down_revision: str | None = "0020_app_settings_and_launches"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("work_items", sa.Column("assigned_to", sa.String(300), nullable=True))
    op.add_column("work_sources", sa.Column("current_iteration", sa.String(500), nullable=True))


def downgrade() -> None:
    op.drop_column("work_sources", "current_iteration")
    op.drop_column("work_items", "assigned_to")
