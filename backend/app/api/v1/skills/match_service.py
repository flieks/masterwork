"""Describe-to-find over installed skills: one-shot agent CLI call over every
installed skill's name + description, picking the best-fitting few.

Mirrors app/api/v1/projects/links_service.py's shape, but matches a free-text
query against installed skills only rather than a project goal against every
asset on disk.
"""

from __future__ import annotations

from app.api.v1.assets.schemas import AssetKind
from app.api.v1.assets.service import list_assets
from app.api.v1.skills.schemas import SkillMatch, SkillMatchResponse
from app.core.exceptions import SkillMatchError
from app.providers.base import Provider
from app.services.agent_runner import AgentRunner, AgentRunnerError
from app.services.redact import redact
from app.services.skill_match_parser import extract_matches

_MAX_MATCHES = 8
_DESCRIPTION_LIMIT = 200


def _installed_skill_lines(providers: list[Provider]) -> tuple[list[str], set[str]]:
    """One "- name — description" line per installed skill, deduped by name (a
    generic skill is scanned once per agent root it is linked into), plus the
    set of names a model's reply may legally choose from."""
    seen: dict[str, str] = {}
    for asset in list_assets(providers, kind=AssetKind.skill):
        if asset.name in seen:
            continue
        description = asset.description.strip().replace("\n", " ")
        if len(description) > _DESCRIPTION_LIMIT:
            description = description[: _DESCRIPTION_LIMIT - 3] + "..."
        seen[asset.name] = description
    lines = sorted(redact(f"- {name} — {description}") for name, description in seen.items())
    return lines, set(seen)


def build_match_prompt(query: str, lines: list[str]) -> str:
    catalog = "\n".join(lines)
    return f"""\
You are matching a user's description of what they need to the installed \
coding-agent skills that best serve it.

WHAT THE USER WANTS
{query}

INSTALLED SKILLS
{catalog}

INSTRUCTIONS
Choose at most 8 skills from the list above that best fit what the user wants, \
best match first. Pick names EXACTLY as they appear in the list, nothing else.

Reply with ONLY a JSON array of this shape (valid JSON, no other text):
[{{"name": "example-skill", "reason": "one line: why it fits"}}]
"""


async def match_installed(
    providers: list[Provider], runner: AgentRunner, query: str
) -> SkillMatchResponse:
    """The model's best-fitting installed skills for a free-text query.

    An empty surviving list is a real answer ("nothing matched") and returns
    200; only a runner failure or an unparseable reply is a SkillMatchError.
    """
    lines, known = _installed_skill_lines(providers)
    prompt = build_match_prompt(query, lines)
    try:
        reply = await runner.run_once(prompt)
    except AgentRunnerError as exc:
        raise SkillMatchError(f"{runner.display_name} failed to match skills: {exc}") from exc

    parsed = extract_matches(reply)
    if parsed is None:
        raise SkillMatchError("the assistant returned no usable matches")

    seen: set[str] = set()
    matches: list[SkillMatch] = []
    for match in parsed:
        if match.name not in known or match.name in seen:
            continue
        seen.add(match.name)
        matches.append(SkillMatch(name=match.name, reason=match.reason))
        if len(matches) >= _MAX_MATCHES:
            break
    return SkillMatchResponse(matches=matches)
