"""Simulation business logic.

A run is asynchronous: POST creates a `running` row and schedules the claude -p
call as a background task (own DB session — the request session is gone by the
time the CLI returns); the frontend polls the list until it completes. Applying
a suggestion reuses the proposal machinery: paths re-validated against the
writable provider roots, changes written by the backend.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1.simulations import schemas, serializers
from app.core.exceptions import (
    AutopilotNotFoundError,
    NoLinkedAssetsError,
    ProjectNotFoundError,
    ScenarioGenerationError,
    SimulationNotFoundError,
    SimulationRunningError,
    SuggestionNotFoundError,
    SuggestionNotPendingError,
)
from app.db.models.project import Project
from app.db.models.simulation import Simulation
from app.providers.base import Provider, resolve_within_roots
from app.repositories import projects as project_repo
from app.repositories import simulations as simulation_repo
from app.services.asset_history import prepare_snapshots, snapshot_writes
from app.services.claude_runner import ClaudeRunner, ClaudeRunnerError
from app.services.file_changes import apply_change
from app.services.redact import redact
from app.services.scenario_parser import extract_scenario
from app.services.shared_assets import shared_asset_notes
from app.services.simulation_parser import ParsedSimulation, extract_simulation

logger = logging.getLogger(__name__)

RESTART_ERROR = "the backend restarted while this simulation was running"

# Autopilot runs the user asked to stop. In-memory is fine: the loop lives in
# this process, and a restart kills it anyway (startup sweep marks rows failed).
_cancelled_autopilots: set[uuid.UUID] = set()


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _parse_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        return None


async def _get_project_or_404(db: AsyncSession, project_id: str) -> Project:
    parsed = _parse_uuid(project_id)
    project = await project_repo.get_project(db, parsed) if parsed is not None else None
    if project is None:
        raise ProjectNotFoundError(f"unknown project: {project_id}")
    return project


def _require_linked_assets(project: Project) -> None:
    """A run against zero assets grades an undefined toolkit — refuse it."""
    if not project.asset_ids:
        raise NoLinkedAssetsError(
            "no assets linked to this project — link the toolkit first "
            "(Overview → Edit links, or use suggest-links)"
        )


async def _get_simulation_or_404(db: AsyncSession, simulation_id: str) -> Simulation:
    parsed = _parse_uuid(simulation_id)
    simulation = await simulation_repo.get_simulation(db, parsed) if parsed is not None else None
    if simulation is None:
        raise SimulationNotFoundError(f"unknown simulation: {simulation_id}")
    return simulation


def _writable_roots(providers: list[Provider]) -> list[Path]:
    return [root for provider in providers for root in provider.roots()]


def _asset_lines(
    providers: list[Provider],
    asset_ids: list[str],
    shared_notes: dict[str, str] | None = None,
) -> list[str]:
    index = {asset.id: asset for provider in providers for asset in provider.scan()}
    lines: list[str] = []
    for asset_id in asset_ids:
        asset = index.get(asset_id)
        if asset is None:  # linked id no longer on disk — surface it to the model
            lines.append(f"- {asset_id} — MISSING: the linked file no longer exists on disk")
            continue
        note = " (plugin-provided, READ-ONLY)" if asset.read_only else ""
        # redact(): titles/descriptions come straight from the asset file.
        line = f"- {asset_id}{note} — {asset.title}: {asset.description}\n  file: {asset.path}"
        if shared_notes and asset_id in shared_notes:
            line += f"\n  SHARED with: {shared_notes[asset_id]}"
        lines.append(redact(line))
    return lines


def _unlinked_catalog_lines(providers: list[Provider], linked_ids: list[str]) -> list[str]:
    """Every asset on disk that is NOT linked, one compact line each (descriptions
    truncated — the model can Read the file when a name looks relevant)."""
    linked = set(linked_ids)
    lines: list[str] = []
    for provider in providers:
        for asset in provider.scan():
            if asset.id in linked:
                continue
            description = asset.description.strip().replace("\n", " ")
            if len(description) > 160:
                description = description[:157] + "..."
            lines.append(redact(f"- {asset.id} — {description}\n  file: {asset.path}"))
    return sorted(lines)


# A run whose checklist is re-derived from scratch. Withholding the previous
# checklist is the point: re-grading a frozen rubric can only ever flip items to
# `pass`, so the score ratchets to 100 and stops being able to find anything.
_CONTROL_RUN_BLOCK = """\
CONTROL RUN — build the capability checklist from scratch
Earlier runs of this scenario exist, and their checklist is being deliberately \
withheld: re-grading an inherited rubric can only confirm it, never question it. \
Derive the required capabilities independently, from the goal, the intended flow \
and the scenario — enumerate what this scenario ACTUALLY demands end-to-end, \
including capabilities an earlier rubric may never have thought to test. Do not \
reverse-engineer the checklist from what the linked assets happen to provide, and \
do not assume any previous grading was correct: re-verify every judgement against \
the files as they are now.

