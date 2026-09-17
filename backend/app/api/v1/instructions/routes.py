"""Global instructions endpoints: read and edit an agent's own instructions file
(`~/.claude/CLAUDE.md` or `~/.codex/AGENTS.md`)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_instructions_paths
from app.api.v1.instructions import service
from app.api.v1.instructions.schemas import InstructionsDoc, InstructionsUpdateRequest
from app.api.v1.instructions.service import InstructionsPaths
from app.services.agent_cli import AgentId

router = APIRouter(tags=["instructions"])

_AGENT_DESCRIPTION = "Whose instructions file; defaults to claude."


@router.get("/instructions", response_model=InstructionsDoc, operation_id="getInstructions")
async def get_instructions(
    agent: AgentId = Query(AgentId.CLAUDE, description=_AGENT_DESCRIPTION),
    paths: InstructionsPaths = Depends(get_instructions_paths),
) -> InstructionsDoc:
    return service.read_instructions(paths, agent)


@router.put("/instructions", response_model=InstructionsDoc, operation_id="updateInstructions")
async def update_instructions(
    body: InstructionsUpdateRequest,
    agent: AgentId = Query(AgentId.CLAUDE, description=_AGENT_DESCRIPTION),
    paths: InstructionsPaths = Depends(get_instructions_paths),
) -> InstructionsDoc:
    return service.write_instructions(paths, agent, body.content)
