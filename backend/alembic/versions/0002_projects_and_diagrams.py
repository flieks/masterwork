"""projects, project-scoped sessions, proposal project updates, asset diagrams

Revision ID: 0002_projects_diagrams
Revises: 0001_initial
Create Date: 2026-07-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from app.db.types import JSONColumn

from alembic import op

revision: str = "0002_projects_diagrams"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("flow_mermaid", sa.Text(), nullable=True),
        sa.Column("asset_ids", JSONColumn, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    # Batch mode: SQLite cannot ALTER in a constraint, so it rebuilds the table.
    # On Postgres this emits the same plain ALTERs it always did.
    with op.batch_alter_table("chat_sessions") as batch_op:
        batch_op.add_column(sa.Column("project_id", sa.Uuid(), nullable=True))
        batch_op.create_index("ix_chat_sessions_project_id", ["project_id"])
        batch_op.create_foreign_key(
            "fk_chat_sessions_project_id",
            "projects",
            ["project_id"],
            ["id"],
            ondelete="CASCADE",
        )

    op.add_column(
        "proposals",
        sa.Column("project_update", JSONColumn, nullable=True),
    )

    op.create_table(
        "asset_diagrams",
        sa.Column("asset_id", sa.String(), nullable=False),
        sa.Column("file_hash", sa.String(), nullable=False),
        sa.Column("mermaid", sa.Text(), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("asset_id"),
    )


def downgrade() -> None:
    op.drop_table("asset_diagrams")
    op.drop_column("proposals", "project_update")
    with op.batch_alter_table("chat_sessions") as batch_op:
        batch_op.drop_constraint("fk_chat_sessions_project_id", type_="foreignkey")
        batch_op.drop_index("ix_chat_sessions_project_id")
        batch_op.drop_column("project_id")
    op.drop_table("projects")
