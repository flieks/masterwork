"""asset-scoped chat sessions

Revision ID: 0010_chat_asset_scope
Revises: 0009_simulation_control_run
Create Date: 2026-08-07
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_chat_asset_scope"
down_revision: str | None = "0009_simulation_control_run"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chat_sessions", sa.Column("asset_id", sa.String(length=200), nullable=True))
    op.create_index("ix_chat_sessions_asset_id", "chat_sessions", ["asset_id"])


def downgrade() -> None:
    op.drop_index("ix_chat_sessions_asset_id", table_name="chat_sessions")
    op.drop_column("chat_sessions", "asset_id")
