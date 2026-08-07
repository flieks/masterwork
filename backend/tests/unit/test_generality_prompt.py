"""The generality-audit prompt must keep its leak-detection rubric intact.

These clauses are what let the audit tell a real leak (one domain colonizing a
shared asset) apart from a legitimate list of example domains — pinned so a
future prompt refactor can't silently drop them. See build_generality_prompt in
app/api/v1/projects/generality_service.py.
"""

from __future__ import annotations

from app.api.v1.projects.generality_service import build_generality_prompt
from app.db.models.project import Project


def _prompt() -> str:
    project = Project(name="p", goal="ship it", flow_mermaid=None, asset_ids=[])
    return build_generality_prompt(project, [])


def test_prompt_flags_product_logic_as_severe() -> None:
    assert "SEVERE LEAK: actual PRODUCT logic baked in" in _prompt()


def test_prompt_distinguishes_running_identifier_from_example_list() -> None:
    # the core rubric: one colonizing identifier = leak; a list of examples = fine
    assert "single running identifier is a leak; a list of examples is not" in _prompt()


def test_prompt_asks_for_a_concrete_generalization() -> None:
    assert "neutral placeholder" in _prompt()


def test_prompt_emits_a_generality_block() -> None:
    assert "info string is `generality`" in _prompt()
