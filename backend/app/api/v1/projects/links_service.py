"""Asset-link suggestions — the automated answer to "which assets should this
project use?".

One-shot claude -p over the FULL on-disk catalog (skills + agents, linked or
not). Returns the complete recommended toolkit with a one-line reason per
asset; nothing is persisted — the user reviews and saves the links themselves.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.projects import schemas, service
from app.core.exceptions import LinkSuggestionError
from app.db.models.project import Project
from app.providers.base import Provider
from app.services.claude_runner import ClaudeRunner, ClaudeRunnerError
from app.services.links_parser import extract_links
from app.services.redact import redact


def _catalog_lines(providers: list[Provider], linked_ids: list[str]) -> list[str]:
    linked = set(linked_ids)
    lines: list[str] = []
    for provider in providers:
        for asset in provider.scan():
            mark = " (currently linked)" if asset.id in linked else ""
            description = asset.description.strip().replace("\n", " ")
            if len(description) > 200:
                description = description[:197] + "..."
            lines.append(redact(f"- {asset.id}{mark} — {description}\n  file: {asset.path}"))
    return sorted(lines)


def build_links_prompt(project: Project, providers: list[Provider]) -> str:
    catalog = "\n".join(_catalog_lines(providers, list(project.asset_ids)))
    flow = project.flow_mermaid or "(none)"
    return f"""\
You are choosing the TOOLKIT for a project: which of the AI-coding assets \
(Claude Code skills and subagents) already on this machine should be linked to \
it. Linked assets become the toolkit that simulations evaluate against the goal.

PROJECT
- name: {project.name}
- goal:
{project.goal or "(empty)"}
- intended flow (mermaid):
{flow}

ASSET CATALOG (everything on disk):
{catalog}

INSTRUCTIONS
1. From the catalog, shortlist the assets whose descriptions plausibly serve \
the goal. Read each shortlisted file with your Read tool and confirm against \
its ACTUAL trigger text and steps — not what its name implies.
2. Select the complete recommended toolkit. Be lean: an asset earns its place \
only if the goal exercises it — a kitchen-sink list dilutes every simulation. \
Include both skills and subagents. Drop currently-linked assets that do not \
serve the goal.
3. When two assets overlap, pick the better fit and score the loser low; say \
which one beat it in its reason.
4. Score every listed asset 0-100 for how strongly THIS goal exercises it:
   - 85-100: load-bearing — the goal repeatedly and directly needs it; the \
toolkit is incomplete without it.
   - 70-84: clearly serves a main path of the goal.
   - 60-69: useful but adjacent — the goal touches it occasionally.
   - 40-59: borderline — plausible, but the goal may never trigger it. List \
these too, with the doubt spelled out in the reason: the user decides.
   - Below 40: leave it out of the list entirely.
   Spread the scores. If everything lands in one band you have not judged, you \
have just agreed with yourself.

Reply with ONLY a fenced code block whose info string is `links` containing \
JSON of this shape (valid JSON, highest confidence first):

```links
{{
  "links": [
    {{"asset_id": "claude:skill:example", "confidence": 90, \
"reason": "one line: why the goal needs it — or why it is borderline"}}
  ]
}}
```
"""


async def suggest_links(
    db: AsyncSession,
    providers: list[Provider],
    runner: ClaudeRunner,
    project_id: str,
) -> schemas.ProjectSuggestLinksResponse:
    """Recommend the complete asset set for a project; persists nothing."""
    project = await service.get_project_or_404(db, project_id)

    prompt = build_links_prompt(project, providers)
    try:
        reply = await runner.run_once(prompt)
    except ClaudeRunnerError as exc:
        raise LinkSuggestionError(f"claude failed to suggest links: {exc}") from exc

    parsed = extract_links(reply)
    if parsed is None:
        raise LinkSuggestionError("the assistant returned no usable links block")

    known = {asset.id for provider in providers for asset in provider.scan()}
    suggestions = [
        schemas.SuggestedLink(
            asset_id=link.asset_id, reason=link.reason, confidence=link.confidence
        )
        for link in parsed
        if link.asset_id in known
    ]
    if not suggestions:
        raise LinkSuggestionError("the assistant suggested no known asset ids")
    # Re-sort rather than trust the model's ordering; ties keep its order.
    suggestions.sort(key=lambda s: s.confidence, reverse=True)
    return schemas.ProjectSuggestLinksResponse(suggestions=suggestions)
