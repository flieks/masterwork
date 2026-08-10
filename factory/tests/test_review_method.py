"""The two-axis review METHOD, lifted from the user's `code-review` skill.

That skill reviews a diff on two axes — Standards (does this follow the repo's own
documented rules?) and Spec (does this do what was asked?) — and runs them as two
parallel sub-agents so neither pollutes the other's context. The review role can
never run it: it spawns sub-agents over `git diff`, and every role disallows `Task`
and `Bash` on purpose, because a sub-agent is work the runner cannot gate, budget or
see. So the method was carried into the role's identity and the machinery was left
behind. What one agent with no shell can still do is ask the two questions
separately, from the two different authorities, and keep them separate all the way
into the envelope — which is what these tests pin.
"""

from __future__ import annotations

import re
from pathlib import Path

from adw import prompts
from adw.config import load_config
from adw.envelopes import Envelope

REQUEST = "Add a /health endpoint"


def compiled(repo: Path) -> prompts.CompiledPrompt:
    cfg = load_config(repo)
    return prompts.compile_prompt(
        role=cfg.roles["review"], stage=cfg.stages["review"], request=REQUEST, repo=repo
    )


def flat(text: str) -> str:
    """Prompt prose is hand-wrapped; where a sentence breaks is not the contract."""
    return " ".join(text.split())


def system(repo: Path) -> str:
    return flat(compiled(repo).system)


def test_the_two_axes_are_asked_as_two_separate_questions(tmp_path: Path):
    """Standards and Spec fail differently — code can follow every rule and implement
    the wrong thing, or do exactly what was asked in a style the repo forbids. A
    reviewer that merges them reports whichever it happened to notice first."""
    text = system(tmp_path)
    assert "STANDARDS —" in text and "SPEC —" in text
    assert "two axes, answered as two separate questions" in text


def test_the_standards_axis_is_told_what_it_may_judge_from(tmp_path: Path):
    """Its authority is documented rules, not the reviewer's taste — otherwise every
    run pays a correction round for a preference nobody wrote down."""
    text = system(tmp_path)
    assert "Its authority is the shared conventions you are shown" in text
    assert "the style of the code already around the change — never your own taste" in text


def test_the_standards_axis_has_a_floor_for_a_repo_that_documents_nothing(tmp_path: Path):
    """The skill's fallback is Fowler's smell catalogue. Only the names travel here —
    the definitions and fixes are ~400 words the reviewing model does not need — and
    both rules that bind it come with them: the repo overrides, and tooling wins."""
    text = system(tmp_path)
    assert "fall back to the classic smells" in text
    for smell in ("duplicated logic", "mysterious names", "speculative generality"):
        assert smell in text, f"the smell floor does not name {smell}"
    assert "A documented repo rule always beats that fallback" in text
    assert "linter, formatter or type checker enforces is never your finding" in text


def test_the_spec_axis_is_told_what_it_may_judge_from(tmp_path: Path):
    """There is no issue tracker to fetch, so the request and `plan.md` are the whole
    of the authority — and a plan that drifted from the request does not get to win."""
    text = system(tmp_path)
    assert "Its authority is the original request, plus `plan.md` if a planner wrote one" in text
    assert "where those two disagree the request wins" in text
    assert "there is nothing else to appeal to" in text


def test_the_spec_axis_names_the_three_shapes_a_spec_failure_takes(tmp_path: Path):
    """Missing, unasked-for, and wrongly-implemented. The third is the one a reviewer
    skips when it only checks the request off item by item."""
    text = system(tmp_path)
    assert "something asked for that is missing or half-done" in text
    assert "behaviour nobody asked for" in text
    assert "looks implemented but is implemented wrongly" in text
    assert "Quote the words you are holding the code to" in text


def test_each_axis_gets_its_own_verdict_even_when_it_passes(tmp_path: Path):
    """One verdict for both axes is how a Spec failure disappears behind a clean
    Standards read. A passing axis is reported out loud, not by omission."""
    text = system(tmp_path)
    assert "Work can pass one axis and fail the other" in text
    assert "`summary` gives a verdict for EACH, including the one that passed" in text


def test_every_blocking_entry_opens_with_the_axis_it_came_from(tmp_path: Path):
    """`blocking` is one flat list, so the separation would end there. The prefix is
    the whole of what survives into the correction the builder is sent."""
    text = system(tmp_path)
    assert "opening with the axis it came from — `Standards:` or `Spec:`" in text

    correction = prompts.review_correction(
        ["Spec: app.py returns no `status` key, which the request asked for"], 1, 3
    )
    assert "Spec: app.py returns no `status` key" in correction


def test_a_finding_not_worth_a_build_round_is_kept_out_of_blocking(tmp_path: Path):
    """The skill's discipline is: never approve over a finding, downgrade it
    explicitly. Here the downgrade has exactly one destination — `summary` — because
    `blocking` is not a severity bucket, it is the trigger for another build round."""
    text = system(tmp_path)
    assert "a judgement call, a nit, a preference — goes in `summary` and never in `blocking`" in (
        text
    )
    assert "every blocking entry costs the run another build round" in text
    assert "Set `approved: true` only when `blocking` is empty" in text


def test_the_method_invents_no_vocabulary_the_envelope_cannot_carry(tmp_path: Path):
    """The skill's machine-readable block has `important`, `nits` and `fixed_point`.
    This envelope has `approved` + `blocking`, and a field the runner never reads is a
    finding the run never sees — so every bare field name the prompt uses is a real one."""
    text = flat(compiled(tmp_path).combined)
    for invented in ("`important`", "`nits`", "fixed_point"):
        assert invented not in text
    named = set(re.findall(r"`([a-z_]+)`", text))
    assert named <= set(Envelope.__dataclass_fields__), (
        f"the review prompt names {named - set(Envelope.__dataclass_fields__)}, "
        "which is not an envelope field"
    )


def test_the_method_rides_the_system_prompt_not_the_task(tmp_path: Path):
    """`system.md` is re-sent on every `--resume`; the user message is not. A review
    corrected mid-turn must not lose the two axes it is meant to be answering."""
    prompt = compiled(tmp_path)
    assert "STANDARDS —" in flat(prompt.system)
    assert "STANDARDS —" not in flat(prompt.user)
