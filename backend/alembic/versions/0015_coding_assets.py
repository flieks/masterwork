"""asset attribution per run, plus title provenance and parent linking

Revision ID: 0015_coding_assets
Revises: 0014_coding_run_model
Create Date: 2026-08-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015_coding_assets"
down_revision: str | None = "0014_coding_run_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Plain columns, no batch mode: `parent_session_id` is deliberately not a
    # foreign key. Making it one would force SQLite to rebuild `coding_sessions`
    # while three child tables reference it, and the link is soft anyway — an
    # unresolvable parent just means the child is shown as a root.
    op.add_column("coding_sessions", sa.Column("title_source", sa.String(length=20), nullable=True))
    op.add_column(
        "coding_sessions", sa.Column("parent_session_id", sa.String(length=200), nullable=True)
    )
    op.create_index(
        "ix_coding_sessions_parent_session_id", "coding_sessions", ["parent_session_id"]
    )

    op.create_table(
        "coding_assets",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("lane", sa.String(length=100), nullable=True),
        sa.Column("uses", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["session_id"], ["coding_sessions.id"], ondelete="CASCADE"),
        # The upsert key. `lane` is nullable, so this does not stop two null-lane
        # rows in either dialect — the upsert matches on `lane IS NULL` itself.
        sa.UniqueConstraint(
            "session_id", "kind", "name", "lane", name="uq_coding_assets_session_kind_name_lane"
        ),
    )
    # The cross-session rollup groups on (kind, name) and filters on last_seen_at.
    op.create_index("ix_coding_assets_kind_name", "coding_assets", ["kind", "name"])
    op.create_index("ix_coding_assets_last_seen_at", "coding_assets", ["last_seen_at"])


def downgrade() -> None:
    op.drop_index("ix_coding_assets_last_seen_at", table_name="coding_assets")
    op.drop_index("ix_coding_assets_kind_name", table_name="coding_assets")
    op.drop_table("coding_assets")
    op.drop_index("ix_coding_sessions_parent_session_id", table_name="coding_sessions")
    op.drop_column("coding_sessions", "parent_session_id")
    op.drop_column("coding_sessions", "title_source")