"""

# Once a run tops out, the rubric is exhausted — force the next one to re-derive it.
_PERFECT_SCORE = 100


@dataclass(frozen=True)
class PreviousRun:
    """What the judge is told about the last completed run of the SAME scenario,
    so it re-grades a stable checklist instead of re-inventing one, and stops
    re-flagging things already fixed."""

    score: int | None
    checklist: list[dict[str, Any]]
    applied_suggestions: list[str]


def _previous_memory_block(previous: PreviousRun | None, control_run: bool = False) -> str:
    """The memory section injected before INSTRUCTIONS. Empty for a first run."""
    if control_run:
        return _CONTROL_RUN_BLOCK
    if previous is None:
        return (
            "This is the FIRST run of this scenario — derive the capability checklist "
            "from scratch (see the checklist rules under OUTPUT).\n\n"
        )

    checklist_lines = [
        f"  - [{item.get('id')}] (weight {item.get('weight', 1)}) {item.get('title')}"
        for item in previous.checklist
    ]
    checklist_text = "\n".join(checklist_lines) if checklist_lines else "  (none recorded)"
    applied_text = (
        "\n".join(f"  - {title}" for title in previous.applied_suggestions)
        if previous.applied_suggestions
        else "  (none)"
    )
    score_text = str(previous.score) if previous.score is not None else "n/a"
    return f"""\
MEMORY OF THE PREVIOUS RUN (same scenario) — score {score_text}
Reuse THIS checklist. Keep each item's `id`, `title`, and `weight` identical; \
re-grade its `status` against the CURRENT files (a fix may have landed since). \
Add a new item ONLY for a genuinely new required capability you discover; do not \
drop items. Previous checklist:
{checklist_text}

Suggestions already APPLIED since that run — verify each actually landed in the \
files and do NOT propose it again; only raise NEW issues:
{applied_text}

"""


async def _previous_run(
    db: AsyncSession,
    project_id: uuid.UUID,
    scenario: str,
    *,
    exclude_id: uuid.UUID | None = None,
) -> PreviousRun | None:
    row = await simulation_repo.latest_completed_for_scenario(
        db, project_id, scenario, exclude_id=exclude_id
    )
    if row is None:
        return None
    applied = [
        s["title"]
        for s in (row.suggestions or [])
        if s.get("status") == "applied" and isinstance(s.get("title"), str)
    ]
    return PreviousRun(
        score=row.score,
        checklist=list(row.checklist or []),
        applied_suggestions=applied,
    )


async def _resolve_memory(
    db: AsyncSession,
    project_id: uuid.UUID,
    scenario: str,
    *,
    requested_control: bool,
    exclude_id: uuid.UUID | None = None,
) -> tuple[PreviousRun | None, bool]:
    """Pick the memory this run grades against: the previous run's checklist, or
    nothing at all (a control run).

    Forced when the last run scored 100. The memory block pins the checklist —
    same ids, weights, no drops — so fixes flip items to `pass` against a frozen
    denominator. At 100 that rubric is spent: every question it knows to ask is
    answered, and only a fresh one can find what it never asked.
    """
    previous = await _previous_run(db, project_id, scenario, exclude_id=exclude_id)
    control = requested_control or (previous is not None and previous.score == _PERFECT_SCORE)
    return (None if control else previous), control


def build_prompt(
    project: Project,
    providers: list[Provider],
    scenario: str,
    previous: PreviousRun | None = None,
    shared_notes: dict[str, str] | None = None,
    control_run: bool = False,
) -> str:
    asset_lines = _asset_lines(providers, list(project.asset_ids), shared_notes)
    assets_text = "\n".join(asset_lines) if asset_lines else "(no assets linked)"
    catalog_lines = _unlinked_catalog_lines(providers, list(project.asset_ids))
    catalog_text = "\n".join(catalog_lines) if catalog_lines else "(none)"
    roots_text = ", ".join(str(root) for root in _writable_roots(providers))
    scenario_text = (
        scenario.strip()
        or "No scenario given — derive the most representative realistic scenario from the goal."
    )
    flow = project.flow_mermaid or "(none)"
    memory = _previous_memory_block(previous, control_run)
    return f"""\
You are running a SIMULATION to evaluate whether a project's configured AI-coding \
assets (Claude Code skills and subagents) achieve the project goal.

