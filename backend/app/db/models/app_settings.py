"""Persisted app settings — one row per key.

A key-value table, not a column-per-setting, so a new setting never needs a
migration.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import UTCDateTime

PROJECTS_ROOT_KEY = "projects_root"


class AppSetting(Base):
    """One key/value pair, upserted through app/repositories/app_settings.py."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now()
    )
