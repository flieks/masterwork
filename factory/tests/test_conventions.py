"""The shared conventions layer: one file, every role, or no trace at all."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import run as cli
from adw import prompts, roles
from adw.config import load_config
from adw.roles import CONVENTIONS_FILE, PROJECT_SUBDIR, ROLES, RoleStore
from conftest import FakeCLI
from test_pipeline import BUILD_OK, DOCUMENT_OK, PLAN_OK, REVIEW_OK, run

HOUSE = "- Every public function carries a one-line docstring.\n"
PROJECT = "- This repo indents with tabs.\n"

REQUEST = "Add a /health endpoint"


def write_conventions(*, library: Path | None = None, repo: Path | None = None) -> None:
    for base, text in ((library, HOUSE), (repo, PROJECT)):
        if base is None:
            continue
        base.mkdir(parents=True, exist_ok=True)
        (base / CONVENTIONS_FILE).write_text(text, encoding="utf-8")


def config_for(repo: Path, role: str):
    """Every role needs a workflow that contains it before it has a Stage."""
    return load_config(repo, workflow="scout" if role == "scout" else None)


def compiled(repo: Path, role: str) -> str:
    cfg = config_for(repo, role)
    return prompts.compile_prompt(
        role=cfg.roles[role],
        stage=cfg.stages[role],
        request=REQUEST,
        repo=repo,
        conventions=cfg.conventions,
    ).combined


# --- absence leaves no trace ------------------------------------------------


@pytest.mark.parametrize("role", ROLES)
def test_no_conventions_file_leaves_the_prompt_byte_identical(tmp_path: Path, role: str):
    """An optional block that renders empty must disappear exactly like
    `## Artifacts …` does, leaving no heading and no hole behind."""
    repo = tmp_path / "repo"
    repo.mkdir()
    cfg = config_for(repo, role)
    stage = cfg.stages[role]
    values = prompts.template_values(stage=stage, request=REQUEST, repo=repo)

    with_variable = prompts.compile_prompt(
        role=cfg.roles[role], stage=stage, request=REQUEST, repo=repo, conventions=""
    )
    # The same template with the variable deleted outright, not merely emptied.
    never_had_it = prompts.render(
        cfg.roles[role].user.replace("{{conventions}}\n\n", ""), values, source="test"
    )
    assert with_variable.user == never_had_it
    assert "Shared conventions" not in with_variable.combined
    assert "\n\n\n" not in with_variable.combined  # no hole where the block was


def test_the_golden_prompts_are_unchanged_by_the_conventions_variable(tmp_path: Path):
    """Belt and braces: the pinned pre-refactor prompts, recompiled today."""
    golden = json.loads(
        (Path(__file__).resolve().parent / "golden" / "legacy_prompts.json").read_text()
    )
    repo = tmp_path / "golden-repo"
    (repo / "docs" / "specs").mkdir(parents=True)
    cfg = load_config(repo)
    compiled_plan = prompts.compile_prompt(
        role=cfg.roles["plan"],
        stage=cfg.stages["plan"],
        request="Add a /health endpoint that returns {'status': 'ok'}",
        repo=repo,
        conventions=cfg.conventions,
    )
    assert cfg.conventions == ""
    assert compiled_plan.combined == golden["plan"]


# --- presence reaches every role -------------------------------------------


@pytest.mark.parametrize("role", ROLES)
def test_the_library_conventions_reach_every_role(tmp_path: Path, isolated_roles: Path, role: str):
    repo = tmp_path / "repo"
    repo.mkdir()
    write_conventions(library=isolated_roles)
    assert HOUSE.strip() in compiled(repo, role)


@pytest.mark.parametrize("role", ROLES)
def test_the_repo_conventions_reach_every_role(tmp_path: Path, role: str):
    repo = tmp_path / "repo"
    write_conventions(repo=repo / PROJECT_SUBDIR)
    assert PROJECT.strip() in compiled(repo, role)


def test_the_repo_file_appends_to_the_library_one_and_wins_on_conflict(
    tmp_path: Path, isolated_roles: Path
):
    """Appending, not replacing: a project adding one rule must not silently drop
    every rule the user wrote once for all their repos."""
    repo = tmp_path / "repo"
    write_conventions(library=isolated_roles, repo=repo / PROJECT_SUBDIR)
    text = compiled(repo, "build")

    assert HOUSE.strip() in text and PROJECT.strip() in text
    assert text.index(HOUSE.strip()) < text.index(PROJECT.strip())  # the repo reads last
    assert "the repository's own file wins" in text
    assert str(isolated_roles / CONVENTIONS_FILE) in text  # both sources are named
    assert str(repo / PROJECT_SUBDIR / CONVENTIONS_FILE) in text


def test_one_source_alone_does_not_talk_about_two(tmp_path: Path, isolated_roles: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    write_conventions(library=isolated_roles)
    assert "the repository's own file wins" not in compiled(repo, "review")


def test_an_empty_conventions_file_is_the_same_as_none(tmp_path: Path, isolated_roles: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    isolated_roles.mkdir(parents=True)
    (isolated_roles / CONVENTIONS_FILE).write_text("   \n\n", encoding="utf-8")
    assert load_config(repo).conventions == ""


# --- the end-to-end path ----------------------------------------------------


def test_every_stage_of_a_real_run_is_sent_the_conventions(
    git_repo: Path, fake_cli: FakeCLI, isolated_roles: Path
):
    write_conventions(library=isolated_roles, repo=git_repo / PROJECT_SUBDIR)
    run(git_repo, fake_cli, [PLAN_OK, BUILD_OK, REVIEW_OK, DOCUMENT_OK])

    assert len(fake_cli.calls) == 4
    for call in fake_cli.calls:
        assert HOUSE.strip() in call["prompt"]
        assert PROJECT.strip() in call["prompt"]


def test_a_library_that_predates_the_variable_warns_instead_of_ignoring_it(
    git_repo: Path, isolated_roles: Path
):
    """The failure this must never have: the user writes house rules and the run
    quietly does not show them to anyone."""
    store = RoleStore(git_repo)
    store.seed()
    old = isolated_roles / "build" / "user.md"
    old.write_text("Implement this: {{request}}\n\n{{envelope_contract}}\n", encoding="utf-8")
    write_conventions(library=isolated_roles)

    warnings = [w for w in load_config(git_repo).warnings if w.startswith("conventions:")]
    assert len(warnings) == 1
    assert "build" in warnings[0]
    assert str(isolated_roles / CONVENTIONS_FILE) in warnings[0]
    assert "--refresh-roles" in warnings[0]


def test_the_dry_run_names_the_conventions_files_in_play(
    git_repo: Path, capsys, isolated_roles: Path
):
    (git_repo / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert cli.main(["--repo", str(git_repo), "--dry-run", "x"]) == 0
    assert "conventions (shown to every role): (none)" in capsys.readouterr().out

    write_conventions(library=isolated_roles)
    assert cli.main(["--repo", str(git_repo), "--dry-run", "x"]) == 0
    assert f"conventions (shown to every role): {isolated_roles / CONVENTIONS_FILE}" in (
        capsys.readouterr().out
    )


def test_the_conventions_variable_is_documented_like_every_other(tmp_path: Path):
    assert "conventions" in roles.VARIABLES
    for role in ROLES:
        assert "conventions" in roles.placeholder_names(roles.builtin_text(role, "user.md"))
