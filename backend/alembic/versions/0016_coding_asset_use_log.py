"""per-call asset log: which run used an asset, and what it was handed

Revision ID: 0016_coding_asset_use_log
Revises: 0015_coding_assets
Create Date: 2026-08-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.types import JSONColumn

revision: str = "0016_coding_asset_use_log"
down_revision: str | None = "0015_coding_assets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # No backfill here: the rows are derived, and `POST /coding-sessions/backfill`
    # rebuilds them from the stored event stream.
    op.create_table(
        "coding_asset_uses",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("lane", sa.String(length=100), nullable=True),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("input", JSONColumn, nullable=True),
        sa.Column(
            "used_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["session_id"], ["coding_sessions.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_coding_asset_uses_kind_name", "coding_asset_uses", ["kind", "name"])
    op.create_index("ix_coding_asset_uses_session_id", "coding_asset_uses", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_coding_asset_uses_session_id", table_name="coding_asset_uses")
    op.drop_index("ix_coding_asset_uses_kind_name", table_name="coding_asset_uses")
    op.drop_table("coding_asset_uses")
