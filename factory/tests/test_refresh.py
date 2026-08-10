"""--refresh-roles: which library files are still ours, and which are the user's."""

from __future__ import annotations

from pathlib import Path

import pytest
import run as cli
from adw import roles
from adw.roles import (
    CURRENT,
    EDITED,
    MISSING,
    PRISTINE,
    SYSTEM_FILE,
    USER_FILE,
    RoleStore,
)

MY_TEXT = "MY OWN REVIEWER\n"
OLD_TEXT = "AN OLDER BUILT-IN\n"


def states(store: RoleStore) -> dict[str, str]:
    return {entry.key: entry.state for entry in store.audit()}


def seeded_library(repo: Path) -> RoleStore:
    store = RoleStore(repo)
    store.seed()
    return store


def pretend_the_builtin_improved(store: RoleStore, role: str, filename: str) -> Path:
    """Seed one file from an OLDER built-in, then let the built-in improve.

    The library copy is then exactly what we wrote and no longer what we ship —
    pristine, and the only case an update may happen without asking.
    """
    path = store.global_dir / role / filename
    path.unlink()
    real = roles.builtin_text
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            roles,
            "builtin_text",
            lambda r, f: OLD_TEXT if (r, f) == (role, filename) else real(r, f),
        )
        store.seed()
    return path


# --- the audit --------------------------------------------------------------


def test_a_freshly_seeded_library_is_entirely_current(git_repo: Path):
    store = seeded_library(git_repo)
    assert set(states(store).values()) == {CURRENT}


def test_pristine_edited_and_missing_are_told_apart(git_repo: Path):
    store = seeded_library(git_repo)
    pretend_the_builtin_improved(store, "plan", SYSTEM_FILE)
    (store.global_dir / "review" / SYSTEM_FILE).write_text(MY_TEXT, encoding="utf-8")
    (store.global_dir / "document" / USER_FILE).unlink()

    seen = states(store)
    assert seen["plan/system.md"] == PRISTINE  # ours, and we have improved it since
    assert seen["review/system.md"] == EDITED  # theirs
    assert seen["document/user.md"] == MISSING
    assert seen["build/system.md"] == CURRENT


def test_without_a_seed_record_every_difference_counts_as_an_edit(git_repo: Path):
    """The safe direction for a library seeded before the record existed: we cannot
    prove the text is ours, so we must assume it is theirs."""
    store = seeded_library(git_repo)
    (store.global_dir / "plan" / SYSTEM_FILE).write_text(OLD_TEXT, encoding="utf-8")
    store.seed_record_path.unlink()

    assert states(store)["plan/system.md"] == EDITED


# --- refreshing -------------------------------------------------------------


def test_refresh_updates_the_pristine_ones_and_never_the_edited_one(git_repo: Path):
    store = seeded_library(git_repo)
    stale = pretend_the_builtin_improved(store, "plan", SYSTEM_FILE)
    mine = store.global_dir / "review" / SYSTEM_FILE
    mine.write_text(MY_TEXT, encoding="utf-8")
    gap = store.global_dir / "document" / USER_FILE
    gap.unlink()

    updated, skipped, backed_up = store.refresh()

    assert {e.key for e in updated} == {"plan/system.md", "document/user.md"}
    assert [e.key for e in skipped] == ["review/system.md"]
    assert backed_up == []
    assert stale.read_text() == roles.builtin_text("plan", SYSTEM_FILE)
    assert gap.read_text() == roles.builtin_text("document", USER_FILE)
    assert mine.read_text() == MY_TEXT  # untouched, and no .bak needed
    assert not mine.with_name(mine.name + ".bak").exists()


def test_a_refreshed_file_is_pristine_again_not_edited(git_repo: Path):
    """The record has to follow the write, or the next refresh sees our own text
    as a user edit and stops updating that file forever."""
    store = seeded_library(git_repo)
    pretend_the_builtin_improved(store, "build", USER_FILE)
    store.refresh()
    assert states(store)["build/user.md"] == CURRENT


