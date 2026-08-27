"""skill_install.py: staging-then-replace disk writes, no HTTP involved.
`tmp_path` stands in for the skills root — no test ever touches ~/.claude/skills."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.exceptions import (
    InvalidSkillNameError,
    SkillAlreadyInstalledError,
    SkillFetchError,
    SkillLicenseRefusedError,
)
from app.services import skill_install
from app.services.skill_catalog import MAX_SKILL_BYTES, FetchedSkill, SkillFile


def _fetched(
    *,
    skill_md: str = "# A skill\n",
    files: list[SkillFile] | None = None,
    root_path: str = "my-skill",
) -> FetchedSkill:
    return FetchedSkill(skill_md=skill_md, files=files or [], root_path=root_path)


def test_install_writes_skill_md_and_companion_files(tmp_path: Path) -> None:
    fetched = _fetched(files=[SkillFile(relative_path="reference.md", content=b"details")])

    target = skill_install.install_skill(fetched, slug="my-skill", skills_root=tmp_path)

    assert target == tmp_path / "my-skill"
    assert (target / "SKILL.md").read_text() == "# A skill\n"
    assert (target / "reference.md").read_bytes() == b"details"
    assert list(tmp_path.iterdir()) == [target]  # no staging leftovers


def test_install_refuses_a_companion_path_that_escapes_the_skill_folder(tmp_path: Path) -> None:
    fetched = _fetched(files=[SkillFile(relative_path="../evil.md", content=b"pwned")])

    with pytest.raises(SkillFetchError):
        skill_install.install_skill(fetched, slug="my-skill", skills_root=tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_install_refuses_an_absolute_companion_path(tmp_path: Path) -> None:
    fetched = _fetched(files=[SkillFile(relative_path="/etc/passwd", content=b"pwned")])

    with pytest.raises(SkillFetchError):
        skill_install.install_skill(fetched, slug="my-skill", skills_root=tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_install_refuses_over_the_byte_cap(tmp_path: Path) -> None:
    fetched = _fetched(
        files=[SkillFile(relative_path="big.bin", content=b"x" * (MAX_SKILL_BYTES + 1))]
    )

    with pytest.raises(SkillFetchError):
        skill_install.install_skill(fetched, slug="my-skill", skills_root=tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_install_refuses_to_overwrite_without_the_flag(tmp_path: Path) -> None:
    skill_install.install_skill(
        _fetched(skill_md="# Original\n"), slug="my-skill", skills_root=tmp_path
    )

    with pytest.raises(SkillAlreadyInstalledError):
        skill_install.install_skill(
            _fetched(skill_md="# New\n"), slug="my-skill", skills_root=tmp_path
        )

    assert (tmp_path / "my-skill" / "SKILL.md").read_text() == "# Original\n"


def test_install_replaces_with_overwrite(tmp_path: Path) -> None:
    skill_install.install_skill(
        _fetched(skill_md="# Original\n"), slug="my-skill", skills_root=tmp_path
    )

    target = skill_install.install_skill(
        _fetched(skill_md="# New\n"), slug="my-skill", skills_root=tmp_path, overwrite=True
    )

    assert (target / "SKILL.md").read_text() == "# New\n"
    assert list(tmp_path.iterdir()) == [target]  # the moved-aside original is cleaned up


def test_mid_write_failure_leaves_no_directory_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fetched = _fetched(
        files=[
            SkillFile(relative_path="a.md", content=b"a"),
            SkillFile(relative_path="b.md", content=b"b"),
        ]
    )
    real_write_bytes = Path.write_bytes
    calls = {"n": 0}

    def flaky_write_bytes(self: Path, data: bytes) -> int:
        calls["n"] += 1
        if calls["n"] == 2:  # SKILL.md succeeds, the first companion file fails
            raise OSError("disk full")
        return real_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", flaky_write_bytes)

    with pytest.raises(OSError):
        skill_install.install_skill(fetched, slug="my-skill", skills_root=tmp_path)

    assert list(tmp_path.iterdir()) == []


def test_invalid_slug_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(InvalidSkillNameError):
        skill_install.install_skill(_fetched(), slug="Not_Kebab", skills_root=tmp_path)


def test_uninstall_refuses_an_invalid_slug(tmp_path: Path) -> None:
    with pytest.raises(InvalidSkillNameError):
        skill_install.uninstall_skill("Not_Kebab", skills_root=tmp_path)


def test_uninstall_removes_the_directory(tmp_path: Path) -> None:
    skill_install.install_skill(_fetched(), slug="my-skill", skills_root=tmp_path)

    skill_install.uninstall_skill("my-skill", skills_root=tmp_path)

    assert not (tmp_path / "my-skill").exists()


def test_check_installable_refuses_anthropics_skills_document_skills() -> None:
    for skill in ("docx", "pdf", "pptx", "xlsx"):
        with pytest.raises(SkillLicenseRefusedError, match="license"):
            skill_install.check_installable("anthropics", "skills", skill)


def test_check_installable_allows_other_repos_and_other_skills() -> None:
    skill_install.check_installable("anthropics", "skills", "some-other-skill")
    skill_install.check_installable("someone", "skills", "pdf")
