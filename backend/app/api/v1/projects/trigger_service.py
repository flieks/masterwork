"""Trigger-guide generation.

One-shot claude -p that READS every linked asset file and explains how to make
Claude Code fire this toolkit: entry prompts to type, the actual trigger
phrases each asset matches on, and how the assets chain into each other. The
result is persisted on the project so the Trigger tab survives reloads.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.projects import schemas, service
from app.core.exceptions import TriggerGenerationError
from app.db.models.project import Project
from app.providers.base import Provider
from app.services.claude_runner import ClaudeRunner, ClaudeRunnerError
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


def build_trigger_prompt(project: Project, providers: list[Provider]) -> str:
    assets_text = "\n".join(_asset_lines(providers, list(project.asset_ids)))
    flow = project.flow_mermaid or "(none)"
    return f"""\
You are writing a TRIGGER GUIDE for a project whose configured AI-coding assets \
(Claude Code skills and subagents) are meant to achieve a goal together. The \
guide teaches the user how to phrase requests to Claude Code so that this \
toolkit actually fires — the right entry asset triggers, and the chain of \
skills/agents runs end-to-end.

PROJECT
- name: {project.name}
- goal:
{project.goal or "(empty)"}
- intended flow (mermaid):
{flow}

LINKED ASSETS:
{assets_text}

INSTRUCTIONS
1. Read EVERY linked asset file with your Read tool. Ground everything in the \
actual trigger text (frontmatter descriptions, "Use when…" phrases, examples) — \
not in what the names imply.
2. Write a markdown guide with exactly these sections:
   - `## Entry point` — which asset kicks off the whole flow, and why (quote \
the decisive phrases from its trigger description).
   - `## Prompts that trigger the full flow` — 2-3 ready-to-paste example \
prompts for Claude Code, each in a fenced code block, from short to detailed. \
After each, one line on why it matches the entry trigger.
   - `## Trigger phrases per asset` — one bullet per asset: the key phrases \
from its ACTUAL description that make it fire, and whether it is invoked \
directly by the user or by another asset (conductor/agent).
   - `## How the chain runs` — the order in which the assets invoke each other \
for this goal, one line per hand-off.
   - `## Make triggering reliable` — short do/don't tips: words to include, \
phrasings that would mis-route to the wrong asset or skip the conductor.
Be concrete and quote real phrases; plain markdown only (no HTML).

Reply with ONLY a fenced code block whose info string is `trigger` containing \
the markdown — no prose before or after.
"""


async def generate_trigger_guide(
    db: AsyncSession,
    providers: list[Provider],
    runner: ClaudeRunner,
    project_id: str,
) -> schemas.ProjectTriggerResponse:
    """Synchronously generate and persist the trigger guide for a project."""
    project = await service.get_project_or_404(db, project_id)

    if not project.asset_ids:
        guide = EMPTY_GUIDE
    else:
        prompt = build_trigger_prompt(project, providers)
        try:
            reply = await runner.run_once(prompt)
        except ClaudeRunnerError as exc:
            raise TriggerGenerationError(f"claude failed to generate the guide: {exc}") from exc
        guide = extract_trigger(reply) or reply.strip()
        if not guide:
            raise TriggerGenerationError("the assistant returned an empty guide")

    now = datetime.now(tz=UTC)
    project.trigger_guide = guide
    project.trigger_guide_at = now
    project.updated_at = now
    await db.commit()
    return schemas.ProjectTriggerResponse(trigger_guide=guide, generated_at=now)
