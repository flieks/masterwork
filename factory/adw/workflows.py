"""Which stages run, in what order — the shape of one run, declared not hard-coded.

A workflow is just an ordered list of stage names. `full` is the chain the runner
has always run; the other presets are the same machinery with fewer stages, so a
doc-only or a build+test run costs what it is worth. A shape that could not honour
its own correction loops (`checks` with no builder to fix them) or that reads
backwards (`review` before `build`) is refused at config load, before any agent runs.
"""

from __future__ import annotations

from adw.roles import ROLES

# The one stage the runner executes itself: never an agent, never a role file.
CHECKS = "checks"

# Every name a workflow may use: the agent roles plus `checks`.
KNOWN_STAGES: tuple[str, ...] = (*ROLES, CHECKS)

# `checks` sits between build and review because a reviewer should never be the
# first thing to learn the suite is red.
FULL: tuple[str, ...] = ("plan", "build", CHECKS, "review", "document")

PRESETS: dict[str, tuple[str, ...]] = {
    "full": FULL,
    "plan_build": ("plan", "build"),
    "build_test": ("build", CHECKS),
    "build_review": ("build", "review"),
    "document": ("document",),
    "scout": ("scout",),
}
DEFAULT_PRESET = "full"
CUSTOM = "custom"  # what a workflow given as an explicit list is called

# A stage that cannot run at all without another one BEFORE it. Both entries are
# the same fact: a failing check and a rejected review are both corrected by the
# builder, so a workflow holding either without a `build` stage could not honour
# the loop it promises.
_NEEDS: dict[str, tuple[str, str]] = {
    CHECKS: ("build", "a failing check is handed back to the builder to fix"),
    "review": ("build", "a rejected review is handed back to the builder to fix"),
}

# Ordering that is only wrong when both stages are present — you cannot implement
# a plan before it is written, or document code before it exists.
_AFTER: dict[str, tuple[str, ...]] = {
    "build": ("plan",),
    CHECKS: ("build",),
    "review": ("build",),
    "document": ("build",),
}


class WorkflowError(Exception):
    """The workflow names an unknown stage, or an order that cannot run."""


def preset_names() -> str:
    return ", ".join(sorted(PRESETS))


def describe(stages: tuple[str, ...]) -> str:
    return " → ".join(stages)


def resolve(spec: object, *, source: str) -> tuple[str, tuple[str, ...]]:
    """`(name, stages)` from a preset name, an explicit list, or None for the default."""
    if spec is None:
        return DEFAULT_PRESET, PRESETS[DEFAULT_PRESET]
    if isinstance(spec, str):
        name = spec.strip()
        if name not in PRESETS:
            raise WorkflowError(
                f'{source}: unknown workflow "{name}" — known presets are {preset_names()}, '
                "or give an explicit list of stages"
            )
        return name, PRESETS[name]
    if isinstance(spec, list) and all(isinstance(item, str) for item in spec):
        stages = tuple(item.strip() for item in spec)
        validate(stages, source=source)
        return CUSTOM, stages
    raise WorkflowError(
        f'{source}: a workflow must be a preset name ({preset_names()}) or a list of stage names'
    )


def validate(stages: tuple[str, ...], *, source: str) -> None:
    """Everything wrong with a stage list, said before a single agent is launched."""
    if not stages:
        raise WorkflowError(f"{source}: the workflow is empty — a run needs at least one stage")

    seen: list[str] = []
    for stage in stages:
        if stage not in KNOWN_STAGES:
            raise WorkflowError(
                f'{source}: unknown stage "{stage}" — known stages are '
                f"{', '.join(sorted(KNOWN_STAGES))}"
            )
        if stage in seen:
            raise WorkflowError(
                f'{source}: stage "{stage}" appears more than once — each stage runs at '
                "most once per workflow"
            )
        seen.append(stage)

    for index, stage in enumerate(stages):
        before = stages[:index]
        # Order first: a stage that IS there but later is a swapped list, not a gap.
        for earlier in _AFTER.get(stage, ()):
            if earlier in stages and earlier not in before:
                raise WorkflowError(
                    f'{source}: "{stage}" comes before "{earlier}" in {describe(stages)} — '
                    f'"{stage}" can only run once "{earlier}" has'
                )
        needed = _NEEDS.get(stage)
        if needed is not None and needed[0] not in before:
            raise WorkflowError(
                f'{source}: "{stage}" needs a "{needed[0]}" stage before it — {needed[1]}, '
                f"and {describe(stages)} has none"
            )
