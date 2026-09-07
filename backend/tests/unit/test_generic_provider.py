"""The generic (~/.agents/skills) and Codex providers scan temp trees, and the
per-agent providers skip the links that point into the generic folder."""

from __future__ import annotations

from pathlib import Path

from app.providers.claude import ClaudeProvider
from app.providers.codex import CodexProvider
from app.providers.generic import GenericSkillProvider


def _skill(root: Path, name: str, body: str = "body") -> Path:
    (root / name).mkdir(parents=True)
    (root / name / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {name} does things.\n---\n{body}\n", encoding="utf-8"
    )
    return root / name


def test_generic_scan_reports_which_agents_link_to_each_skill(tmp_path: Path) -> None:
    generic = tmp_path / "agents" / "skills"
    claude = tmp_path / "claude" / "skills"
    codex = tmp_path / "codex" / "skills"
    shared = _skill(generic, "shared")
    _skill(generic, "orphan")
    claude.mkdir(parents=True)
    codex.mkdir(parents=True)
    (claude / "shared").symlink_to(shared, target_is_directory=True)
    _skill(codex, "shared")  # a real, unrelated Codex skill of the same name

    provider = GenericSkillProvider(
        skills_root=generic, agent_roots={"claude": claude, "codex": codex}
    )
    assets = {a.name: a for a in provider.scan()}

    assert set(assets) == {"shared", "orphan"}
    assert assets["shared"].provider == "generic"
    assert assets["shared"].id == "generic:skill:shared"
    assert assets["shared"].agents == ("claude",)  # codex has its own copy, not a link
    assert assets["orphan"].agents == ()


def test_agent_providers_skip_links_into_the_generic_folder(tmp_path: Path) -> None:
    generic = tmp_path / "agents" / "skills"
    claude = tmp_path / "claude" / "skills"
    codex = tmp_path / "codex" / "skills"
    shared = _skill(generic, "shared")
    _skill(claude, "mine")
    _skill(codex, "theirs")
    (claude / "shared").symlink_to(shared, target_is_directory=True)
    (codex / "shared").symlink_to(shared, target_is_directory=True)

    claude_assets = {
        a.id
        for a in ClaudeProvider(
            skills_root=claude, agents_root=tmp_path / "none", generic_root=generic
        ).scan()
    }
    codex_assets = {a.id for a in CodexProvider(skills_root=codex, generic_root=generic).scan()}

    assert claude_assets == {"claude:skill:mine"}
    assert codex_assets == {"codex:skill:theirs"}


def test_without_a_generic_root_links_are_scanned_as_the_agents_own(tmp_path: Path) -> None:
    generic = tmp_path / "agents" / "skills"
    claude = tmp_path / "claude" / "skills"
    shared = _skill(generic, "shared")
    claude.mkdir(parents=True)
    (claude / "shared").symlink_to(shared, target_is_directory=True)

    provider = ClaudeProvider(skills_root=claude, agents_root=tmp_path / "none")
    assert {a.id for a in provider.scan()} == {"claude:skill:shared"}


def test_codex_skips_hidden_system_dir_and_tags_agent(tmp_path: Path) -> None:
    codex = tmp_path / "codex" / "skills"
    _skill(codex, "theirs")
    _skill(codex / ".system", "imagegen")

    provider = CodexProvider(skills_root=codex)
    assets = list(provider.scan())

    assert [a.id for a in assets] == ["codex:skill:theirs"]
    assert assets[0].agents == ("codex",)
    assert provider.asset_id_for_path(codex / "theirs" / "SKILL.md") == "codex:skill:theirs"
    assert provider.asset_id_for_path(codex / ".system" / "imagegen" / "SKILL.md") is None


def test_generic_asset_id_for_path_follows_a_link(tmp_path: Path) -> None:
    generic = tmp_path / "agents" / "skills"
    claude = tmp_path / "claude" / "skills"
    shared = _skill(generic, "shared")
    claude.mkdir(parents=True)
    (claude / "shared").symlink_to(shared, target_is_directory=True)

    provider = GenericSkillProvider(skills_root=generic, agent_roots={"claude": claude})
    claude_provider = ClaudeProvider(
        skills_root=claude, agents_root=tmp_path / "none", generic_root=generic
    )

    # A write aimed at the Claude path lands on the generic copy and is owned by it.
    assert provider.asset_id_for_path(claude / "shared" / "SKILL.md") == "generic:skill:shared"
    assert claude_provider.asset_id_for_path(claude / "shared" / "SKILL.md") is None


def test_missing_roots_scan_to_nothing(tmp_path: Path) -> None:
    assert list(GenericSkillProvider(skills_root=tmp_path / "nope", agent_roots={}).scan()) == []
    assert list(CodexProvider(skills_root=tmp_path / "nope").scan()) == []
