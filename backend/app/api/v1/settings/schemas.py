"""App settings API schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AppSettings(BaseModel):
    projects_root: str = Field(..., description="Absolute folder all code projects live under.")


class AppSettingsUpdateRequest(BaseModel):
    projects_root: str | None = Field(
        None, description="New projects root; omitted or null leaves it unchanged."
    )
