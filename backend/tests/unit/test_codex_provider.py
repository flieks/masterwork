"""CodexProvider (v1.49): custom agents in ~/.codex/agents, and skills switched
off by `[[skills.config]]` in config.toml."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.exceptions import InvalidAssetContentError
from app.providers.codex import CodexProvider, parse_agent_toml
from app.providers.generic import GenericSkillProvider

AGENT = """\
name = "reviewer"
description = "Reviews a diff before it lands."
developer_instructions = \"\"\"
Read the diff. Say what breaks.
\"\"\"
model = "gpt-5.6"
model_reasoning_effort = "high"
"""


def _skill(root: Path, name: str) -> Path:
    (root / name).mkdir(parents=True)
    path = root / name / "SKILL.md"
    path.write_text(f"---\nname: {name}\ndescription: {name} things\n---\n", encoding="utf-8")
    return path


@pytest.fixture
def codex_home(tmp_path: Path) -> Path:
    home = tmp_path / "codex-home"
    (home / "agents").mkdir(parents=True)
    (home / "agents" / "reviewer.toml").write_text(AGENT, encoding="utf-8")
    (home / "agents" / "broken.toml").write_text("name = [unclosed", encoding="utf-8")
    (home / "agents" / "notes.md").write_text("not an agent", encoding="utf-8")
    (home / "agents" / ".hidden.toml").write_text(AGENT, encoding="utf-8")
    _skill(home / "skills", "deploy")
    return home


def _provider(home: Path, **kwargs: object) -> CodexProvider:
    return CodexProvider(
        skills_root=home / "skills",
        agents_root=home / "agents",
        config_file=home / "config.toml",
        **kwargs,  # type: ignore[arg-type]
    )


def test_custom_agents_are_indexed_from_their_toml(codex_home: Path) -> None:
    by_id = {a.id: a for a in _provider(codex_home).scan()}
    assert set(by_id) == {"codex:skill:deploy", "codex:agent:reviewer", "codex:agent:broken"}
    reviewer = by_id["codex:agent:reviewer"]
    assert reviewer.kind == "agent"
    assert reviewer.title == "reviewer"
    assert reviewer.description == "Reviews a diff before it lands."
    assert reviewer.model == "gpt-5.6"
    assert reviewer.agents == ("codex",)
    assert reviewer.read_only is False
    assert reviewer.content == AGENT


def test_an_agent_that_does_not_parse_is_still_listed_to_be_fixed(codex_home: Path) -> None:
    broken = next(a for a in _provider(codex_home).scan() if a.name == "broken")
    assert broken.title == "broken"
    assert broken.description == ""


def test_agents_root_is_writable_and_mapped_back(codex_home: Path) -> None:
    provider = _provider(codex_home)
    assert provider.roots() == [codex_home / "skills", codex_home / "agents"]
    assert provider.asset_id_for_path(codex_home / "agents" / "reviewer.toml") == (
        "codex:agent:reviewer"
    )
    assert provider.asset_id_for_path(codex_home / "agents" / "notes.md") is None
    assert provider.is_agent_file(codex_home / "agents" / "new-one.toml")


def test_without_an_agents_root_nothing_is_indexed_or_writable(codex_home: Path) -> None:
    provider = CodexProvider(skills_root=codex_home / "skills")
    assert provider.roots() == [codex_home / "skills"]
    assert [a.id for a in provider.scan()] == ["codex:skill:deploy"]


def test_refs_match_the_scan_without_reading_files(codex_home: Path) -> None:
    provider = _provider(codex_home)
    assert {r.id for r in provider.asset_refs()} == {a.id for a in provider.scan()}


def test_skills_config_disables_a_codex_skill(codex_home: Path) -> None:
    other = _skill(codex_home / "skills", "keep")
    (codex_home / "config.toml").write_text(
        f'model = "gpt-5.6"\n\n[[skills.config]]\npath = "{codex_home}/skills/deploy/SKILL.md"\n'
        f'enabled = false\n\n[[skills.config]]\npath = "{other}"\nenabled = true\n',
        encoding="utf-8",
    )
    by_name = {a.name: a for a in _provider(codex_home).scan() if a.kind == "skill"}
    assert by_name["deploy"].disabled is True
    assert by_name["deploy"].disabled_by == "codex-config"
    assert by_name["deploy"].agents == ()
    assert by_name["keep"].disabled is False
    assert by_name["keep"].disabled_by is None


def test_a_parked_skill_says_so(codex_home: Path) -> None:
    _skill(codex_home / "skills" / ".disabled", "parked")
    parked = next(a for a in _provider(codex_home).scan() if a.name == "parked")
    assert parked.disabled_by == "folder"


def test_an_unparseable_config_disables_nothing(codex_home: Path) -> None:
    (codex_home / "config.toml").write_text("[[skills.config", encoding="utf-8")
    assert not any(a.disabled for a in _provider(codex_home).scan())


def test_a_generic_skill_switched_off_for_codex_keeps_claude(tmp_path: Path) -> None:
    generic, claude, codex = tmp_path / "agents", tmp_path / "claude", tmp_path / "codex"
    skill = _skill(generic, "shared")
    for root in (claude, codex):
        root.mkdir()
        (root / "shared").symlink_to(generic / "shared", target_is_directory=True)
    config = tmp_path / "config.toml"
    # Written through the Codex link: compared resolved, like Codex dedupes it.
    config.write_text(
        f'[[skills.config]]\npath = "{codex}/shared/SKILL.md"\nenabled = false\n', encoding="utf-8"
    )
    provider = GenericSkillProvider(
        skills_root=generic,
        agent_roots={"claude": claude, "codex": codex},
        codex_config_file=config,
    )
    (asset,) = provider.scan()
    assert asset.path == skill
    assert asset.agents == ("claude",)
    assert asset.disabled is False


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("name = [", "not valid TOML"),
        ('name = "x"\ndescription = "y"\n', "missing: developer_instructions"),
        ('name = "x"\ndescription = 3\ndeveloper_instructions = "z"\n', "must be strings"),
    ],
)
def test_agent_toml_validation(content: str, message: str) -> None:
    with pytest.raises(InvalidAssetContentError, match=message):
        parse_agent_toml(content)
    assert parse_agent_toml(AGENT)["name"] == "reviewer"
