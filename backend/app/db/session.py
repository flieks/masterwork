"""Async engine and session factory.

Supports SQLite (the zero-setup default) and Postgres. SQLite needs three
pragmas the app genuinely depends on, none of which are on by default:
foreign keys (for ON DELETE CASCADE), WAL (so a long simulation write doesn't
block reads), and a busy timeout (so concurrent writers wait instead of raising
"database is locked").
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings


def make_engine(url: str, **kwargs: Any) -> Any:
    """Build an async engine with the right per-dialect setup.

    Shared with the test fixtures — the SQLite pragmas are not optional (cascade
    deletes silently no-op without them), so they must not live only in the
    app's own engine.
    """
    if not url.startswith("sqlite"):
        return create_async_engine(url, pool_pre_ping=True, **kwargs)

    # sqlite:///path → make sure the parent directory exists first.
    path = url.split("///", 1)[-1]
    if path and path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    eng = create_async_engine(url, **kwargs)

    @event.listens_for(eng.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")  # ON DELETE CASCADE is a no-op without this
        cur.execute("PRAGMA journal_mode=WAL")  # a long write must not block reads
        cur.execute("PRAGMA busy_timeout=5000")  # wait rather than "database is locked"
        cur.close()

    return eng


engine = make_engine(settings.database_url)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        yield session
