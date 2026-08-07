"""Simulation API schemas — names and fields match the frozen API contract v1.3."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SimulationChange(BaseModel):
    """One change carried by a suggestion (same shape as ProposalChange).
    `link` adds an existing unlinked asset to the project — no file write."""

    path: str
    action: Literal["update", "create", "delete", "link", "unlink"]
    new_content: str | None
    description: str
    asset_id: str | None = Field(..., description="Set when path maps to a known asset.")


class SimulationStats(BaseModel):
    """Run metadata reported by the claude CLI; every field is best-effort."""

    model: str | None = None
    duration_ms: int | None = None
    num_turns: int | None = None
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None


class SimulationChecklistItem(BaseModel):
    """One capability the toolkit must have for the scenario. The run's score is
    the weighted share of these that pass (partial = half; `na` excluded)."""

    id: str
    title: str
    weight: int = Field(..., ge=1, le=3, description="Importance to the goal, 1-3.")
    status: Literal["pass", "partial", "fail", "na"]
    evidence: str = Field(..., description="Asset/file that covers it, or the gap.")


class SimulationSuggestion(BaseModel):
    title: str
    impact: Literal["high", "medium", "low"]
    rationale: str = Field(..., description="Markdown: why this improves goal achievement.")
    changes: list[SimulationChange]
    status: Literal["pending", "applied", "failed"]
    error: str | None
    applied_at: datetime | None


class Simulation(BaseModel):
    id: str
    project_id: str
    status: Literal["running", "completed", "failed"]
    scenario: str = Field(..., description="User-provided scenario; empty = derived from goal.")
    score: int | None = Field(..., description="0-100, null until completed.")
    verdict: str | None
    summary: str | None = Field(..., description="Markdown: what the simulated run did.")
    analysis: str | None = Field(..., description="Markdown: strengths, gaps, failure points.")
    trace_mermaid: str | None = Field(..., description="Mermaid trace of the simulated run.")
    checklist: list[SimulationChecklistItem] = Field(
        ..., description="Capability checklist the score is computed from; [] for pre-v1.7 runs."
    )
    suggestions: list[SimulationSuggestion]
    control_run: bool = Field(
        ...,
        description="True when this run built its checklist from scratch instead of "
        "re-grading the previous run's; forced automatically after a run scores 100.",
    )
    error: str | None
    created_at: datetime
    completed_at: datetime | None
    autopilot_run_id: str | None = Field(
        ..., description="Set when this run is one iteration of an autopilot chain."
    )
    autopilot_iteration: int | None = Field(..., description="1-based iteration number.")
    autopilot_total: int | None = Field(..., description="Requested iteration count.")
    stats: SimulationStats | None = Field(
        ..., description="CLI-reported run metadata; null for runs before v1.6."
    )


_CONTROL_RUN_DESCRIPTION = (
    "Build the capability checklist from scratch instead of re-grading the previous "
    "run's. Forced anyway when the last run of this scenario scored 100."
)


class SimulationCreateRequest(BaseModel):
    scenario: str = Field(
        "",
        description="Optional scenario to simulate; empty lets the model derive one from the goal.",
    )
    control_run: bool = Field(False, description=_CONTROL_RUN_DESCRIPTION)


class AutopilotCreateRequest(BaseModel):
    scenario: str = Field(
        "",
        description="Optional scenario to simulate; empty lets the model derive one from the goal.",
    )
    control_run: bool = Field(
        False, description=f"{_CONTROL_RUN_DESCRIPTION} Applies to the first iteration only."
    )
    iterations: int = Field(
        5,
        ge=1,
        le=20,
        description="Maximum number of chained runs; stops early when a run has no suggestions. "
        "A run that scores 100 instead gets a freshly generated scenario and keeps going.",
    )


class ScenarioGenerateResponse(BaseModel):
    scenario: str = Field(..., description="The generated scenario, saved on the project.")
