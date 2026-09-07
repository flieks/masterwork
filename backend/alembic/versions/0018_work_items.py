"""work items: registered DevOps sources, their mirrored items, and the
coding sessions requested for one

Revision ID: 0018_work_items
Revises: 0017_coding_evidence
Create Date: 2026-08-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.types import JSONColumn

revision: str = "0018_work_items"
down_revision: str | None = "0017_coding_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "work_sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=50), server_default="azuredevops", nullable=False),
        sa.Column("org_url", sa.String(length=500), nullable=False),
        sa.Column("project", sa.String(length=200), nullable=False),
        sa.Column("team", sa.String(length=200), nullable=True),
        sa.Column("query_wiql", sa.Text(), nullable=True),
        sa.Column(
            "secret_ref", sa.String(length=200), server_default="AZURE_DEVOPS_PAT", nullable=False
        ),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "work_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.Integer(), nullable=False),
        sa.Column("external_url", sa.String(length=1000), nullable=False),
        sa.Column("item_type", sa.String(length=100), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description_md", sa.Text(), server_default="", nullable=False),
        sa.Column("acceptance_md", sa.Text(), nullable=True),
        sa.Column("state", sa.String(length=100), nullable=False),
        sa.Column("iteration", sa.String(length=500), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=True),
        sa.Column("tags", JSONColumn, nullable=True),
        sa.Column("raw", JSONColumn, nullable=False),
        sa.Column("external_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["source_id"], ["work_sources.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("source_id", "external_id", name="uq_work_items_source_external"),
    )
    op.create_index("ix_work_items_source_state", "work_items", ["source_id", "state"])

    op.create_table(
        "work_item_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("work_item_id", sa.Integer(), nullable=False),
        # Nullable: no reusable session-launch path exists yet — see plan.md.
        sa.Column("session_id", sa.String(length=200), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("pushed_state", sa.String(length=100), nullable=True),
        sa.Column("last_comment_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["work_item_id"], ["work_items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["coding_sessions.id"], ondelete="CASCADE"),
    )


def downgrade() -> None:
    op.drop_table("work_item_sessions")
    op.drop_index("ix_work_items_source_state", table_name="work_items")
    op.drop_table("work_items")
    op.drop_table("work_sources")
