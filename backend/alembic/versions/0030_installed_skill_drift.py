"""installed_skills: the upstream baseline an install records, and the cached
result of the last drift check.

Revision ID: 0030_installed_skill_drift
Revises: 0029_installed_skills
Create Date: 2026-09-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0030_installed_skill_drift"
down_revision: str | None = "0029_installed_skills"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    sa.Column("installed_sha", sa.String(length=64), nullable=True),
    sa.Column("installed_tree_hash", sa.String(length=64), nullable=True),
    sa.Column("root_path", sa.String(length=500), nullable=True),
    sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("upstream_sha", sa.String(length=64), nullable=True),
    sa.Column("drift_status", sa.String(length=30), nullable=True),
)


def upgrade() -> None:
    for column in _COLUMNS:
        op.add_column("installed_skills", column)


def downgrade() -> None:
    for column in reversed(_COLUMNS):
        op.drop_column("installed_skills", column.name)
