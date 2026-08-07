"""projects, project-scoped sessions, proposal project updates, asset diagrams

Revision ID: 0002_projects_diagrams
Revises: 0001_initial
Create Date: 2026-07-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

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
        sa.Column("asset_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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

    op.add_column("chat_sessions", sa.Column("project_id", sa.Uuid(), nullable=True))
    op.create_index("ix_chat_sessions_project_id", "chat_sessions", ["project_id"])
    op.create_foreign_key(
        "fk_chat_sessions_project_id",
        "chat_sessions",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.add_column(
        "proposals",
        sa.Column("project_update", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
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
    op.drop_constraint("fk_chat_sessions_project_id", "chat_sessions", type_="foreignkey")
    op.drop_index("ix_chat_sessions_project_id", table_name="chat_sessions")
    op.drop_column("chat_sessions", "project_id")
    op.drop_table("projects")
