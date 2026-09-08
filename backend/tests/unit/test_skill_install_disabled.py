"""A catalog skill the user switched off is still an install: it can be found,
read, updated in place and uninstalled from its parked folder."""

from __future__ import annotations

from pathlib import Path

from app.providers.base import DISABLED_DIR
from app.services import skill_install


def _park(skills_root: Path, slug: str) -> Path:
    folder = skills_root / DISABLED_DIR / slug
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text("---\nname: x\n---\nparked\n", encoding="utf-8")
    return folder


def test_a_parked_skill_is_still_installed(tmp_path: Path) -> None:
    folder = _park(tmp_path, "parked")
    assert skill_install.installed_dir("parked", skills_root=tmp_path) == folder
    assert skill_install.is_installed("parked", skills_root=tmp_path)
    assert skill_install.read_installed_skill_md("parked", skills_root=tmp_path) == (
        "---\nname: x\n---\nparked\n"
    )
    assert skill_install.read_installed_files("parked", skills_root=tmp_path) == {
        "SKILL.md": b"---\nname: x\n---\nparked\n"
    }


def test_uninstall_removes_the_parked_copy(tmp_path: Path) -> None:
    folder = _park(tmp_path, "parked")
    skill_install.uninstall_skill("parked", skills_root=tmp_path)
    assert not folder.exists()
    assert not (tmp_path / "parked").exists()


def test_the_enabled_copy_wins_when_both_exist(tmp_path: Path) -> None:
    _park(tmp_path, "twice")
    (tmp_path / "twice").mkdir()
    assert skill_install.installed_dir("twice", skills_root=tmp_path) == tmp_path / "twice"
