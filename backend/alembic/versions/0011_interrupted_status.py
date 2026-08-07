"""interrupted simulation status

A run orphaned by a backend restart, or stopped by the user, is not a failure of
the user's assets. Those were previously indistinguishable from a genuine error.

Revision ID: 0011_interrupted_status
Revises: 0010_chat_asset_scope
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_interrupted_status"
down_revision: str | None = "0010_chat_asset_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

RESTART_ERROR = "the backend restarted while this simulation was running"
STOPPED_ERROR = "autopilot stopped by user"


def upgrade() -> None:
    simulations = sa.table(
        "simulations", sa.column("status", sa.String), sa.column("error", sa.Text)
    )
    op.execute(
        simulations.update()
        .where(simulations.c.status == "failed")
        .where(simulations.c.error.in_([RESTART_ERROR, STOPPED_ERROR]))
        .values(status="interrupted")
    )


def downgrade() -> None:
    simulations = sa.table("simulations", sa.column("status", sa.String))
    op.execute(
        simulations.update().where(simulations.c.status == "interrupted").values(status="failed")
    )
