"""App settings business logic: read with the defaults filled in, validate on
write.

`projects_root` is expanded and validated here so every caller of
`read_projects_root` gets back an absolute, existing directory — never a raw `~`
or a path nothing has checked. `assistant_agent` is always read back as the
effective agent, so no caller has to repeat the fallback.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.settings.schemas import AgentInfo, AppSettings, AppSettingsUpdateRequest
from app.config import settings
from app.core.exceptions import InvalidSettingError
from app.db.models.app_settings import ASSISTANT_AGENT_KEY, PROJECTS_ROOT_KEY
from app.repositories import app_settings as app_settings_repo
from app.services.agent_cli import (
    AGENT_LABELS,
    AGENT_ORDER,
    AgentBins,
    AgentId,
    configured_bin,
    effective_agent,
)


async def read_projects_root(db: AsyncSession) -> str:
    stored = await app_settings_repo.get_value(db, PROJECTS_ROOT_KEY)
    return stored or str(settings.default_projects_root)


async def read_assistant_agent(db: AsyncSession, bins: AgentBins) -> AgentId:
    return effective_agent(await app_settings_repo.get_value(db, ASSISTANT_AGENT_KEY), bins)


def agent_infos(bins: AgentBins) -> list[AgentInfo]:
    return [
        AgentInfo(
            id=agent,
            label=AGENT_LABELS[agent],
            installed=bins.get(agent) is not None,
            bin_path=bins.get(agent),
        )
        for agent in AGENT_ORDER
    ]


async def read_settings(db: AsyncSession, bins: AgentBins) -> AppSettings:
    return AppSettings(
        projects_root=await read_projects_root(db),
        assistant_agent=await read_assistant_agent(db, bins),
        agents=agent_infos(bins),
    )


def _validate_projects_root(value: str) -> Path:
    expanded = Path(value).expanduser()
    if not expanded.is_absolute():
        raise InvalidSettingError(f"projects_root must be an absolute path, got: {value}")
    if not expanded.is_dir():
        raise InvalidSettingError(f"projects_root does not exist or is not a directory: {expanded}")
    return expanded


def _validate_assistant_agent(agent: AgentId, bins: AgentBins) -> AgentId:
    # Picking an agent whose CLI is missing would fail every assistant call.
    if bins.get(agent) is None:
        raise InvalidSettingError(
            f"{AGENT_LABELS[agent]} is not installed: no '{configured_bin(agent, settings)}' CLI "
            "was found on the backend's PATH or in its usual install locations"
        )
    return agent


async def update_settings(
    db: AsyncSession, body: AppSettingsUpdateRequest, bins: AgentBins
) -> AppSettings:
    # Validate everything before writing anything, so a bad field changes nothing.
    root = _validate_projects_root(body.projects_root) if body.projects_root is not None else None
    agent = (
        _validate_assistant_agent(body.assistant_agent, bins)
        if body.assistant_agent is not None
        else None
    )
    if root is not None:
        await app_settings_repo.set_value(db, PROJECTS_ROOT_KEY, str(root))
    if agent is not None:
        await app_settings_repo.set_value(db, ASSISTANT_AGENT_KEY, agent.value)
    if root is not None or agent is not None:
        await db.commit()
    return await read_settings(db, bins)
