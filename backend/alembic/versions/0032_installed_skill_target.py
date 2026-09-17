"""installed_skills.target: which skills folder a catalog install was written to.

Revision ID: 0032_installed_skill_target
Revises: 0031_chat_session_agent
Create Date: 2026-09-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0032_installed_skill_target"
down_revision: str | None = "0031_chat_session_agent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Every install before this revision went to ~/.claude/skills.
    op.add_column(
        "installed_skills",
        sa.Column("target", sa.String(length=20), nullable=False, server_default="claude"),
    )


def downgrade() -> None:
    op.drop_column("installed_skills", "target")
