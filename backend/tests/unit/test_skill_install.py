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


# --- drift ------------------------------------------------------------------


def test_tree_hash_is_order_independent_and_content_sensitive() -> None:
    a = skill_install.tree_hash({"SKILL.md": b"# A\n", "ref.md": b"x"})
    b = skill_install.tree_hash({"ref.md": b"x", "SKILL.md": b"# A\n"})
    assert a == b
    assert a != skill_install.tree_hash({"SKILL.md": b"# A\n", "ref.md": b"y"})
    assert a != skill_install.tree_hash({"SKILL.md": b"# A\n", "other.md": b"x"})  # path counts


def test_tree_hash_matches_between_a_fetch_and_the_folder_it_wrote(tmp_path: Path) -> None:
    """The install baseline is hashed from disk; the check hashes the fetch.
    Both must agree or every fresh install would report upstream_changed."""
    fetched = _fetched(files=[SkillFile(relative_path="docs/ref.md", content=b"details")])
    skill_install.install_skill(fetched, slug="my-skill", skills_root=tmp_path)

    on_disk = skill_install.read_installed_files("my-skill", skills_root=tmp_path)

    assert on_disk == {"SKILL.md": b"# A skill\n", "docs/ref.md": b"details"}
    assert skill_install.tree_hash(on_disk) == skill_install.tree_hash(
        skill_install.fetched_files(fetched)
    )


def test_read_installed_files_is_none_for_a_missing_folder(tmp_path: Path) -> None:
    assert skill_install.read_installed_files("nope", skills_root=tmp_path) is None


@pytest.mark.parametrize(
    ("local", "upstream", "installed_sha", "upstream_sha", "expected"),
    [
        ("base", "base", "s1", "s1", skill_install.DriftStatus.current),
        ("edit", "base", "s1", "s1", skill_install.DriftStatus.edited_locally),
        ("base", "new", "s1", "s2", skill_install.DriftStatus.upstream_changed),
        ("edit", "new", "s1", "s2", skill_install.DriftStatus.diverged),
        # A commit touched the folder: the sha, not the content, is the signal.
        ("base", "base", "s1", "s2", skill_install.DriftStatus.upstream_changed),
        # No sha on one side: content stands in for it.
        ("base", "new", None, "s2", skill_install.DriftStatus.upstream_changed),
        ("base", "base", "s1", None, skill_install.DriftStatus.current),
        ("edit", "new", None, None, skill_install.DriftStatus.diverged),
    ],
)
def test_classify_drift_with_a_baseline(
    local: str,
    upstream: str,
    installed_sha: str | None,
    upstream_sha: str | None,
    expected: skill_install.DriftStatus,
) -> None:
    status = skill_install.classify_drift(
        local_hash=local,
        upstream_hash=upstream,
        installed_hash="base",
        installed_sha=installed_sha,
        upstream_sha=upstream_sha,
    )
    assert status is expected


def test_classify_drift_without_a_baseline_only_knows_whether_the_copies_agree() -> None:
    """A row older than the columns: a difference is reported as diverged, the
    status that makes the update ask first, since nobody knows whose it is."""
    agree = skill_install.classify_drift(
        local_hash="x", upstream_hash="x", installed_hash=None, installed_sha=None, upstream_sha="s"
    )
    differ = skill_install.classify_drift(
        local_hash="x", upstream_hash="y", installed_hash=None, installed_sha=None, upstream_sha="s"
    )
    assert agree is skill_install.DriftStatus.current
    assert differ is skill_install.DriftStatus.diverged


def test_skill_md_diff_is_unified_and_empty_when_equal() -> None:
    assert skill_install.skill_md_diff(b"# A\n", b"# A\n") == ""

    diff = skill_install.skill_md_diff(b"# A\nold line\n", b"# A\nnew line\n")

    assert diff.startswith("--- SKILL.md (installed)\n+++ SKILL.md (upstream)\n")
    assert "-old line\n" in diff
    assert "+new line\n" in diff


def test_skill_md_diff_treats_a_missing_local_file_as_empty() -> None:
    diff = skill_install.skill_md_diff(None, b"# A\n")
    assert "+# A\n" in diff


def test_changed_paths_reports_added_removed_and_changed_but_not_skill_md() -> None:
    local = {"SKILL.md": b"old", "gone.md": b"x", "same.md": b"s", "edited.md": b"1"}
    upstream = {"SKILL.md": b"new", "new.md": b"y", "same.md": b"s", "edited.md": b"2"}

    changes = skill_install.changed_paths(local, upstream)

    assert [(c.path, c.change.value) for c in changes] == [
        ("edited.md", "changed"),
        ("gone.md", "removed"),
        ("new.md", "added"),
    ]
