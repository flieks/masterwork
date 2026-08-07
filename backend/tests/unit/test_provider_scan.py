"""ClaudeProvider scans a temp tree (never the real ~/.claude)."""

from __future__ import annotations

from pathlib import Path

from app.providers.claude import ClaudeProvider, parse_frontmatter


def test_scan_finds_skills_and_agents(claude_tree: tuple[Path, Path]) -> None:
    skills_root, agents_root = claude_tree
    provider = ClaudeProvider(skills_root=skills_root, agents_root=agents_root)

    assets = {a.id: a for a in provider.scan()}

    assert set(assets) == {
        "claude:skill:frontend-dev",
        "claude:skill:backend-dev",
        "claude:agent:architect",
    }
    skill = assets["claude:skill:frontend-dev"]
    assert skill.kind == "skill"
    assert skill.provider == "claude"
    assert skill.name == "frontend-dev"
    assert skill.title == "frontend-dev"
    assert skill.description == "React frontend guidelines."
    assert skill.path == skills_root / "frontend-dev" / "SKILL.md"
    assert "Use React and Vite." in skill.content


def test_scan_ignores_dirs_without_skill_md(claude_tree: tuple[Path, Path]) -> None:
    skills_root, agents_root = claude_tree
    (skills_root / "not-a-skill").mkdir()  # no SKILL.md inside
    provider = ClaudeProvider(skills_root=skills_root, agents_root=agents_root)

    names = {a.name for a in provider.scan() if a.kind == "skill"}
    assert "not-a-skill" not in names


def test_missing_roots_do_not_crash(tmp_path: Path) -> None:
    provider = ClaudeProvider(skills_root=tmp_path / "nope", agents_root=tmp_path / "nada")
    assert list(provider.scan()) == []


def test_malformed_frontmatter_falls_back(claude_tree: tuple[Path, Path]) -> None:
    skills_root, agents_root = claude_tree
    broken = skills_root / "broken"
    broken.mkdir()
    (broken / "SKILL.md").write_text("---\nname: [unterminated\n---\nbody\n", encoding="utf-8")
    provider = ClaudeProvider(skills_root=skills_root, agents_root=agents_root)

    asset = next(a for a in provider.scan() if a.name == "broken")
    assert asset.title == "broken"  # filename fallback
    assert asset.description == ""


def test_parse_frontmatter_edge_cases() -> None:
    assert parse_frontmatter("no frontmatter here") == {}
    assert parse_frontmatter("---\nname: x\n") == {}  # unterminated
    assert parse_frontmatter("---\nname: x\n---\nbody") == {"name": "x"}


def test_asset_id_for_path_roundtrip(claude_tree: tuple[Path, Path]) -> None:
    skills_root, agents_root = claude_tree
    provider = ClaudeProvider(skills_root=skills_root, agents_root=agents_root)

    skill_path = skills_root / "frontend-dev" / "SKILL.md"
    agent_path = agents_root / "architect.md"
    assert provider.asset_id_for_path(skill_path) == "claude:skill:frontend-dev"
    assert provider.asset_id_for_path(agent_path) == "claude:agent:architect"
    assert provider.asset_id_for_path(skills_root / "other.txt") is None
