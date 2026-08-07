"""Map Simulation ORM rows to the contract's Pydantic schemas."""

from __future__ import annotations

from typing import Any

from app.api.v1.simulations import schemas
from app.db.models.simulation import Simulation


def _checklist_item_to_schema(raw: dict[str, Any]) -> schemas.SimulationChecklistItem:
    return schemas.SimulationChecklistItem(
        id=raw["id"],
        title=raw["title"],
        weight=raw["weight"],
        status=raw["status"],
        evidence=raw.get("evidence", ""),
    )


def _suggestion_to_schema(raw: dict[str, Any]) -> schemas.SimulationSuggestion:
    return schemas.SimulationSuggestion(
        title=raw["title"],
        impact=raw["impact"],
        rationale=raw["rationale"],
        changes=[schemas.SimulationChange(**change) for change in raw["changes"]],
        status=raw["status"],
        error=raw.get("error"),
        applied_at=raw.get("applied_at"),
    )


def simulation_to_schema(simulation: Simulation) -> schemas.Simulation:
    return schemas.Simulation(
        id=str(simulation.id),
        project_id=str(simulation.project_id),
        status=simulation.status,
        scenario=simulation.scenario,
        score=simulation.score,
        verdict=simulation.verdict,
        summary=simulation.summary,
        analysis=simulation.analysis,
        trace_mermaid=simulation.trace_mermaid,
        checklist=[_checklist_item_to_schema(c) for c in (simulation.checklist or [])],
        suggestions=[_suggestion_to_schema(s) for s in simulation.suggestions],
        control_run=simulation.control_run,
        error=simulation.error,
        created_at=simulation.created_at,
        completed_at=simulation.completed_at,
        autopilot_run_id=(
            str(simulation.autopilot_run_id) if simulation.autopilot_run_id else None
        ),
        autopilot_iteration=simulation.autopilot_iteration,
        autopilot_total=simulation.autopilot_total,
        # Extras in the stored dict are ignored; missing keys fall back to None.
        stats=schemas.SimulationStats.model_validate(simulation.stats)
        if simulation.stats
        else None,
    )