def test_overwriting_an_edit_is_explicit_and_keeps_a_copy(git_repo: Path):
    store = seeded_library(git_repo)
    mine = store.global_dir / "review" / SYSTEM_FILE
    mine.write_text(MY_TEXT, encoding="utf-8")

    updated, skipped, backed_up = store.refresh(overwrite_edited=True)

    assert [e.key for e in backed_up] == ["review/system.md"]
    assert [e.key for e in updated] == ["review/system.md"]
    assert skipped == []
    assert mine.read_text() == roles.builtin_text("review", SYSTEM_FILE)
    assert mine.with_name(mine.name + ".bak").read_text() == MY_TEXT


def test_the_diff_shows_the_builtin_against_your_copy(git_repo: Path):
    store = seeded_library(git_repo)
    (store.global_dir / "review" / SYSTEM_FILE).write_text(MY_TEXT, encoding="utf-8")
    entry = next(e for e in store.audit() if e.key == "review/system.md")

    assert f"+{MY_TEXT}" in entry.diff
    assert "-You are the REVIEW stage" in entry.diff


# --- the CLI ----------------------------------------------------------------


def test_the_report_writes_nothing_and_names_the_next_command(
    git_repo: Path, capsys, isolated_roles: Path
):
    store = seeded_library(git_repo)
    stale = pretend_the_builtin_improved(store, "plan", SYSTEM_FILE)
    mine = store.global_dir / "review" / SYSTEM_FILE
    mine.write_text(MY_TEXT, encoding="utf-8")

    assert cli.main(["--repo", str(git_repo), "--refresh-roles"]) == 0
    out = capsys.readouterr().out

    assert f"role library: {isolated_roles}" in out
    assert "pristine  plan/system.md" in out
    assert "edited    review/system.md" in out
    assert "1 file(s) can be updated without touching your work" in out
    assert "--refresh-roles --apply" in out
    assert "--overwrite-edited" in out
    assert f"+{MY_TEXT}" in out  # the diff, so the choice is an informed one
    assert stale.read_text() == OLD_TEXT  # …and nothing was written
    assert mine.read_text() == MY_TEXT


def test_apply_updates_only_the_un_edited_files(git_repo: Path, capsys):
    store = seeded_library(git_repo)
    stale = pretend_the_builtin_improved(store, "plan", SYSTEM_FILE)
    mine = store.global_dir / "review" / SYSTEM_FILE
    mine.write_text(MY_TEXT, encoding="utf-8")

    assert cli.main(["--repo", str(git_repo), "--refresh-roles", "--apply"]) == 0
    out = capsys.readouterr().out

    assert "updated 1 file(s)" in out
    assert "1 edited file(s) were NOT touched" in out
    assert stale.read_text() == roles.builtin_text("plan", SYSTEM_FILE)
    assert mine.read_text() == MY_TEXT


def test_overwrite_edited_replaces_it_and_says_where_the_copy_went(git_repo: Path, capsys):
    store = seeded_library(git_repo)
    mine = store.global_dir / "review" / SYSTEM_FILE
    mine.write_text(MY_TEXT, encoding="utf-8")

    code = cli.main(
        ["--repo", str(git_repo), "--refresh-roles", "--apply", "--overwrite-edited"]
    )
    assert code == 0
    out = capsys.readouterr().out

    assert "kept your previous review/system.md as system.md.bak" in out
    assert mine.read_text() == roles.builtin_text("review", SYSTEM_FILE)
    assert mine.with_name("system.md.bak").read_text() == MY_TEXT


def test_a_write_flag_without_refresh_roles_is_refused_not_ignored(git_repo: Path, capsys):
    assert cli.main(["--repo", str(git_repo), "--apply", "--dry-run", "x"]) == 2
    assert "--apply and --overwrite-edited only apply to --refresh-roles" in capsys.readouterr().err


def test_seeding_records_what_it_wrote_so_the_next_refresh_can_tell(git_repo: Path):
    store = seeded_library(git_repo)
    record = store.seed_record_path
    assert record.is_file()
    assert states(store)["plan/system.md"] == CURRENT
    # The record is data about the library, never a role directory.
    assert record.name.startswith(".")
