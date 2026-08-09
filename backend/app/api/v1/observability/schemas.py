"""Observability-integration API schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ObservabilityIntegration(BaseModel):
    """One coding agent and whether it is reporting its sessions here."""

    id: str = Field(..., description='Stable integration id, e.g. "claude-code".')
    label: str = Field(..., description="Agent name to show in the UI.")
    state: Literal["connected", "outdated", "disconnected", "unavailable"] = Field(
        ...,
        description=(
            "connected: recording. outdated: wired to an older hook set or a missing "
            "script — connect repairs it. disconnected: nothing installed. unavailable: "
            "cannot be connected on this machine; `detail` says why."
        ),
    )
    detail: str = Field(..., description="One sentence for the user explaining the state.")
    ingest_url: str = Field(..., description="Where the agent's hooks post their events.")
    events: list[str] = Field(..., description="The agent events subscribed once connected.")
    config_path: str | None = Field(None, description="The agent config file that is edited.")
    script_path: str | None = Field(
        None, description="Where the forwarder script is installed on disk."
    )
    backup_path: str | None = Field(
        None, description="Backup of the agent config taken before the last write, if any."
    )
