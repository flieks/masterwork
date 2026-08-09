"""Look up an integration by id and hand back its status as a schema.

All the real work — reading and editing the agent's config — lives in
`app.observability`; this layer only resolves the id and converts the result.
"""

from __future__ import annotations

from dataclasses import asdict

from app.api.v1.observability.schemas import ObservabilityIntegration
from app.core.exceptions import IntegrationNotFoundError
from app.observability.base import Integration, IntegrationStatus


def _serialize(status: IntegrationStatus) -> ObservabilityIntegration:
    return ObservabilityIntegration(**asdict(status))


def _find(integrations: list[Integration], integration_id: str) -> Integration:
    for integration in integrations:
        if integration.id == integration_id:
            return integration
    raise IntegrationNotFoundError(f"unknown integration: {integration_id}")


def list_integrations(integrations: list[Integration]) -> list[ObservabilityIntegration]:
    return [_serialize(integration.status()) for integration in integrations]


def connect(integrations: list[Integration], integration_id: str) -> ObservabilityIntegration:
    return _serialize(_find(integrations, integration_id).connect())


def disconnect(integrations: list[Integration], integration_id: str) -> ObservabilityIntegration:
    return _serialize(_find(integrations, integration_id).disconnect())
