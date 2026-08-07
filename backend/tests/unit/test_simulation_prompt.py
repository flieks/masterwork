"""The simulation prompt must keep the anti-overfitting rubric intact.

These clauses stop the grader/suggester from baking one scenario's product logic
into shared, reusable assets — pinned so a future prompt refactor can't silently
drop them. See build_prompt in app/api/v1/simulations/service.py.
"""

from __future__ import annotations

from app.api.v1.simulations.service import PreviousRun, build_prompt
from app.db.models.project import Project


def _prompt(previous: PreviousRun | None = None, *, control_run: bool = False) -> str:
    project = Project(name="p", goal="ship it", flow_mermaid=None, asset_ids=[])
    return build_prompt(project, [], "run the scenario", previous, control_run=control_run)


def _previous(score: int = 95) -> PreviousRun:
    return PreviousRun(
        score=score,
        checklist=[{"id": "deploys", "title": "deploys to prod", "weight": 3}],
        applied_suggestions=["Sharpen the deploy trigger"],
    )


def test_prompt_grades_scenario_product_logic_as_na() -> None:
    prompt = _prompt()
    # scenario-specific product logic is out of a reusable toolkit's scope → `na`
    assert "PRODUCT logic" in prompt
    assert "hardcode THIS scenario's domain" in prompt
    assert "grade it `na`, not `partial`/`fail`" in prompt


def test_prompt_carries_generality_guard() -> None:
    prompt = _prompt()
    # suggestions edit shared assets, so every change must stay scenario-agnostic
    assert "GENERALITY GUARD" in prompt
    assert "scenario-agnostic" in prompt
    assert "propose THAT general pattern" in prompt


def test_prompt_prefers_linking_existing_assets_over_creating() -> None:
    prompt = _prompt()
    assert "AVAILABLE BUT UNLINKED" in prompt
    assert "LINK BEFORE CREATE" in prompt
    assert '"action": "link"' in prompt


def test_memory_block_pins_the_previous_checklist() -> None:
    prompt = _prompt(_previous())
    assert "MEMORY OF THE PREVIOUS RUN (same scenario) — score 95" in prompt
    assert "Reuse THIS checklist" in prompt
    assert "[deploys]" in prompt
    assert "Sharpen the deploy trigger" in prompt


def test_control_run_withholds_the_previous_checklist() -> None:
    # The whole point: no inherited rubric, and no anchor to the previous score.
    prompt = _prompt(_previous(100), control_run=True)
    assert "CONTROL RUN" in prompt
    assert "build the capability checklist from scratch" in prompt.lower()
    assert "MEMORY OF THE PREVIOUS RUN" not in prompt
    assert "[deploys]" not in prompt
    assert "score 100" not in prompt


def test_prompt_carries_shared_asset_rule() -> None:
    # The rule is always present; the per-asset "SHARED with:" annotation is
    # covered by the integration test with a real second project.
    prompt = _prompt()
    assert "SHARED-ASSET RULE" in prompt
    assert "ADDITIVE" in prompt
    assert "reproduce ALL existing content faithfully" in prompt
