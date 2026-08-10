"""Declarative workflow shapes: the presets, what they refuse, and what they claim."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import run as cli
from adw import workflows
from adw.config import load_config
from adw.pipeline import format_summary
from adw.workflows import PRESETS, WorkflowError
from conftest import FakeCLI, envelope, git
from test_pipeline import (
    BUILD_OK,
    DOCUMENT_OK,
    PLAN_OK,
    REVIEW_OK,
    events,
    run,
)

SCOUT_OK = {
    "session_id": "scout-session",
    "envelope": envelope(
        summary="One module, one entry point.",
        findings=["README.md is the only documentation in the repo"],
        changed_files=[],
    ),
}

SPECS = {
    "plan": PLAN_OK,
    "build": BUILD_OK,
    "review": REVIEW_OK,
    "document": DOCUMENT_OK,
    "scout": SCOUT_OK,
}


def script_for(stages: tuple[str, ...]) -> list[dict]:
    """One scripted CLI reply per agent stage — `checks` never launches one."""
    return [SPECS[name] for name in stages if name != workflows.CHECKS]


# --- every preset runs its own stage list -----------------------------------


@pytest.mark.parametrize("preset", sorted(PRESETS))
def test_every_preset_runs_exactly_the_stages_it_names(
    git_repo: Path, fake_cli: FakeCLI, preset: str
):
    stages = PRESETS[preset]
    result, telemetry = run(git_repo, fake_cli, script_for(stages), workflow=preset)

    assert result.accepted, result.reason
    assert [outcome.name for outcome in result.outcomes] == list(stages)
    assert [r["phase"] for r in events(telemetry) if r["event"] == "phase_end"] == list(stages)
    # …and no agent was launched for a stage this workflow does not have.
    assert len(fake_cli.calls) == len(script_for(stages))


def test_the_default_workflow_is_still_the_full_chain(git_repo: Path, fake_cli: FakeCLI):
    """An existing config that never heard of workflows keeps running the old chain."""
    result, _ = run(git_repo, fake_cli, script_for(workflows.FULL))
    assert result.workflow_name == "full"
    assert result.workflow == ("plan", "build", "checks", "review", "document")
    assert [outcome.name for outcome in result.outcomes] == list(workflows.FULL)


def test_a_custom_stage_list_in_the_config_is_honoured(git_repo: Path, fake_cli: FakeCLI):
    stages = ("plan", "build", "checks")
    result, _ = run(git_repo, fake_cli, script_for(stages), workflow=list(stages))

    assert result.accepted
    assert result.workflow_name == workflows.CUSTOM
    assert [outcome.name for outcome in result.outcomes] == list(stages)


# --- acceptance stays honest ------------------------------------------------


def test_a_workflow_with_no_checks_stage_cannot_claim_to_be_verified(
    git_repo: Path, fake_cli: FakeCLI
):
    """`build_review` has real checks configured — it just never runs them."""
    result, telemetry = run(
        git_repo, fake_cli, script_for(PRESETS["build_review"]), workflow="build_review"
    )
    summary = format_summary(result)

    assert result.accepted  # the shape the user asked for did run
    assert not result.verified  # …and it verified nothing, on the record
    assert "ACCEPTED (UNVERIFIED — no checks ran)" in summary
    assert "workflow: build_review — build → review" in summary
    stats = [e for e in events(telemetry) if e["event"] == "run_end"][0]["stats"]
    assert stats["verified"] is False and stats["reviewed"] is True
    assert stats["workflow"] == "build_review"


def test_a_workflow_with_no_review_stage_cannot_claim_review_approval(
    git_repo: Path, fake_cli: FakeCLI
):
    result, telemetry = run(
        git_repo, fake_cli, script_for(PRESETS["build_test"]), workflow="build_test"
    )
    summary = format_summary(result)

    assert result.accepted and result.verified
    assert not result.reviewed
    assert "UNREVIEWED — no review stage in this workflow" in summary
    assert "review approved" not in result.reason
    assert [e for e in events(telemetry) if e["event"] == "run_end"][0]["stats"]["reviewed"] is False


def test_a_workflow_that_does_neither_says_both(git_repo: Path, fake_cli: FakeCLI):
    result, _ = run(git_repo, fake_cli, script_for(PRESETS["plan_build"]), workflow="plan_build")
    verdict = format_summary(result).splitlines()[-1]
    assert verdict.startswith(
        "ACCEPTED (UNVERIFIED — no checks ran; UNREVIEWED — no review stage in this workflow)"
    )


def test_the_load_warns_before_the_run_that_a_workflow_runs_no_checks(tmp_path: Path):
    cfg = load_config(tmp_path, workflow="document")
    assert any("has no checks stage" in w and "NO checks" in w for w in cfg.warnings)
    assert not cfg.verified


def test_a_workflow_without_checks_is_not_refused_for_undetectable_checks(
    git_repo: Path, capsys, fake_cli: FakeCLI
):
    """No pyproject, no package.json — but this shape never promised to verify."""
    assert cli.main(["--repo", str(git_repo), "--workflow", "scout", "--dry-run", "x"]) == 0
    assert cli.main(["--repo", str(git_repo), "--dry-run", "x"]) == 2  # the full chain still is
    assert fake_cli.calls == []


# --- the loops keep working with fewer stages -------------------------------


def test_the_review_to_build_loop_still_runs_in_a_workflow_with_no_checks(
    git_repo: Path, fake_cli: FakeCLI
):
    rejected = {
        "session_id": "review-session",
        "envelope": envelope(summary="No", approved=False, blocking=["app.py: no docstring"]),
    }
    fix = {
        "session_id": "build-session",
        "envelope": envelope(summary="Add the docstring", changed_files=["app.py"]),
        "write_files": {"app.py": '"""Health."""\n'},
    }
    result, _ = run(
        git_repo, fake_cli, [BUILD_OK, rejected, fix, REVIEW_OK], workflow="build_review"
    )

    assert result.accepted
    assert [o.name for o in result.outcomes] == ["build", "review", "build", "review"]
    assert "app.py: no docstring" in fake_cli.calls[2]["prompt"]
    # The loop must not acquire a checks stage the workflow never asked for.
    assert "checks" not in {o.name for o in result.outcomes}


def test_the_checks_to_build_loop_still_runs_in_a_workflow_with_no_review(
    git_repo: Path, fake_cli: FakeCLI
):
    marker = (
        "python3 -c \"import pathlib,sys; sys.exit(0 if pathlib.Path('fixed.txt').exists() else 1)\""
    )
    fix = {
        "session_id": "build-session",
        "envelope": envelope(summary="Fix the suite", changed_files=["fixed.txt"]),
        "write_files": {"fixed.txt": "ok\n"},
    }
    result, _ = run(git_repo, fake_cli, [BUILD_OK, fix], workflow="build_test", checks=[marker])

    assert result.accepted and result.verified
    # The correction lands in the builder session, and is recorded as it happens —
    # the `checks` outcome is the one that closes over it.
    assert [o.name for o in result.outcomes] == ["build", "build", "checks"]
    assert result.outcomes[-1].corrections == 1


# --- a shape that cannot run is refused before any agent --------------------


@pytest.mark.parametrize(
    ("spec", "fragment"),
    [
        (["plan", "buld"], 'unknown stage "buld"'),
        (["review", "build"], '"review" comes before "build"'),
        (["build", "checks", "build"], 'stage "build" appears more than once'),
        (["review"], '"review" needs a "build" stage before it'),
        (["checks"], '"checks" needs a "build" stage before it'),
        (["build", "plan"], '"build" comes before "plan"'),
        ([], "the workflow is empty"),
        ("plan_biuld", 'unknown workflow "plan_biuld"'),
        (3, "must be a preset name"),
    ],
)
def test_an_impossible_workflow_is_refused_at_load(tmp_path: Path, spec: object, fragment: str):
    (tmp_path / "factory.config.json").write_text(
        json.dumps({"workflow": spec}), encoding="utf-8"
    )
    with pytest.raises(WorkflowError) as excinfo:
        load_config(tmp_path)
    assert fragment in str(excinfo.value)
    assert "factory.config.json" in str(excinfo.value)


def test_a_refused_workflow_exits_two_before_any_agent_runs(
    git_repo: Path, capsys, fake_cli: FakeCLI
):
    (git_repo / "factory.config.json").write_text(
        json.dumps({"workflow": ["review", "build"], "checks": ["true"]}), encoding="utf-8"
    )
    assert cli.main(["--repo", str(git_repo), "Add a health endpoint"]) == 2
    err = capsys.readouterr().err
    assert '"review" comes before "build"' in err
    assert fake_cli.calls == []


def test_an_unknown_preset_on_the_flag_lists_the_real_ones(git_repo: Path, capsys, fake_cli):
    assert cli.main(["--repo", str(git_repo), "--workflow", "nope", "--dry-run", "x"]) == 2
    err = capsys.readouterr().err
    assert "--workflow" in err
    for preset in PRESETS:
        assert preset in err
    assert fake_cli.calls == []


# --- the scout role ---------------------------------------------------------


def test_the_scout_preset_answers_the_question_and_writes_nothing(
    git_repo: Path, fake_cli: FakeCLI
):
    result, _ = run(git_repo, fake_cli, [SCOUT_OK], workflow="scout")

    assert result.accepted
    assert [o.name for o in result.outcomes] == ["scout"]
    assert result.outcomes[0].envelope.findings == [
        "README.md is the only documentation in the repo"
    ]
    prompt = fake_cli.calls[0]["prompt"]
    assert "WRITE BOUNDARY: NOTHING" in prompt
    assert '"findings"' in prompt
    assert '"changed_files": []' in prompt
    assert "independent axes" not in prompt  # it judges nothing, so it owes no verdict
    assert git(git_repo, "status", "--porcelain").strip() == ""


def test_a_scout_that_reports_no_findings_is_corrected(git_repo: Path, fake_cli: FakeCLI):
    silent = {"session_id": "scout-session", "envelope": {"status": "ok", "summary": "Looks fine."}}
    result, _ = run(git_repo, fake_cli, [silent, SCOUT_OK], workflow="scout")

    assert result.accepted
    assert result.outcomes[0].corrections == 1
    assert "findings" in fake_cli.calls[1]["prompt"]


def test_a_scout_that_writes_is_reverted_like_any_read_only_role(
    git_repo: Path, fake_cli: FakeCLI
):
    rogue = {
        "session_id": "scout-session",
        "envelope": envelope(summary="Fixed it myself", findings=["x"], changed_files=["app.py"]),
        "write_files": {"app.py": "PATCHED BY SCOUT\n"},
    }
    result, _ = run(git_repo, fake_cli, [rogue, SCOUT_OK], workflow="scout")

    assert result.accepted
    assert not (git_repo / "app.py").exists()
    assert result.outcomes[0].corrections == 1


def test_scout_findings_travel_to_the_next_stage(git_repo: Path, fake_cli: FakeCLI):
    result, _ = run(
        git_repo, fake_cli, [SCOUT_OK, BUILD_OK], workflow=["scout", "build"]
    )

    assert result.accepted
    build_prompt = fake_cli.calls[1]["prompt"]
    assert "README.md is the only documentation in the repo" in build_prompt
    assert "Envelope from the scout stage" in build_prompt


# --- the CLI flag -----------------------------------------------------------


def test_the_workflow_flag_beats_the_config_file(git_repo: Path, capsys):
    (git_repo / "factory.config.json").write_text(
        json.dumps({"workflow": "full", "checks": ["true"]}), encoding="utf-8"
    )
    assert cli.main(["--repo", str(git_repo), "--workflow", "build_test", "--dry-run", "x"]) == 0
    out = capsys.readouterr().out

    assert "workflow: build_test — build → checks" in out
    assert "\nplan " not in out  # the stage table holds only what runs
    assert "document" not in out


def test_the_dry_run_table_shows_only_the_stages_that_will_run(git_repo: Path, capsys):
    assert cli.main(["--repo", str(git_repo), "--workflow", "document", "--dry-run", "x"]) == 0
    out = capsys.readouterr().out

    assert "workflow: document — document" in out
    assert "this workflow has no checks stage" in out
    assert "UNVERIFIED" in out
    assert "review [" not in out and "plan [" not in out  # no role rows for absent stages
