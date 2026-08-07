"""Portable column types.

The app runs on SQLite (zero-setup default) and Postgres (opt-in). JSON is the
only place the two dialects diverge enough to matter, so it gets one shared
definition rather than a dialect import at every use site.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Text, TypeDecorator
from sqlalchemy.dialects.postgresql import JSONB

# JSONB where it exists, plain JSON everywhere else.
JSONColumn = JSON().with_variant(JSONB(astext_type=Text()), "postgresql")


class UTCDateTime(TypeDecorator[datetime]):
    """A timestamp that is always tz-aware UTC on the way out.

    SQLite has no native timestamptz, so it hands back naive datetimes where
    Postgres hands back aware ones. Without this, any comparison between a
    stored timestamp and a freshly built `datetime.now(tz=UTC)` raises
    "can't compare offset-naive and offset-aware datetimes" on SQLite only.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
