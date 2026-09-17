"""App settings API schemas."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.services.agent_cli import AgentId


class AgentInfo(BaseModel):
    id: AgentId
    label: str = Field(..., description='"Claude Code" or "Codex".')
    installed: bool = Field(..., description="True when its CLI was found on this machine.")
    bin_path: str | None = Field(None, description="Absolute path of the CLI found, else null.")


class AppSettings(BaseModel):
    projects_root: str = Field(..., description="Absolute folder all code projects live under.")
    assistant_agent: AgentId = Field(
        ...,
        description="The agent every assistant feature runs on: the stored choice, else the "
        "first installed of claude, codex, else claude.",
    )
    agents: list[AgentInfo] = Field(..., description="Every supported agent, claude first.")


class AppSettingsUpdateRequest(BaseModel):
    projects_root: str | None = Field(
        None, description="New projects root; omitted or null leaves it unchanged."
    )
    assistant_agent: AgentId | None = Field(
        None, description="Agent to run assistant features on; must be installed. Null leaves it."
    )
