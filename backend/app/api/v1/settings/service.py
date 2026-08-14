"""App settings business logic: read with the default filled in, validate on
write.

`projects_root` is expanded and validated here so every caller of
`read_settings` gets back an absolute, existing directory — never a raw `~`
or a path nothing has checked.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.settings.schemas import AppSettings, AppSettingsUpdateRequest
from app.config import settings
from app.core.exceptions import InvalidSettingError
from app.db.models.app_settings import PROJECTS_ROOT_KEY
from app.repositories import app_settings as app_settings_repo


async def read_settings(db: AsyncSession) -> AppSettings:
    stored = await app_settings_repo.get_value(db, PROJECTS_ROOT_KEY)
    return AppSettings(projects_root=stored or str(settings.default_projects_root))


def _validate_projects_root(value: str) -> Path:
    expanded = Path(value).expanduser()
    if not expanded.is_absolute():
        raise InvalidSettingError(f"projects_root must be an absolute path, got: {value}")
    if not expanded.is_dir():
        raise InvalidSettingError(
            f"projects_root does not exist or is not a directory: {expanded}"
        )
    return expanded


async def update_settings(db: AsyncSession, body: AppSettingsUpdateRequest) -> AppSettings:
    if body.projects_root is not None:
        validated = _validate_projects_root(body.projects_root)
        await app_settings_repo.set_value(db, PROJECTS_ROOT_KEY, str(validated))
        await db.commit()
    return await read_settings(db)
