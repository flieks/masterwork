"""Global instructions endpoints: read and edit `~/.claude/CLAUDE.md`."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends

from app.api.deps import get_instructions_path
from app.api.v1.instructions import service
from app.api.v1.instructions.schemas import InstructionsDoc, InstructionsUpdateRequest

router = APIRouter(tags=["instructions"])


@router.get("/instructions", response_model=InstructionsDoc, operation_id="getInstructions")
async def get_instructions(path: Path = Depends(get_instructions_path)) -> InstructionsDoc:
    return service.read_instructions(path)


@router.put("/instructions", response_model=InstructionsDoc, operation_id="updateInstructions")
async def update_instructions(
    body: InstructionsUpdateRequest,
    path: Path = Depends(get_instructions_path),
) -> InstructionsDoc:
    return service.write_instructions(path, body.content)
