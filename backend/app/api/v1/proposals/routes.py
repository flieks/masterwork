"""Proposal endpoints: accept (apply) or reject a pending proposal."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_providers
from app.api.v1.chat.schemas import Proposal
from app.api.v1.proposals import service
from app.providers.base import Provider

router = APIRouter(tags=["proposals"])


@router.post(
    "/proposals/{proposal_id}/accept",
    response_model=Proposal,
    operation_id="acceptProposal",
)
async def accept_proposal(
    proposal_id: str,
    db: AsyncSession = Depends(get_db),
    providers: list[Provider] = Depends(get_providers),
) -> Proposal:
    return await service.accept_proposal(db, providers, proposal_id)


@router.post(
    "/proposals/{proposal_id}/reject",
    response_model=Proposal,
    operation_id="rejectProposal",
)
async def reject_proposal(
    proposal_id: str,
    db: AsyncSession = Depends(get_db),
) -> Proposal:
    return await service.reject_proposal(db, proposal_id)
