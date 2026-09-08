"""skill_toggle parks a skill under .disabled/ and brings it back, links and all."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.exceptions import SkillToggleConflictError
from app.providers.claude import ClaudeProvider
from app.providers.codex import CodexProvider
from app.providers.generic import GenericSkillProvider
from app.services.skill_toggle import set_skill_enabled


def _skill(root: Path, name: str) -> Path:
    (root / name / "references").mkdir(parents=True)
    (root / name / "SKILL.md").write_text(f"---\nname: {name}\n---\nbody\n", encoding="utf-8")
    (root / name / "references" / "notes.md").write_text("companion\n", encoding="utf-8")
    return root / name


@pytest.fixture
def roots(tmp_path: Path) -> dict[str, Path]:
    return {
        "claude": tmp_path / "claude" / "skills",
        "codex": tmp_path / "codex" / "skills",
        "generic": tmp_path / "agents" / "skills",
    }


def test_agent_skill_round_trips_through_disabled(roots: dict[str, Path]) -> None:
    claude = roots["claude"]
    _skill(claude, "tdd")

    off = set_skill_enabled("tdd", enabled=False, skills_root=claude)
    assert off.target == claude / ".disabled" / "tdd"
    assert off.relinked_agents == ()
    assert not (claude / "tdd").exists()
    assert (claude / ".disabled" / "tdd" / "references" / "notes.md").read_text() == "companion\n"

    on = set_skill_enabled("tdd", enabled=True, skills_root=claude)
    assert on.target == claude / "tdd"
    assert (claude / "tdd" / "SKILL.md").is_file()
    assert not (claude / ".disabled").exists()  # emptied, so tidied away


def test_generic_skill_moves_its_links_and_restores_only_those(roots: dict[str, Path]) -> None:
    generic, claude, codex = roots["generic"], roots["claude"], roots["codex"]
    real = _skill(generic, "shared")
    claude.mkdir(parents=True)
    codex.mkdir(parents=True)
    (claude / "shared").symlink_to(real, target_is_directory=True)
    _skill(codex, "shared")  # Codex has its own, unrelated skill of that name
    agent_roots = {"claude": claude, "codex": codex}

    off = set_skill_enabled("shared", enabled=False, skills_root=generic, agent_roots=agent_roots)
    assert off.relinked_agents == ("claude",)
    parked = generic / ".disabled" / "shared"
    assert parked.is_dir() and not (generic / "shared").exists()
    assert not (claude / "shared").exists()
    assert (claude / ".disabled" / "shared").is_symlink()
    assert (claude / ".disabled" / "shared").resolve() == parked.resolve()
    # Codex's own folder is untouched.
    assert (codex / "shared").is_dir() and not (codex / "shared").is_symlink()
    assert not (codex / ".disabled").exists()

    on = set_skill_enabled("shared", enabled=True, skills_root=generic, agent_roots=agent_roots)
    assert on.relinked_agents == ("claude",)
    assert (generic / "shared" / "SKILL.md").is_file()
    assert (claude / "shared").is_symlink()
    assert (claude / "shared").resolve() == (generic / "shared").resolve()
    assert not (claude / ".disabled").exists()
    assert not (generic / ".disabled").exists()
    assert not (codex / "shared").is_symlink()  # never linked, so no link was invented


def test_refuses_to_overwrite_an_existing_destination(roots: dict[str, Path]) -> None:
    claude = roots["claude"]
    _skill(claude, "tdd")
    _skill(claude / ".disabled", "tdd")

    with pytest.raises(SkillToggleConflictError, match="already exists"):
        set_skill_enabled("tdd", enabled=False, skills_root=claude)
    # Nothing moved.
    assert (claude / "tdd" / "SKILL.md").is_file()
    assert (claude / ".disabled" / "tdd" / "SKILL.md").is_file()


def test_refuses_a_blocked_link_destination_before_moving_anything(
    roots: dict[str, Path],
) -> None:
    generic, claude = roots["generic"], roots["claude"]
    real = _skill(generic, "shared")
    claude.mkdir(parents=True)
    (claude / "shared").symlink_to(real, target_is_directory=True)
    _skill(claude / ".disabled", "shared")  # something already parked under that name

    with pytest.raises(SkillToggleConflictError):
        set_skill_enabled(
            "shared", enabled=False, skills_root=generic, agent_roots={"claude": claude}
        )
    assert (generic / "shared").is_dir()
    assert (claude / "shared").is_symlink()


def test_a_failure_halfway_rolls_the_tree_back(
    roots: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    generic, claude, codex = roots["generic"], roots["claude"], roots["codex"]
    real = _skill(generic, "shared")
    for root in (claude, codex):
        root.mkdir(parents=True)
        (root / "shared").symlink_to(real, target_is_directory=True)

    # The second link's creation blows up after the folder and the first link moved.
    original = Path.symlink_to
    calls = {"n": 0}

    def flaky(self: Path, *args: object, **kwargs: object) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk says no")
        original(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "symlink_to", flaky)
    with pytest.raises(OSError, match="disk says no"):
        set_skill_enabled(
            "shared",
            enabled=False,
            skills_root=generic,
            agent_roots={"claude": claude, "codex": codex},
        )

    assert (generic / "shared" / "SKILL.md").is_file()
    assert not (generic / ".disabled" / "shared").exists()
    for root in (claude, codex):
        assert (root / "shared").is_symlink()
        assert (root / "shared").resolve() == real.resolve()
        assert not (root / ".disabled" / "shared").exists()


def test_providers_scan_a_parked_skill_as_disabled(roots: dict[str, Path]) -> None:
    generic, claude, codex = roots["generic"], roots["claude"], roots["codex"]
    _skill(claude, "mine")
    _skill(codex, "theirs")
    real = _skill(generic, "shared")
    (claude / "shared").symlink_to(real, target_is_directory=True)
    (codex / "shared").symlink_to(real, target_is_directory=True)
    agent_roots = {"claude": claude, "codex": codex}
    set_skill_enabled("mine", enabled=False, skills_root=claude)
    set_skill_enabled("theirs", enabled=False, skills_root=codex)
    set_skill_enabled("shared", enabled=False, skills_root=generic, agent_roots=agent_roots)

    claude_provider = ClaudeProvider(
        skills_root=claude, agents_root=roots["claude"].parent / "agents", generic_root=generic
    )
    codex_provider = CodexProvider(skills_root=codex, generic_root=generic)
    generic_provider = GenericSkillProvider(skills_root=generic, agent_roots=agent_roots)

    by_id = {a.id: a for p in (claude_provider, codex_provider, generic_provider) for a in p.scan()}
    # Same ids as before; the parked links into the generic folder are not duplicates.
    assert set(by_id) == {"claude:skill:mine", "codex:skill:theirs", "generic:skill:shared"}
    for asset in by_id.values():
        assert asset.disabled is True
        assert asset.agents == ()
    assert by_id["claude:skill:mine"].path == claude / ".disabled" / "mine" / "SKILL.md"
    assert by_id["generic:skill:shared"].path == generic / ".disabled" / "shared" / "SKILL.md"

    # A write aimed at the parked path is still owned by the same asset.
    assert claude_provider.asset_id_for_path(by_id["claude:skill:mine"].path) == "claude:skill:mine"
    assert codex_provider.asset_id_for_path(codex / ".disabled" / "theirs" / "SKILL.md") == (
        "codex:skill:theirs"
    )
    assert generic_provider.asset_id_for_path(claude / ".disabled" / "shared" / "SKILL.md") == (
        "generic:skill:shared"
    )
    assert claude_provider.asset_id_for_path(claude / ".disabled" / "shared" / "SKILL.md") is None
