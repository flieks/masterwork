"""Change-summary generation.

Collects every APPLIED asset change a project has accumulated — chat proposals
and simulation suggestions — groups them per asset, and asks claude for one
markdown digest: a global overview plus a per-asset breakdown. The result is
persisted on the project so the Summary tab survives reloads.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.projects import schemas, service
from app.core.exceptions import SummaryGenerationError
from app.repositories import proposals as proposal_repo
from app.repositories import simulations as simulation_repo
from app.services.claude_runner import ClaudeRunner, ClaudeRunnerError
from app.services.redact import redact
from app.services.summary_parser import extract_summary

EMPTY_SUMMARY = (
    "_No applied changes yet — accept a chat proposal or apply a simulation "
    "suggestion first, then generate again._"
)

_MAX_DESCRIPTION_CHARS = 500


@dataclass(frozen=True)
class _AppliedChange:
    when: datetime | None
    source: str  # e.g. 'chat proposal "Sharpen triggers"' / 'simulation run (score 82)'
    action: str  # update | create | delete
    path: str
    description: str


def _asset_name(path: str, asset_id: str | None) -> str:
    """Group key: asset id when known, else skill folder / agent file stem."""
    if asset_id:
        return asset_id
    parts = [p for p in path.split("/") if p]
    file = parts[-1] if parts else path
    if file.upper() == "SKILL.MD" and len(parts) >= 2:
        return parts[-2]
    return file.removesuffix(".md")


def _clip(text: str) -> str:
    text = " ".join(text.split())
    if len(text) > _MAX_DESCRIPTION_CHARS:
        return text[: _MAX_DESCRIPTION_CHARS - 1] + "…"
    return text


async def _collect_changes(
    db: AsyncSession, project_id: uuid.UUID
) -> dict[str, list[_AppliedChange]]:
    """All applied changes grouped per asset, each group oldest-first."""
    grouped: dict[str, list[_AppliedChange]] = {}

    def add(source: str, when: datetime | None, change: dict[str, Any]) -> None:
        path = str(change.get("path") or "")
        entry = _AppliedChange(
            when=when,
            source=source,
            action=str(change.get("action") or "update"),
            path=path,
            description=_clip(str(change.get("description") or "")),
        )
        grouped.setdefault(_asset_name(path, change.get("asset_id")), []).append(entry)

    for proposal in await proposal_repo.list_applied_for_project(db, project_id):
        source = f'chat proposal "{_clip(proposal.summary) or "untitled"}"'
        for change in proposal.changes:
            add(source, proposal.applied_at, change)

    for simulation in await simulation_repo.list_simulations(db, project_id):
        for suggestion in simulation.suggestions:
            if suggestion.get("status") != "applied":
                continue
            score = f"score {simulation.score}" if simulation.score is not None else "no score"
            title = _clip(str(suggestion.get("title") or ""))
            source = f'simulation suggestion "{title}" ({score} run)'
            applied_at = suggestion.get("applied_at")
            when = datetime.fromisoformat(applied_at) if applied_at else simulation.completed_at
            for change in suggestion.get("changes") or []:
                add(source, when, change)

    for changes in grouped.values():
        changes.sort(key=lambda c: (c.when is None, c.when or datetime.min.replace(tzinfo=UTC)))
    return grouped


def build_summary_prompt(
    project_name: str, goal: str, grouped: dict[str, list[_AppliedChange]]
) -> str:
    sections: list[str] = []
    for asset, changes in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
        lines = [f"ASSET: {asset}"]
        for c in changes:
            when = c.when.strftime("%Y-%m-%d %H:%M") if c.when else "unknown time"
            lines.append(f"- {when} — {c.source} — {c.action} {c.path}: {c.description}")
        sections.append("\n".join(lines))
    changes_text = redact("\n\n".join(sections))
    return f"""\
You are summarizing every change that has been applied to a project's AI-coding \
assets (Claude Code skills and subagents) through the Masterwork app — via \
accepted chat proposals and applied simulation suggestions.

PROJECT
- name: {project_name}
- goal:
{goal or "(empty)"}

APPLIED CHANGES (grouped per asset, oldest first within each group):
{changes_text}

TASK
Write a markdown digest of how the toolkit evolved:
1. Start with a `## Overview` section: 1-2 short paragraphs on the overall \
direction of the changes (what problems they addressed, how the toolkit \
improved), then one line with totals: how many assets were created, updated, \
and deleted.
2. Then a `## Changes per asset` section with one `### <asset>` subsection per \
asset, ordered by amount of change (most-changed first). Open each subsection \
with a bold one-liner stating whether the asset was **created**, **updated** \
(×N), or **deleted**, then 2-5 bullets summarizing what actually changed in \
substance. MERGE related edits into one bullet — do not restate every change \
line verbatim.
Be concrete but concise; plain markdown only (no HTML).

Reply with ONLY a fenced code block whose info string is `summary` containing \
the markdown — no prose before or after.
"""


async def generate_summary(
    db: AsyncSession,
    runner: ClaudeRunner,
    project_id: str,
) -> schemas.ProjectSummaryResponse:
    """Synchronously generate and persist the change summary for a project."""
    project = await service.get_project_or_404(db, project_id)
    grouped = await _collect_changes(db, project.id)

    if not grouped:
        summary = EMPTY_SUMMARY
    else:
        prompt = build_summary_prompt(project.name, project.goal, grouped)
        try:
            reply = await runner.run_once(prompt)
        except ClaudeRunnerError as exc:
            raise SummaryGenerationError(f"claude failed to generate a summary: {exc}") from exc
        summary = extract_summary(reply) or reply.strip()
        if not summary:
            raise SummaryGenerationError("the assistant returned an empty summary")

    now = datetime.now(tz=UTC)
    project.change_summary = summary
    project.change_summary_at = now
    project.updated_at = now
    await db.commit()
    return schemas.ProjectSummaryResponse(summary=summary, generated_at=now)
