"""work pull requests: mirrored PRs, their review threads, and the
remote-to-local-folder memory delegate resolution uses

Revision ID: 0028_work_pull_requests
Revises: 0027_coding_awaiting_input
Create Date: 2026-08-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.types import JSONColumn

revision: str = "0028_work_pull_requests"
down_revision: str | None = "0027_coding_awaiting_input"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "work_pull_requests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.Integer(), nullable=False),
        sa.Column("repository_id", sa.String(length=200), nullable=False),
        sa.Column("repository_name", sa.String(length=300), nullable=False),
        sa.Column("repository_remote_url", sa.String(length=1000), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("source_branch", sa.String(length=500), nullable=False),
        sa.Column("target_branch", sa.String(length=500), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("is_draft", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_by", sa.String(length=300), nullable=True),
        sa.Column("external_url", sa.String(length=1000), nullable=False),
        sa.Column("raw", JSONColumn, nullable=False),
        sa.Column("external_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["source_id"], ["work_sources.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("source_id", "external_id", name="uq_work_prs_source_external"),
    )

    op.create_table(
        "work_pr_threads",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("pull_request_id", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("is_resolved", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("file_path", sa.String(length=1000), nullable=True),
        sa.Column("right_file_line", sa.Integer(), nullable=True),
        sa.Column("comments", JSONColumn, nullable=False),
        sa.Column("raw", JSONColumn, nullable=False),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["pull_request_id"], ["work_pull_requests.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "pull_request_id", "external_id", name="uq_work_pr_threads_pr_external"
        ),
    )

    op.create_table(
        "work_repo_paths",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("remote_url", sa.String(length=1000), nullable=False),
        sa.Column("local_path", sa.String(length=1000), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("remote_url", name="uq_work_repo_paths_remote"),
    )


def downgrade() -> None:
    op.drop_table("work_pr_threads")
    op.drop_table("work_pull_requests")
    op.drop_table("work_repo_paths")
