"""Session launcher API schemas."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class LaunchMode(StrEnum):
    AUTONOMOUS = "autonomous"
    INTERVIEW = "interview"


class LauncherProject(BaseModel):
    name: str
    path: str = Field(..., description="Absolute path under projects_root.")
    is_git_repo: bool


class LauncherProjectCreateRequest(BaseModel):
    """Named apart from the unrelated `ProjectCreateRequest` (features/projects) —
    this creates a plain folder under projects_root, not a masterwork Project."""

    name: str = Field(..., description="Folder name — no path separators or traversal.")


class LaunchRequest(BaseModel):
    project_path: str = Field(..., description="Absolute path; must resolve under projects_root.")
    request_text: str = Field(
        ..., min_length=1, description="What to build — handed to the factory as-is."
    )
    mode: LaunchMode = Field(
        LaunchMode.AUTONOMOUS, description="Both modes launch the same unattended run today."
    )


class SessionLaunchRead(BaseModel):
    id: int
    project_path: str
    request_text: str
    mode: LaunchMode
    launched_at: datetime
    pid: int | None
    launched: bool = Field(..., description="True once the subprocess was spawned.")
