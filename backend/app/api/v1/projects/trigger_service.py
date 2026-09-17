"""Trigger-guide generation.

One-shot agent CLI call that READS every linked asset file and explains how to
make the active agent (Claude Code or Codex) fire this toolkit: entry prompts to
type, the actual trigger phrases each asset matches on, and how the assets chain
into each other. The result is persisted on the project so the Trigger tab
survives reloads.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.projects import schemas, service
from app.core.exceptions import TriggerGenerationError
from app.db.models.project import Project
from app.providers.base import Provider
from app.services.agent_runner import AgentRunner, AgentRunnerError
from app.services.redact import redact
from app.services.trigger_parser import extract_trigger

EMPTY_GUIDE = (
    "_No assets linked yet — link the skills and agents this project should use "
    "(Overview tab), then generate again._"
)


def _asset_lines(providers: list[Provider], asset_ids: list[str]) -> list[str]:
    index = {asset.id: asset for provider in providers for asset in provider.scan()}
    lines: list[str] = []
    for asset_id in asset_ids:
        asset = index.get(asset_id)
        if asset is None:
            lines.append(f"- {asset_id} — MISSING: the linked file no longer exists on disk")
            continue
        line = f"- {asset_id} — {asset.title}: {asset.description}\n  file: {asset.path}"
        lines.append(redact(line))
    return lines


# Codex invokes skills differently enough that the guide has to say so.
_CODEX_NOTES = """\
HOW CODEX PICKS ASSETS (ground the guide in this)
- Codex sees every available skill's name and description and decides from the \
description alone whether a request needs that skill, so the trigger phrasing in \
the description is what makes it fire on its own.
- A prompt can force a skill with an explicit mention of its name: `$skill-name` \
(e.g. `$backend-dev`). Use it in at least one example prompt for the entry asset, \
and say when the description alone is enough.
- Codex loads skills only from ~/.codex/skills, ~/.agents/skills and its enabled \
plugins, and custom agents from ~/.codex/agents/<name>.toml. Claude Code skills \
(~/.claude/skills) and subagents (~/.claude/agents) are invisible to it: name every \
linked asset Codex will never load, and what the flow loses without it.

"""


def build_trigger_prompt(
    project: Project, providers: list[Provider], *, agent_id: str, agent_name: str
) -> str:
    assets_text = "\n".join(_asset_lines(providers, list(project.asset_ids)))
    flow = project.flow_mermaid or "(none)"
    codex = agent_id == "codex"
    notes = _CODEX_NOTES if codex else ""
    invoked_by = "the user (by description or `$name`)" if codex else "the user"
    explicit_tip = " When to add an explicit `$skill-name`." if codex else ""
    return f"""\
You are writing a TRIGGER GUIDE for a project whose configured AI-coding assets \
(skills and subagents) are meant to achieve a goal together. The guide teaches \
the user how to phrase requests to {agent_name} so that this toolkit actually \
fires — the right entry asset triggers, and the chain of skills/agents runs \
end-to-end.

PROJECT
- name: {project.name}
- goal:
{project.goal or "(empty)"}
- intended flow (mermaid):
{flow}

LINKED ASSETS:
{assets_text}

{notes}INSTRUCTIONS
1. Read EVERY linked asset file. Ground everything in the actual trigger text \
(frontmatter descriptions, "Use when…" phrases, examples) — not in what the \
names imply.
2. Write a markdown guide with exactly these sections:
   - `## Entry point` — which asset kicks off the whole flow, and why (quote \
the decisive phrases from its trigger description).
   - `## Prompts that trigger the full flow` — 2-3 ready-to-paste example \
prompts for {agent_name}, each in a fenced code block, from short to detailed. \
After each, one line on why it matches the entry trigger.
   - `## Trigger phrases per asset` — one bullet per asset: the key phrases \
from its ACTUAL description that make it fire, and whether it is invoked \
directly by {invoked_by} or by another asset (conductor/agent).
   - `## How the chain runs` — the order in which the assets invoke each other \
for this goal, one line per hand-off.
   - `## Make triggering reliable` — short do/don't tips: words to include, \
phrasings that would mis-route to the wrong asset or skip the conductor.{explicit_tip}
Be concrete and quote real phrases; plain markdown only (no HTML).

Reply with ONLY a fenced code block whose info string is `trigger` containing \
the markdown — no prose before or after.
"""


async def generate_trigger_guide(
    db: AsyncSession,
    providers: list[Provider],
    runner: AgentRunner,
    project_id: str,
) -> schemas.ProjectTriggerResponse:
    """Synchronously generate and persist the trigger guide for a project."""
    project = await service.get_project_or_404(db, project_id)

    if not project.asset_ids:
        guide = EMPTY_GUIDE
    else:
        prompt = build_trigger_prompt(
            project, providers, agent_id=runner.agent_id, agent_name=runner.display_name
        )
        try:
            reply = await runner.run_once(prompt)
        except AgentRunnerError as exc:
            raise TriggerGenerationError(
                f"{runner.display_name} failed to generate the guide: {exc}"
            ) from exc
        guide = extract_trigger(reply) or reply.strip()
        if not guide:
            raise TriggerGenerationError("the assistant returned an empty guide")

    now = datetime.now(tz=UTC)
    project.trigger_guide = guide
    project.trigger_guide_at = now
    project.updated_at = now
    await db.commit()
    return schemas.ProjectTriggerResponse(trigger_guide=guide, generated_at=now)
