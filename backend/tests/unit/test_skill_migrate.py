"""skill_migrate moves one agent's skill into the generic folder and links it back."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.core.exceptions import GenericSkillExistsError, InvalidSkillNameError
from app.services.skill_migrate import migrate_to_generic, normalize_frontmatter


def test_normalize_keeps_a_matching_name_byte_for_byte() -> None:
    content = "---\nname: tdd\ndescription: >\n  Folded text.\n---\n# TDD\n"
    assert normalize_frontmatter(content, "tdd") == (content, (), False)


def test_normalize_reports_claude_only_keys_but_keeps_them() -> None:
    content = (
        "---\nname: tdd\ndescription: x\n"
        "disable-model-invocation: true\nargument-hint: '[x]'\n---\nb\n"
    )
    out, claude_only, rewritten = normalize_frontmatter(content, "tdd")
    assert out == content
    assert claude_only == ("argument-hint", "disable-model-invocation")
    assert rewritten is False


def test_normalize_rewrites_a_name_that_does_not_match_the_folder() -> None:
    content = "---\nname: TDD Helper\ndescription: x\n---\nbody\n"
    out, _, rewritten = normalize_frontmatter(content, "tdd")
    assert out == "---\nname: tdd\ndescription: x\n---\nbody\n"
    assert rewritten is True


def test_normalize_adds_a_missing_name_and_missing_frontmatter() -> None:
    assert normalize_frontmatter("---\ndescription: x\n---\nbody\n", "tdd") == (
        "---\nname: tdd\ndescription: x\n---\nbody\n",
        (),
        True,
    )
    assert normalize_frontmatter("# just a body\n", "tdd") == (
        "---\nname: tdd\n---\n# just a body\n",
        (),
        True,
    )


@pytest.fixture
def roots(tmp_path: Path) -> dict[str, Path]:
    claude = tmp_path / "claude" / "skills"
    codex = tmp_path / "codex" / "skills"
    generic = tmp_path / "agents" / "skills"
    (claude / "tdd" / "references").mkdir(parents=True)
    (claude / "tdd" / "SKILL.md").write_text(
        "---\nname: tdd\ndescription: Test first.\ndisable-model-invocation: true\n---\n# TDD\n",
        encoding="utf-8",
    )
    (claude / "tdd" / "references" / "notes.md").write_text("companion\n", encoding="utf-8")
    return {"claude": claude, "codex": codex, "generic": generic}


def test_migrate_copies_links_back_and_links_the_other_agent(roots: dict[str, Path]) -> None:
    outcome = migrate_to_generic(
        "tdd",
        source_root=roots["claude"],
        generic_root=roots["generic"],
        agent_roots={"claude": roots["claude"], "codex": roots["codex"]},
    )

    target = roots["generic"] / "tdd"
    assert outcome.target == target
    assert (target / "SKILL.md").is_file() and not (target / "SKILL.md").is_symlink()
    assert (target / "references" / "notes.md").read_text() == "companion\n"
    # The real copy is the only one; both agents hold links to it.
    assert (roots["claude"] / "tdd").is_symlink()
    assert (roots["claude"] / "tdd").resolve() == target.resolve()
    assert (roots["codex"] / "tdd").is_symlink()
    assert (roots["codex"] / "tdd" / "SKILL.md").read_text().startswith("---\nname: tdd")
    assert outcome.linked == ("claude", "codex")
    assert outcome.skipped == ()
    assert outcome.claude_only_keys == ("disable-model-invocation",)
    assert outcome.name_rewritten is False
    assert not (roots["generic"] / ".masterwork-migrate-tdd").exists()


def test_migrate_leaves_another_agents_own_skill_of_that_name_alone(roots: dict[str, Path]) -> None:
    (roots["codex"] / "tdd").mkdir(parents=True)
    (roots["codex"] / "tdd" / "SKILL.md").write_text("---\nname: tdd\n---\ncodex's own\n")

    outcome = migrate_to_generic(
        "tdd",
        source_root=roots["claude"],
        generic_root=roots["generic"],
        agent_roots={"claude": roots["claude"], "codex": roots["codex"]},
    )

    assert outcome.linked == ("claude",)
    assert outcome.skipped == ("codex",)
    assert not (roots["codex"] / "tdd").is_symlink()
    assert (roots["codex"] / "tdd" / "SKILL.md").read_text().endswith("codex's own\n")


def test_migrate_refuses_a_differing_generic_copy(roots: dict[str, Path]) -> None:
    (roots["generic"] / "tdd").mkdir(parents=True)
    (roots["generic"] / "tdd" / "SKILL.md").write_text("---\nname: tdd\n---\nolder\n")

    with pytest.raises(GenericSkillExistsError, match="differs"):
        migrate_to_generic(
            "tdd",
            source_root=roots["claude"],
            generic_root=roots["generic"],
            agent_roots={"claude": roots["claude"]},
        )
    # Nothing moved: the source is still a real directory.
    assert not (roots["claude"] / "tdd").is_symlink()
    assert (roots["generic"] / "tdd" / "SKILL.md").read_text().endswith("older\n")


def test_migrate_replaces_a_differing_generic_copy_on_request(roots: dict[str, Path]) -> None:
    (roots["generic"] / "tdd" / "extra").mkdir(parents=True)
    (roots["generic"] / "tdd" / "SKILL.md").write_text("---\nname: tdd\n---\nolder\n")
    (roots["generic"] / "tdd" / "extra" / "gone.md").write_text("stale\n")

    outcome = migrate_to_generic(
        "tdd",
        source_root=roots["claude"],
        generic_root=roots["generic"],
        agent_roots={"claude": roots["claude"]},
        replace_generic=True,
    )

    assert outcome.replaced_generic is True and outcome.adopted is False
    assert (roots["generic"] / "tdd" / "SKILL.md").read_text().endswith("# TDD\n")
    assert not (roots["generic"] / "tdd" / "extra").exists()
    assert not (roots["generic"] / ".masterwork-old-tdd").exists()
    assert (roots["claude"] / "tdd").is_symlink()


def test_migrate_adopts_an_identical_generic_copy(roots: dict[str, Path]) -> None:
    shutil.copytree(roots["claude"] / "tdd", roots["generic"] / "tdd")
    before = (roots["generic"] / "tdd" / "SKILL.md").stat().st_mtime_ns

    outcome = migrate_to_generic(
        "tdd",
        source_root=roots["claude"],
        generic_root=roots["generic"],
        agent_roots={"claude": roots["claude"], "codex": roots["codex"]},
    )

    assert outcome.adopted is True and outcome.replaced_generic is False
    assert outcome.claude_only_keys == ("disable-model-invocation",)
    # The generic copy was not touched; the source became a link to it.
    assert (roots["generic"] / "tdd" / "SKILL.md").stat().st_mtime_ns == before
    assert (roots["claude"] / "tdd").is_symlink()
    assert (roots["codex"] / "tdd").is_symlink()
    assert outcome.linked == ("claude", "codex")


def test_migrate_refuses_a_source_that_is_already_a_link(roots: dict[str, Path]) -> None:
    shutil.move(str(roots["claude"] / "tdd"), str(roots["generic"] / "tdd"))
    (roots["claude"] / "tdd").symlink_to(roots["generic"] / "tdd", target_is_directory=True)

    with pytest.raises(GenericSkillExistsError, match="already a link"):
        migrate_to_generic(
            "tdd",
            source_root=roots["claude"],
            generic_root=roots["generic"],
            agent_roots={"claude": roots["claude"]},
        )


def test_migrate_rejects_a_bad_slug(roots: dict[str, Path]) -> None:
    with pytest.raises(InvalidSkillNameError):
        migrate_to_generic(
            "../escape",
            source_root=roots["claude"],
            generic_root=roots["generic"],
            agent_roots={"claude": roots["claude"]},
        )
