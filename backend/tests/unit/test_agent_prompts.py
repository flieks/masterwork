"""Prompts name the agent that runs them and no agent's tool names."""

from __future__ import annotations

from app.api.v1.projects.links_service import build_links_prompt
from app.api.v1.projects.trigger_service import build_trigger_prompt
from app.api.v1.simulations.service import build_prompt, build_scenario_prompt
from app.db.models.project import Project
from app.services.assistant_prompts import app_system_prompt


def _project() -> Project:
    return Project(name="p", goal="ship it", flow_mermaid=None, asset_ids=[])


def test_system_prompt_lists_every_agents_asset_locations() -> None:
    prompt = app_system_prompt("Codex")
    assert "running on Codex" in prompt
    for location in (
        "~/.claude/skills/<name>/SKILL.md",
        "~/.claude/agents/<name>.md",
        "~/.codex/skills/<name>/SKILL.md",
        "~/.codex/agents/<name>.toml",
        "~/.agents/skills/<name>/SKILL.md",
    ):
        assert location in prompt
    assert "Glob" not in prompt and "Grep" not in prompt and "Read tool" not in prompt


def test_simulation_prompts_simulate_the_active_agent() -> None:
    assert "the way Codex would" in build_prompt(_project(), [], "s", agent_name="Codex")
    assert "type to Codex" in build_scenario_prompt(_project(), [], agent_name="Codex")
    assert "Read tool" not in build_prompt(_project(), [], "s", agent_name="Claude Code")


def test_trigger_guide_for_codex_explains_dollar_mentions() -> None:
    codex = build_trigger_prompt(_project(), [], agent_id="codex", agent_name="Codex")
    assert "`$skill-name`" in codex
    assert "prompts for Codex" in codex
    assert "invisible to it" in codex  # Claude-only assets named as unreachable

    claude = build_trigger_prompt(_project(), [], agent_id="claude", agent_name="Claude Code")
    assert "$skill-name" not in claude
    assert "prompts for Claude Code" in claude


def test_link_suggestions_show_both_id_forms() -> None:
    prompt = build_links_prompt(_project(), [])
    assert "claude:skill:example" in prompt
    assert "codex:skill:" in prompt
