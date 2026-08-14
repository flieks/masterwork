"""Data access for the app_settings key-value table."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.app_settings import AppSetting


async def get_value(db: AsyncSession, key: str) -> str | None:
    setting = await db.get(AppSetting, key)
    return setting.value if setting else None


async def set_value(db: AsyncSession, key: str, value: str) -> AppSetting:
    """Select-then-insert-or-update, like work.upsert_item — runs identically on
    both dialects."""
    setting = await db.get(AppSetting, key)
    if setting is None:
        setting = AppSetting(key=key, value=value)
        db.add(setting)
    else:
        setting.value = value
    await db.flush()
    return setting
