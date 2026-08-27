"""installed_skills: skills masterwork itself installed from the community
catalog, so uninstall never deletes a directory it did not write.

Revision ID: 0029_installed_skills
Revises: 0028_work_pull_requests
Create Date: 2026-08-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0029_installed_skills"
down_revision: str | None = "0028_work_pull_requests"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "installed_skills",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("owner", sa.String(length=200), nullable=False),
        sa.Column("repo", sa.String(length=200), nullable=False),
        sa.Column("license", sa.String(length=100), nullable=True),
        sa.Column("registry", sa.String(length=50), nullable=False),
        sa.Column(
            "installed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_installed_skills_name"),
    )


def downgrade() -> None:
    op.drop_table("installed_skills")
