"""Observability setup endpoints: which agents can report their sessions here,
and one call each to wire one up or unwire it.

The Sessions screen calls these so a new install needs no terminal step. Writes
land in the user's own agent config, so they only ever happen on an explicit
request — never on startup, never as a side effect of reading status.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_integrations
from app.api.v1.observability import service
from app.api.v1.observability.schemas import ObservabilityIntegration
from app.observability.base import Integration

router = APIRouter(tags=["observability"])


@router.get(
    "/observability/integrations",
    response_model=list[ObservabilityIntegration],
    operation_id="listObservabilityIntegrations",
    summary="Every coding agent masterwork can record, and whether it is recording",
)
async def list_integrations(
    integrations: list[Integration] = Depends(get_integrations),
) -> list[ObservabilityIntegration]:
    return service.list_integrations(integrations)


@router.post(
    "/observability/integrations/{integration_id}/connect",
    response_model=ObservabilityIntegration,
    operation_id="connectObservabilityIntegration",
    summary="Install (or repair) the agent's hooks so its sessions land here",
)
async def connect(
    integration_id: str,
    integrations: list[Integration] = Depends(get_integrations),
) -> ObservabilityIntegration:
    """Idempotent. Backs the agent's config up before writing, and touches only
    the entries that run masterwork's forwarder."""
    return service.connect(integrations, integration_id)


@router.post(
    "/observability/integrations/{integration_id}/disconnect",
    response_model=ObservabilityIntegration,
    operation_id="disconnectObservabilityIntegration",
    summary="Remove the agent's hooks; recorded sessions are kept",
)
async def disconnect(
    integration_id: str,
    integrations: list[Integration] = Depends(get_integrations),
) -> ObservabilityIntegration:
    return service.disconnect(integrations, integration_id)
