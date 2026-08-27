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


class IntegrationNotFoundError(DomainError):
    """No observability integration with this id is registered."""

    status_code = 404


class ObservabilityUnavailableError(DomainError):
    """The agent can't be wired up here — not installed, or its config is unreadable."""

    status_code = 409


class ObservabilityIOError(DomainError):
    """The agent's config file could not be read or written."""

    status_code = 500


class ProjectNotFoundError(DomainError):
    status_code = 404


class ProposalNotFoundError(DomainError):
    status_code = 404


class ProposalNotPendingError(DomainError):
    status_code = 409


class DiagramNotFoundError(DomainError):
    status_code = 404


class CodingSessionNotFoundError(DomainError):
    """No Claude Code session with this id has sent an event."""

    status_code = 404


class CodingMediaNotFoundError(DomainError):
    """No image with this id was extracted from that session's events."""

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


class WorkSourceNotFoundError(DomainError):
    status_code = 404


class WorkItemNotFoundError(DomainError):
    status_code = 404


class PullRequestNotFoundError(DomainError):
    status_code = 404


class InvalidRepoPathError(DomainError):
    """A repo path was relative, nonexistent, or not a directory."""

    status_code = 400


class InvalidWorkSourceError(DomainError):
    status_code = 400


class WorkSyncError(DomainError):
    """The DevOps call failed, or the PAT env var named by secret_ref is unset."""

    status_code = 502


class InvalidSettingError(DomainError):
    status_code = 400


class InvalidProjectNameError(DomainError):
    status_code = 400


class ProjectPathOutsideRootError(DomainError):
    status_code = 400


class ProjectExistsError(DomainError):
    """A project folder with this name already exists under projects_root."""

    status_code = 409


class ProjectCreationError(DomainError):
    """`git init` failed for a just-created project folder; the folder is
    removed before this is raised, so a retry sees a clean projects_root."""

    status_code = 502


class LaunchFailedError(DomainError):
    """The factory subprocess could not be spawned."""

    status_code = 502


class LaunchNotFoundError(DomainError):
    status_code = 404


class InterviewNotWaitingError(DomainError):
    """The launch isn't currently paused for answers — also the double-submit guard."""

    status_code = 409


class InterviewAnswerMismatchError(DomainError):
    """The submitted answers don't match the recorded questions one-for-one, or one is blank."""

    status_code = 400


class InvalidBrowsePathError(DomainError):
    """A browse path was relative, nonexistent, not a directory, or unreadable."""

    status_code = 400


class RunNotFoundError(DomainError):
    """No run.json for that run id under the project's runs root."""

    status_code = 404


class RunNotResumableError(DomainError):
    """The run is still running, or completed accepted — nothing to resume."""

    status_code = 409


class SkillCatalogError(DomainError):
    """Both skills.sh and GitHub search failed — a single source failing degrades
    to a partial result instead, see app/services/skill_catalog.py."""

    status_code = 502


class SkillNotFoundError(DomainError):
    """No SKILL.md at any resolved path candidate for this owner/repo/skill."""

    status_code = 404


class SkillFetchError(DomainError):
    """The GitHub contents fetch failed, or tripped the size/entry/escape guard."""

    status_code = 502


class GitHubRateLimitError(DomainError):
    """GitHub's hourly quota is spent. Anonymous requests get 60/h, which one
    catalog browse can exhaust — the message names the token as the fix."""

    status_code = 429


class InvalidSkillNameError(DomainError):
    """Not a plain lowercase-kebab slug."""

    status_code = 400


class SkillAlreadyInstalledError(DomainError):
    """A directory already exists at this slug and overwrite was not passed."""

    status_code = 409


class SkillLicenseRefusedError(DomainError):
    """anthropics/skills' docx/pdf/pptx/xlsx carry an all-rights-reserved
    license that forbids extraction — refused before any fetch."""

    status_code = 403


class InstalledSkillNotFoundError(DomainError):
    """No installed_skills row for this name — never delete a directory
    masterwork did not itself install."""

    status_code = 404
