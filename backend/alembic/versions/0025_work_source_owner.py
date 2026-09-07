"""work sources: PAT owner display name, refreshed on sync

Revision ID: 0025_work_source_owner
Revises: 0024_dismissed_runs
Create Date: 2026-08-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0025_work_source_owner"
down_revision: str | None = "0024_dismissed_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("work_sources", sa.Column("owner_display_name", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("work_sources", "owner_display_name")
