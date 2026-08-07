"""Generality audit.

One-shot claude -p that READS every linked asset file and checks whether the
shared, reusable skills/agents stayed general — or whether a past scenario's
domain leaked into them. The counterpart to the anti-overfitting guard in the
simulation prompt (build_prompt): that guard stops FUTURE runs from baking one
scenario's product logic into shared assets; this audit catches what PAST runs
already did. Read-only markdown report, persisted so the tab survives reloads.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.projects import schemas, service
from app.core.exceptions import GeneralityGenerationError
from app.db.models.project import Project
from app.providers.base import Provider
from app.services.claude_runner import ClaudeRunner, ClaudeRunnerError
from app.services.generality_parser import extract_generality
from app.services.redact import redact
from app.services.shared_assets import shared_asset_notes

EMPTY_REPORT = (
    "_No assets linked yet — link the skills and agents this project should use "
    "(Overview tab), then audit again._"
)


def _asset_lines(
    providers: list[Provider],
    asset_ids: list[str],
    shared_notes: dict[str, str] | None = None,
) -> list[str]:
    index = {asset.id: asset for provider in providers for asset in provider.scan()}
    lines: list[str] = []
    for asset_id in asset_ids:
        asset = index.get(asset_id)
        if asset is None:
            lines.append(f"- {asset_id} — MISSING: the linked file no longer exists on disk")
            continue
        writable = "read-only (plugin)" if asset.read_only else "writable"
        line = (
            f"- {asset_id} — {asset.title} [{asset.kind}, {writable}]: {asset.description}"
            f"\n  file: {asset.path}"
        )
        if shared_notes and asset_id in shared_notes:
            line += f"\n  SHARED with: {shared_notes[asset_id]}"
        lines.append(redact(line))
    return lines


def build_generality_prompt(
    project: Project,
    providers: list[Provider],
    shared_notes: dict[str, str] | None = None,
) -> str:
    assets_text = "\n".join(_asset_lines(providers, list(project.asset_ids), shared_notes))
    return f"""\
You are auditing a project's AI-coding assets (Claude Code skills and subagents) \
for GENERALITY. These assets are SHARED and reusable — the whole point is that \
they work for ANY tool the user builds, not one specific app. A past series of \
simulations may have ground repeatedly on ONE scenario's domain and leaked its \
specifics into these shared assets, quietly overfitting them.

PROJECT
- name: {project.name}
- goal:
{project.goal or "(empty)"}

LINKED ASSETS (the shared toolkit under audit):
{assets_text}

INSTRUCTIONS
1. Read EVERY linked asset file above with your Read tool. Ground every finding \
in what the file actually says — quote the exact offending line.
2. For each asset, decide whether it stayed GENERAL or leaked a specific \
scenario's domain. Judge by these rules:
   - LEAK: a specific product's domain hardcoded as THE running example — a \
single identifier named after one domain threaded through the asset (a container \
/ database / endpoint / function / variable named `receipts`, `invoices`, etc.), \
or scenario-specific business rules or enums baked into a general asset. A fresh \
unrelated tool reading this would be misled into copying the wrong noun.
   - SEVERE LEAK: actual PRODUCT logic baked in — the app's headline feature/\
intelligence (e.g. OCR, receipt categorization, expense classification). This is \
what the developer builds per product; it must never live in a reusable asset.
   - NOT a leak (general, leave alone): a *rule* phrased generically ("any \
endpoint taking an `UploadFile`", "an existing Flexible Server") — even if it \
lists domain examples — AND a LIST of co-examples like "(receipts, invoices, \
images, PDFs)", which reads as illustration, not as the app's real domain. A \
single running identifier is a leak; a list of examples is not.
3. An asset annotated "SHARED with:" also serves the other project goals listed \
there. Additionally flag (severity `medium` or worse) any content that would \
degrade or contradict one of those goals — e.g. this project's stack or workflow \
stated as the only way, where the other goal needs a different one.
4. Rate each finding severity: `severe` (product logic), `medium` (a running \
identifier colonizing the asset), `low` (an incidental domain-named example), or \
`clean` (general).
5. For every leak, give a concrete generalization: the neutral placeholder or \
rewording that keeps the rule identical but removes the specific domain (e.g. \
`receipts` container → `uploads`; `CREATE DATABASE receipts;` → `CREATE DATABASE \
<app>;`). Note read-only (plugin) assets cannot be edited — report them, but say \
the fix must go upstream.

OUTPUT
Write a markdown report with exactly these sections:
- `## Verdict` — one line: overall, did the toolkit stay general? Then a totals \
line: N assets clean, N low, N medium, N severe.
- `## Findings` — one `### <asset-id>` subsection per asset that is NOT clean, \
worst severity first. Open each with a bold severity tag (**severe** / \
**medium** / **low**), then: what leaked (quote the exact line(s) in a fenced \
block), why it is scenario-specific not general, and the concrete generalization.
- `## Clean` — a single bullet list of the asset ids that are general, each with \
a few-word reason (e.g. "rules phrased for any UploadFile; examples are a list").
Be concrete and quote real lines; plain markdown only (no HTML).

Reply with ONLY a fenced code block whose info string is `generality` containing \
the markdown — no prose before or after.
"""


async def generate_generality_report(
    db: AsyncSession,
    providers: list[Provider],
    runner: ClaudeRunner,
    project_id: str,
) -> schemas.ProjectGeneralityResponse:
    """Synchronously generate and persist the generality audit for a project."""
    project = await service.get_project_or_404(db, project_id)

    if not project.asset_ids:
        report = EMPTY_REPORT
    else:
        shared = await shared_asset_notes(db, exclude_project_id=project.id)
        prompt = build_generality_prompt(project, providers, shared)
        try:
            reply = await runner.run_once(prompt)
        except ClaudeRunnerError as exc:
            raise GeneralityGenerationError(f"claude failed to audit generality: {exc}") from exc
        report = extract_generality(reply) or reply.strip()
        if not report:
            raise GeneralityGenerationError("the assistant returned an empty report")

    now = datetime.now(tz=UTC)
    project.generality_report = report
    project.generality_report_at = now
    project.updated_at = now
    await db.commit()
    return schemas.ProjectGeneralityResponse(generality_report=report, generated_at=now)
