"""Domain exceptions, translated to HTTP responses by handlers in `app.main`.

Keeping these HTTP-agnostic lets the service layer stay free of FastAPI.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base class carrying an HTTP status and a client-facing detail message."""

    status_code: int = 400

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class InvalidAssetIdError(DomainError):
    status_code = 400


class AssetNotFoundError(DomainError):
    status_code = 404


class ReadOnlyAssetError(DomainError):
    """The asset is provided by a plugin and cannot be edited here."""

    status_code = 403


class InstructionsIOError(DomainError):
    """The global CLAUDE.md could not be read or written."""

    status_code = 500


class SessionNotFoundError(DomainError):
    status_code = 404


class ProjectNotFoundError(DomainError):
    status_code = 404


class ProposalNotFoundError(DomainError):
    status_code = 404


class ProposalNotPendingError(DomainError):
    status_code = 409


class DiagramNotFoundError(DomainError):
    status_code = 404


class SimulationNotFoundError(DomainError):
    status_code = 404


class SimulationRunningError(DomainError):
    """The project already has a simulation in flight."""

    status_code = 409


class SuggestionNotFoundError(DomainError):
    status_code = 404


class SuggestionNotPendingError(DomainError):
    status_code = 409


class AutopilotNotFoundError(DomainError):
    """No autopilot run with this id is currently in flight."""

    status_code = 404


class SummaryGenerationError(DomainError):
    """The claude CLI failed or returned no usable change summary."""

    status_code = 502


class TriggerGenerationError(DomainError):
    """The claude CLI failed or returned no usable trigger guide."""

    status_code = 502


class GeneralityGenerationError(DomainError):
    """The claude CLI failed or returned no usable generality-audit report."""

    status_code = 502


class DiagramGenerationError(DomainError):
    """The claude CLI failed or returned no usable Mermaid diagram."""

    status_code = 502


class ScenarioGenerationError(DomainError):
    """The claude CLI failed or returned no usable simulation scenario."""

    status_code = 502


class LinkSuggestionError(DomainError):
    """The claude CLI failed or returned no usable asset-link suggestions."""

    status_code = 502


class NoLinkedAssetsError(DomainError):
    """A simulation/scenario was requested for a project with no linked assets."""

    status_code = 409
