"""work items: parent link (System.Parent) so tasks can nest under stories

Revision ID: 0019_work_item_parent
Revises: 0018_work_items
Create Date: 2026-08-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019_work_item_parent"
down_revision: str | None = "0018_work_items"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("work_items", sa.Column("parent_external_id", sa.Integer(), nullable=True))
    op.add_column(
        "work_items",
        sa.Column(
            "pulled_as_parent", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
    )


def downgrade() -> None:
    op.drop_column("work_items", "pulled_as_parent")
    op.drop_column("work_items", "parent_external_id")
