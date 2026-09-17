"""chat_sessions.agent: which agent CLI owns the stored session id.

Revision ID: 0031_chat_session_agent
Revises: 0030_installed_skill_drift
Create Date: 2026-09-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0031_chat_session_agent"
down_revision: str | None = "0030_installed_skill_drift"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("chat_sessions", sa.Column("agent", sa.String(length=20), nullable=True))
    # Every session id stored before this revision came from `claude -p`.
    op.execute("UPDATE chat_sessions SET agent = 'claude' WHERE claude_session_id IS NOT NULL")


def downgrade() -> None:
    op.drop_column("chat_sessions", "agent")