PROJECT
- name: {project.name}
- goal:
{project.goal or "(empty)"}
- intended flow (mermaid):
{flow}

LINKED ASSETS (the toolkit under test):
{assets_text}

AVAILABLE BUT UNLINKED (on disk, NOT part of the toolkit under test — grade the \
checklist against linked assets only):
{catalog_text}

SCENARIO TO SIMULATE:
{scenario_text}

{memory}INSTRUCTIONS
1. Read EVERY linked asset file listed above with your Read tool. Ground every \
judgement in what the files actually say — trigger descriptions, steps, examples — \
not in what their names imply.
2. Simulate executing the scenario end-to-end the way Claude Code would: for each \
step decide which skill or agent (if any) would trigger given its actual trigger \
text, what it would do, and what could go wrong. Flag every point where no asset \
covers a needed step, where two assets overlap or conflict, and where an asset's \
instructions are wrong, vague, or outdated for this goal.
3. Produce a CAPABILITY CHECKLIST: the concrete, independently-verifiable \
capabilities the toolkit must have to achieve the goal for this scenario. Grade \
each one against the files.

SCORING — the score is COMPUTED from your checklist, not guessed
- Do NOT pick a holistic number. The backend computes the score as the \
weight-weighted share of checklist items that pass (partial counts half, `na` \
items are excluded). Grade honestly and the number takes care of itself.
- A `pass` item must be fully covered by an asset that would actually trigger. Use \
`partial` for present-but-weak coverage (vague trigger, missing edge case). Use \
`fail` when no asset covers the capability or one is wrong/broken for it.
- Use `na` for capabilities inherently outside a REUSABLE toolkit's control — a human \
approval gate, external DNS, a credential the user must supply — AND for scenario-specific \
PRODUCT logic: the app's headline domain feature (this scenario's core intelligence/business \
rules), which the developer builds per product. A general toolkit's job is to make such a \
feature easy and canonical to build, not to encode the specific one. Test: if the only asset \
that could own a capability would have to hardcode THIS scenario's domain (its product nouns, \
entities, or rules), it is product logic — grade it `na`, not `partial`/`fail`. These are \
excluded from the score instead of permanently capping it; explain why in the item's evidence.
- A checklist should have roughly 8-15 items. Weight each 1 (nice-to-have) to 3 \
(core to the goal).

OUTPUT
End your reply with exactly ONE fenced code block whose info string is `simulation` \
containing JSON of this shape (valid JSON — escape newlines inside strings):

```simulation
{{
  "checklist": [
    {{
      "id": "snake_case_stable_id",
      "title": "short capability statement",
      "weight": 3,
      "status": "pass",
      "evidence": "one line citing the asset/file that covers it, or the exact gap"
    }}
  ],
  "score": 0,
  "verdict": "one-sentence verdict",
  "summary": "markdown — the simulated run, step by step, naming the asset used at each \
step",
  "analysis": "markdown — strengths, gaps, mis-triggers, overlaps; use headings and \
tables where useful",
  "trace_mermaid": "flowchart TD — the execution trace: scenario steps, which asset \
handled each, and gap/failure nodes. Put every node label in double quotes. Follow \
the trace styling rules below.",
  "suggestions": [
    {{
      "title": "short imperative title",
      "impact": "high",
      "rationale": "markdown — which checklist item(s) this flips and why",
      "changes": [
        {{
          "path": "/absolute/path/under/a/writable/root",
          "action": "update",
          "new_content": "COMPLETE new file content (never a diff), or null when action is delete",
          "description": "what this change does"
        }}
      ]
    }}
  ]
}}
```

Rules for the block:
- "status" is "pass", "partial", "fail", or "na"; "weight" is an integer 1-3. \
Still fill in "score" (your best estimate) — the backend overrides it from the \
checklist, but never leave it out.
- "impact" is "high", "medium", or "low"; "action" is "update", "create", "delete", \
"link", or "unlink".
- Every suggestion must target a checklist item that is `partial` or `fail`. If \
every gradable item already passes, "suggestions" MUST be an empty list — an empty \
list is the correct, expected outcome for a mature toolkit, not a failure.
- LINK BEFORE CREATE: when a gap is covered by an asset in the AVAILABLE BUT \
UNLINKED list, Read that file to confirm, then propose {{"action": "link", "path": \
"<its file path>", "new_content": null}} instead of creating a duplicate. Applying \
it adds the asset to the toolkit. A "link" may be combined with an "update" of the \
same file when the existing asset also needs changes (link works for read-only \
plugin assets too; update does not).
- UNLINK DEAD WEIGHT: when a LINKED asset serves no checklist capability and never \
appears in the trace, propose {{"action": "unlink", "path": "<its file path>", \
"new_content": null}} to drop it from this project's toolkit. The file is untouched \
and other projects keep it — only this project's link is removed. Unlink-only \
suggestions are exempt from the checklist-item rule above; their rationale is the \
dilution the dead weight causes. Never unlink an asset the scenario merely failed \
to trigger this run but the goal plausibly needs.
- Suggestion changes may ONLY touch files under these writable roots: {roots_text}. \
Never propose changes to plugin-provided (READ-ONLY) assets — put advice about \
those in "analysis" instead.
- Consider BOTH asset kinds: skills (SKILL.md under a skills root) and subagents \
(<name>.md under an agents root). Propose a new or updated subagent whenever the \
goal needs a role, trigger, or handoff no current agent covers — don't default to \
skills-only suggestions.
- GENERALITY GUARD: every suggestion edits a SHARED, reusable asset, so each change \
must be scenario-agnostic and help the whole class of goals — never hardcode THIS \
scenario's domain (its product nouns, entities, or business rules) into a skill or \
agent. A change that only helps this one named scenario is anti-valuable. If a gap can \
only be closed by naming this scenario's domain in an asset, it is product logic, not a \
toolkit gap: grade that item `na` and omit the suggestion. When a genuinely reusable \
pattern underlies a scenario-specific gap (e.g. a generic "uploaded document → \
deterministic parse → optional LLM classify → typed route" pipeline), propose THAT \
general pattern, phrased and triggered generically — never the single instance.
- SHARED-ASSET RULE: an asset annotated "SHARED with:" is load-bearing for the \
other project goals listed there, which you cannot otherwise see. Edits to such \
an asset must be ADDITIVE — add a new section, profile, or variant alongside the \
existing content; never restructure, rename, or delete existing sections those \
goals rely on, and never make the asset contradict a listed goal. Since \
"new_content" replaces the whole file, reproduce ALL existing content faithfully \
and weave your addition in. If the change this project needs inherently conflicts \
with a listed goal, do NOT propose it — describe the trade-off in "analysis".
- Order suggestions by impact, highest first.
- Trace styling: so readers can tell node kinds apart, "trace_mermaid" must end \
with exactly these classDef lines and assign a class to every asset node:
  classDef agent fill:#ede9fe,stroke:#7c3aed,color:#4c1d95
  classDef skill fill:#d1fae5,stroke:#059669,color:#065f46
  classDef gap fill:#fee2e2,stroke:#dc2626,color:#7f1d1d,stroke-dasharray: 4 3
  Tag every subagent node `class <nodeId> agent`, every skill node \
`class <nodeId> skill`, and every gap/failure node `class <nodeId> gap`; plain \
scenario steps stay unstyled. When a subagent invokes skills, draw a dashed edge \
from the agent to each skill it uses: agentId -. "uses" .-> skillId.
- The block must be the last thing in your reply.
"""


def build_scenario_prompt(
    project: Project, providers: list[Provider], previous_scenario: str = ""
) -> str:
    asset_lines = _asset_lines(providers, list(project.asset_ids))
    assets_text = "\n".join(asset_lines) if asset_lines else "(no assets linked)"
    flow = project.flow_mermaid or "(none)"
    previous_block = (
        f"""
PREVIOUS SCENARIO (already used — do NOT rewrite or resemble it):
{previous_scenario}
"""
        if previous_scenario.strip()
        else ""
    )
    distinct_rule = (
        "- A previous scenario is shown above. Write a COMPLETELY different one: "
        "different user request, different domain specifics, different complication. "
        "Reusing the toolkit is expected; reusing the story, tech names, or edge case "
        "is not.\n"
        if previous_scenario.strip()
        else ""
    )
    return f"""\
You are writing ONE concrete simulation scenario for a project whose configured \
AI-coding assets (Claude Code skills and subagents) are meant to achieve a goal.

PROJECT
- name: {project.name}
- goal:
{project.goal or "(empty)"}
- intended flow (mermaid):
{flow}

LINKED ASSETS (the toolkit the scenario must exercise):
{assets_text}
{previous_block}
TASK
Write ONE concrete simulation scenario for this project — a realistic first-person \
request a user would type to Claude Code, phrased so the linked assets' trigger \
descriptions would have to match it. 2-5 sentences. It must exercise the toolkit \
end-to-end for the goal, name concrete specifics (tech, endpoints, pages — invented \
but plausible), and include at least one realistic complication or edge case (a \
failure to diagnose, a constraint, a cross-cutting step). It must be self-consistent \
(no impossible premises) and must NOT merely restate the goal.
{distinct_rule}
Reply with ONLY a fenced code block whose info string is `scenario` containing the \
scenario text — no prose before or after.
"""


def _asset_id_for_path(providers: list[Provider], path: str) -> str | None:
    target = Path(path)
    for provider in providers:
        asset_id = provider.asset_id_for_path(target)
        if asset_id is not None:
            return asset_id
    return None


def _checklist_to_row(parsed: ParsedSimulation) -> list[dict[str, Any]]:
    """Flatten parsed checklist items into the JSONB rows the API serves."""
    return [
        {
            "id": item.id,
            "title": item.title,
            "weight": item.weight,
            "status": item.status,
            "evidence": item.evidence,
        }
        for item in parsed.checklist
    ]


def _suggestion_to_row(providers: list[Provider], parsed: ParsedSimulation) -> list[dict[str, Any]]:
    """Normalize parsed suggestions into the JSONB rows the API serves."""
    return [
        {
            "title": suggestion.title,
            "impact": suggestion.impact,
            "rationale": suggestion.rationale,
            "changes": [
                {
                    "path": change.path,
                    "action": change.action,
                    "new_content": change.new_content,
                    "description": change.description,
                    "asset_id": _asset_id_for_path(providers, change.path),
                }
                for change in suggestion.changes
            ],
            "status": "pending",
            "error": None,
            "applied_at": None,
        }
        for suggestion in parsed.suggestions
    ]


async def _run_claude(
    runner: ClaudeRunner, simulation_id: uuid.UUID, prompt: str
) -> tuple[ParsedSimulation | None, str | None, dict[str, Any] | None]:
    """Shell out to claude and parse the reply; never raises."""
    try:
        result = await runner.run(prompt)
        stats = result.stats or None
        parsed = extract_simulation(result.reply)
        if parsed is None:
            return None, "the model did not return a valid `simulation` block", stats
        return parsed, None, stats
    except ClaudeRunnerError as exc:
        return None, str(exc), None
    except Exception:  # never lose the row to an unexpected bug — mark it failed
        logger.exception("simulation %s crashed", simulation_id)
        return None, "unexpected internal error while running the simulation", None


def _finish_simulation(
    providers: list[Provider],
    simulation: Simulation,
    parsed: ParsedSimulation | None,
    error: str | None,
    stats: dict[str, Any] | None,
) -> None:
    """Write the run outcome onto the row (caller commits)."""
    if parsed is not None:
        simulation.status = "completed"
        simulation.score = parsed.score
        simulation.verdict = parsed.verdict
        simulation.summary = parsed.summary
        simulation.analysis = parsed.analysis
        simulation.trace_mermaid = parsed.trace_mermaid
        simulation.suggestions = _suggestion_to_row(providers, parsed)
        simulation.checklist = _checklist_to_row(parsed)
    else:
        simulation.status = "failed"
        simulation.error = error
    simulation.stats = stats
    simulation.completed_at = _utcnow()


async def _run_simulation(
    session_factory: async_sessionmaker[AsyncSession],
    providers: list[Provider],
    runner: ClaudeRunner,
    simulation_id: uuid.UUID,
    prompt: str,
) -> None:
    """Background task: shell out to claude, then persist the outcome."""
    parsed, error, stats = await _run_claude(runner, simulation_id, prompt)
    async with session_factory() as db:
        simulation = await simulation_repo.get_simulation(db, simulation_id)
        if simulation is None:  # deleted while running
            return
        _finish_simulation(providers, simulation, parsed, error, stats)
        await db.commit()


async def start_simulation(
    db: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    providers: list[Provider],
    runner: ClaudeRunner,
    schedule: Any,  # BackgroundTasks.add_task-compatible callable holder
    project_id: str,
    body: schemas.SimulationCreateRequest,
) -> schemas.Simulation:
    project = await _get_project_or_404(db, project_id)
    _require_linked_assets(project)
    if await simulation_repo.project_has_running(db, project.id):
        raise SimulationRunningError("a simulation is already running for this project")

    scenario = body.scenario.strip()
    simulation = await simulation_repo.create_simulation(
        db, project_id=project.id, scenario=scenario
    )
    previous, control = await _resolve_memory(
        db,
        project.id,
        scenario,
        requested_control=body.control_run,
        exclude_id=simulation.id,
    )
    simulation.control_run = control
    shared = await shared_asset_notes(db, exclude_project_id=project.id)
    prompt = build_prompt(project, providers, scenario, previous, shared, control_run=control)
    # Mirror the last-used scenario onto the project (including empty), so the
    # Simulation tab can prefill it and the next run reuses it.
    if project.scenario != scenario:
        project.scenario = scenario
        project.updated_at = _utcnow()
    await db.commit()

    schedule.add_task(_run_simulation, session_factory, providers, runner, simulation.id, prompt)
    return serializers.simulation_to_schema(simulation)


async def _fail_running_row(
    session_factory: async_sessionmaker[AsyncSession],
    simulation_id: uuid.UUID,
    error: str,
    *,
    status: str = "failed",
) -> None:
    """End an in-flight run. `interrupted` means nobody's assets are at fault."""
    async with session_factory() as db:
        simulation = await simulation_repo.get_simulation(db, simulation_id)
        if simulation is None or simulation.status != "running":
            return
        simulation.status = status
        simulation.error = error
        simulation.completed_at = _utcnow()
        await db.commit()


async def _apply_all_suggestions(
    session_factory: async_sessionmaker[AsyncSession],
    providers: list[Provider],
    simulation_id: uuid.UUID,
) -> int:
    """Apply every suggestion of a completed run; returns how many succeeded."""
    async with session_factory() as db:
        simulation = await simulation_repo.get_simulation(db, simulation_id)
        count = len(simulation.suggestions) if simulation else 0
    applied = 0
    for index in range(count):
        async with session_factory() as db:
            try:
                result = await apply_suggestion(db, providers, str(simulation_id), index)
            except (SimulationNotFoundError, SuggestionNotFoundError, SuggestionNotPendingError):
                continue
            if result.suggestions[index].status == "applied":
                applied += 1
    return applied


async def _rotate_scenario(
    session_factory: async_sessionmaker[AsyncSession],
    providers: list[Provider],
    runner: ClaudeRunner,
    project_id: uuid.UUID,
    next_simulation_id: uuid.UUID,
) -> str:
    """Generate a fresh scenario mid-chain and pin it to the project and to the
    already-created next row. Raises ScenarioGenerationError. The claude call
    runs between two short sessions so no DB session is held across it.
    """
    async with session_factory() as db:
        project = await project_repo.get_project(db, project_id)
        if project is None:
            raise ScenarioGenerationError("the project was deleted")
        prompt = build_scenario_prompt(project, providers, previous_scenario=project.scenario)

    generated = await _run_scenario_prompt(runner, prompt)

    async with session_factory() as db:
        project = await project_repo.get_project(db, project_id)
        row = await simulation_repo.get_simulation(db, next_simulation_id)
        if project is None or row is None:
            raise ScenarioGenerationError("the project was deleted")
        project.scenario = generated
        project.updated_at = _utcnow()
        row.scenario = generated
        await db.commit()
    return generated


async def _run_autopilot(
    session_factory: async_sessionmaker[AsyncSession],
    providers: list[Provider],
    runner: ClaudeRunner,
    project_id: uuid.UUID,
    run_id: uuid.UUID,
    scenario: str,
    total: int,
    first_simulation_id: uuid.UUID,
    requested_control: bool = False,
) -> None:
    """Background task: run → auto-apply suggestions → run again, up to `total`
    times. A run that scores 100 rotates in a freshly generated scenario instead
    of ending the chain. Stops early when a run fails, yields no suggestions,
    nothing could be applied, a new scenario could not be generated, the user
    stops it, or a row/project disappears. The next iteration's row is created in
    the SAME commit that completes the current one, so the project always has a
    `running` row until the chain ends (this keeps the frontend polling and
    blocks concurrent manual runs).
    """
    simulation_id = first_simulation_id
    try:
        for iteration in range(1, total + 1):
            if run_id in _cancelled_autopilots:
                await _fail_running_row(
                    session_factory,
                    simulation_id,
                    "autopilot stopped by user",
                    status="interrupted",
                )
                return

            async with session_factory() as db:
                project = await project_repo.get_project(db, project_id)
                if project is None:  # deleted — cascade removed the rows too
                    return
                # Rebuild each iteration: applied suggestions change files and links,
                # and the prior iteration becomes this run's re-gradable memory.
                # An explicit control run only covers iteration 1 — later iterations
                # need the fresh rubric it produced as their baseline.
                previous, control = await _resolve_memory(
                    db,
                    project_id,
                    scenario,
                    requested_control=requested_control and iteration == 1,
                    exclude_id=simulation_id,
                )
                shared = await shared_asset_notes(db, exclude_project_id=project_id)
                prompt = build_prompt(
                    project, providers, scenario, previous, shared, control_run=control
                )
                simulation = await simulation_repo.get_simulation(db, simulation_id)
                if simulation is not None and simulation.control_run != control:
                    simulation.control_run = control
                    await db.commit()

            parsed, error, stats = await _run_claude(runner, simulation_id, prompt)

            next_id: uuid.UUID | None = None
            perfect = False
            async with session_factory() as db:
                simulation = await simulation_repo.get_simulation(db, simulation_id)
                if simulation is None:  # deleted while running — treat as stopped
                    return
                _finish_simulation(providers, simulation, parsed, error, stats)
                perfect = simulation.score == _PERFECT_SCORE
                # A perfect run keeps the chain alive even with nothing left to
                # suggest: its rubric is spent, and a fresh scenario asks what it
                # never asked.
                chain = (
                    parsed is not None
                    and (len(parsed.suggestions) > 0 or perfect)
                    and iteration < total
                    and run_id not in _cancelled_autopilots
                )
                if chain:
                    next_row = await simulation_repo.create_simulation(
                        db,
                        project_id=simulation.project_id,
                        scenario=scenario,
                        autopilot_run_id=run_id,
                        autopilot_iteration=iteration + 1,
                        autopilot_total=total,
                    )
                    next_id = next_row.id
                await db.commit()

            if next_id is None:
                return

            if perfect:
                try:
                    scenario = await _rotate_scenario(
                        session_factory, providers, runner, project_id, next_id
                    )
                except ScenarioGenerationError as exc:
                    await _fail_running_row(
                        session_factory,
                        next_id,
                        f"autopilot stopped: could not generate a new scenario after a "
                        f"perfect score: {exc}",
                    )
                    return
                # A perfect run often has nothing to apply — that is not a stop.
                await _apply_all_suggestions(session_factory, providers, simulation_id)
                simulation_id = next_id
                continue

            if await _apply_all_suggestions(session_factory, providers, simulation_id) == 0:
                await _fail_running_row(
                    session_factory, next_id, "autopilot stopped: no suggestion could be applied"
                )
                return
            simulation_id = next_id
    except Exception:  # pragma: no cover — belt and braces around the whole chain
        logger.exception("autopilot %s crashed", run_id)
        await _fail_running_row(
            session_factory, simulation_id, "unexpected internal error during autopilot"
        )
    finally:
        _cancelled_autopilots.discard(run_id)


async def start_autopilot(
    db: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    providers: list[Provider],
    runner: ClaudeRunner,
    schedule: Any,
    project_id: str,
    body: schemas.AutopilotCreateRequest,
) -> schemas.Simulation:
    project = await _get_project_or_404(db, project_id)
    _require_linked_assets(project)
    if await simulation_repo.project_has_running(db, project.id):
        raise SimulationRunningError("a simulation is already running for this project")

    scenario = body.scenario.strip()
    run_id = uuid.uuid4()
    simulation = await simulation_repo.create_simulation(
        db,
        project_id=project.id,
        scenario=scenario,
        autopilot_run_id=run_id,
        autopilot_iteration=1,
        autopilot_total=body.iterations,
    )
    if project.scenario != scenario:
        project.scenario = scenario
        project.updated_at = _utcnow()
    await db.commit()

    schedule.add_task(
        _run_autopilot,
        session_factory,
        providers,
        runner,
        project.id,
        run_id,
        scenario,
        body.iterations,
        simulation.id,
        body.control_run,
    )
    return serializers.simulation_to_schema(simulation)


async def stop_autopilot(db: AsyncSession, run_id: str) -> None:
    """Flag an in-flight autopilot chain to stop after its current run."""
    parsed = _parse_uuid(run_id)
    if parsed is None or not await simulation_repo.autopilot_has_running(db, parsed):
        raise AutopilotNotFoundError(f"no autopilot run in flight: {run_id}")
    _cancelled_autopilots.add(parsed)


async def _run_scenario_prompt(runner: ClaudeRunner, prompt: str) -> str:
    """One claude call for a scenario; raises ScenarioGenerationError."""
    try:
        reply = await runner.run_once(prompt)
    except ClaudeRunnerError as exc:
        raise ScenarioGenerationError(f"claude failed to generate a scenario: {exc}") from exc

    # Prefer the fenced block; fall back to the whole reply if the model skipped it.
    generated = extract_scenario(reply) or reply.strip()
    if not generated:
        raise ScenarioGenerationError("the assistant returned an empty scenario")
    return generated


async def generate_scenario(
    db: AsyncSession,
    providers: list[Provider],
    runner: ClaudeRunner,
    project_id: str,
) -> schemas.ScenarioGenerateResponse:
    """Synchronously generate and persist one simulation scenario for a project."""
    project = await _get_project_or_404(db, project_id)
    _require_linked_assets(project)
    prompt = build_scenario_prompt(project, providers, previous_scenario=project.scenario)
    generated = await _run_scenario_prompt(runner, prompt)

    project.scenario = generated
    project.updated_at = _utcnow()
    await db.commit()
    return schemas.ScenarioGenerateResponse(scenario=generated)


async def list_simulations(db: AsyncSession, project_id: str) -> list[schemas.Simulation]:
    project = await _get_project_or_404(db, project_id)
    simulations = await simulation_repo.list_simulations(db, project.id)
    return [serializers.simulation_to_schema(s) for s in simulations]


async def get_simulation(db: AsyncSession, simulation_id: str) -> schemas.Simulation:
    simulation = await _get_simulation_or_404(db, simulation_id)
    return serializers.simulation_to_schema(simulation)


async def delete_simulation(db: AsyncSession, simulation_id: str) -> None:
    simulation = await _get_simulation_or_404(db, simulation_id)
    await simulation_repo.delete_simulation(db, simulation)
    await db.commit()


def _fail_suggestion(suggestions: list[dict[str, Any]], index: int, error: str) -> None:
    suggestions[index] = {**suggestions[index], "status": "failed", "error": error}


async def _sync_project_links(
    db: AsyncSession,
    providers: list[Provider],
    project_id: uuid.UUID,
    changes: list[dict[str, Any]],
) -> None:
    """Keep project links in step with an applied suggestion: assets it created
    or updated become linked, assets it deleted or unlinked leave the toolkit.
    Saves the user from manually re-linking every asset a simulation invents.
    """
    project = await project_repo.get_project(db, project_id)
    if project is None:
        return
    linked = list(project.asset_ids)
    for change in changes:
        # Stored asset_id, or recompute (a create's mapping may not have resolved
        # at simulation time if the provider dir didn't exist yet).
        asset_id = change.get("asset_id") or _asset_id_for_path(providers, str(change.get("path")))
        if not asset_id:
            continue
        if change.get("action") in ("delete", "unlink"):
            if asset_id in linked:
                linked.remove(asset_id)
        elif asset_id not in linked:
            linked.append(asset_id)
    if linked != list(project.asset_ids):
        project.asset_ids = linked
        project.updated_at = _utcnow()


async def apply_suggestion(
    db: AsyncSession,
    providers: list[Provider],
    simulation_id: str,
    suggestion_index: int,
) -> schemas.Simulation:
    simulation = await _get_simulation_or_404(db, simulation_id)
    suggestions = [dict(s) for s in simulation.suggestions]
    if not 0 <= suggestion_index < len(suggestions):
        raise SuggestionNotFoundError(f"no suggestion at index {suggestion_index}")
    suggestion = suggestions[suggestion_index]
    # `failed` may be retried (the failure could be transient); `applied` may not.
    if suggestion["status"] == "applied":
        raise SuggestionNotPendingError("suggestion is already applied")

    roots = _writable_roots(providers)

    # Validate every change before writing anything (same policy as proposals).
    # `link`/`unlink` changes write nothing — they only need to map to a known
    # asset (plugin assets are read-only yet linkable), so they skip the roots check.
    resolved_paths: list[Path | None] = []
    failure: str | None = None
    for change in suggestion["changes"]:
        if change.get("action") in ("link", "unlink"):
            path = str(change.get("path"))
            if not (change.get("asset_id") or _asset_id_for_path(providers, path)):
                failure = f"{change.get('action')} target is not a known asset: {path}"
                break
            resolved_paths.append(None)
            continue
        resolved = resolve_within_roots(Path(str(change.get("path"))), roots)
        if resolved is None:
            failure = f"path outside allowed roots: {change.get('path')}"
            break
        resolved_paths.append(resolved)

    written = [path for path in resolved_paths if path is not None]
    if failure is None:
        await prepare_snapshots(providers, written)
        for change, resolved in zip(suggestion["changes"], resolved_paths, strict=True):
            if resolved is None:  # link — nothing to write
                continue
            try:
                apply_change(change, resolved)
            except (OSError, ValueError) as exc:
                failure = f"{change.get('path')}: {exc}"
                break

    if failure is not None:
        _fail_suggestion(suggestions, suggestion_index, failure)
    else:
        suggestions[suggestion_index] = {
            **suggestion,
            "status": "applied",
            "error": None,
            "applied_at": _utcnow().isoformat(),
        }
        await _sync_project_links(db, providers, simulation.project_id, suggestion["changes"])
        await snapshot_writes(
            providers,
            written,
            f"masterwork: apply suggestion: {suggestion['title'][:72]}",
        )
    # Reassign so SQLAlchemy sees the JSONB change.
    simulation.suggestions = suggestions
    await db.commit()
    return serializers.simulation_to_schema(simulation)
