"""how a coding session was launched

Revision ID: 0013_launch_mode
Revises: 0012_coding_sessions
Create Date: 2026-08-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013_launch_mode"
down_revision: str | None = "0012_coding_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nullable with no backfill: rows recorded before the launcher chain existed
    # cannot be classified, and null reads as "unknown", which stays listed.
    op.add_column("coding_sessions", sa.Column("launch_mode", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("coding_sessions", "launch_mode")
