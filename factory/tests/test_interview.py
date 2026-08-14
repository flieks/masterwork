"""--interview: pause after `plan` with questions, refuse or fold answers on --resume."""

from __future__ import annotations

import json
from pathlib import Path

import run as cli
from adw import interview, runs
from adw.config import load_config
from adw.pipeline import Pipeline
from adw.telemetry import Telemetry
from conftest import FakeCLI, envelope
from test_pipeline import BUILD_OK, DOCUMENT_OK, PASSING_CHECK, PLAN_OK, REQUEST, REVIEW_OK, subjects

RUN_ID = "testrun1"
BRANCH = f"factory/{RUN_ID}"
ASSUMPTIONS = ["Use SQLite for now", "Skip auth on this endpoint"]

PLAN_WITH_ASSUMPTIONS = dict(
    PLAN_OK,
    envelope=envelope(
        summary="Plan the health endpoint",
        artifacts=["plan.md"],
        changed_files=["plan.md"],
        notes_for_next_agent="Put the route in app.py",
        assumptions=ASSUMPTIONS,
    ),
)


def run_dir_of(repo: Path) -> Path:
    return repo.parent / "runs" / RUN_ID


def build_pipeline(
    repo: Path,
    *,
    checks: list[str] | None = None,
    interview_flag: bool = False,
    answers: list[interview.Answer] | None = None,
    resume: runs.ResumePlan | None = None,
) -> tuple[Pipeline, Telemetry]:
    data = {
        "telemetry_url": None,
        "checks": checks if checks is not None else [],
        "runs_dir": str(repo.parent / "runs"),
    }
    (repo / "factory.config.json").write_text(json.dumps(data), encoding="utf-8")
    run_id = resume.record.run_id if resume else RUN_ID
    cfg = load_config(repo, run_id=run_id)
    telemetry = Telemetry(
        run_id=cfg.run_id,
        repo=repo,
        run_dir=cfg.run_dir,
        url=cfg.telemetry_url,
        seq_start=runs.next_seq(cfg.run_dir) if resume else 1,
    )
    pipeline = Pipeline(cfg, REQUEST, telemetry, resume=resume, interview=interview_flag, answers=answers)
    return pipeline, telemetry


def run(repo: Path, fake_cli: FakeCLI, script: list[dict], **kwargs):
    fake_cli.script(script)
    pipeline, telemetry = build_pipeline(repo, **kwargs)
    result = pipeline.run()
    telemetry.close()
    return result, telemetry


def cli_args(repo: Path, root: Path, *args: str) -> list[str]:
    return ["--repo", str(repo), "--runs-dir", str(root), *args]


# --- pausing -----------------------------------------------------------------


def test_an_interview_run_pauses_after_plan_with_one_question_per_assumption(
    git_repo: Path, fake_cli: FakeCLI
):
    result, _ = run(git_repo, fake_cli, [PLAN_WITH_ASSUMPTIONS], interview_flag=True)

    assert result.paused
    assert result.exit_code == 0
    assert not result.accepted
    assert [q.question for q in result.questions] == ASSUMPTIONS
    assert [q.id for q in result.questions] == ["q1", "q2"]
    assert len(fake_cli.calls) == 1  # only the planner ran — build was never called

    questions_path = run_dir_of(git_repo) / interview.QUESTIONS_FILENAME
    saved = json.loads(questions_path.read_text())
    assert saved["run_id"] == RUN_ID
    assert saved["stage"] == "plan"
    assert [q["question"] for q in saved["questions"]] == ASSUMPTIONS

    record = runs.read(run_dir_of(git_repo))
    assert record is not None
    assert record.state == runs.WAITING_INPUT
    assert record.pid is None
    assert record.interview is True
    assert subjects(git_repo)[0] == "plan: Plan the health endpoint"  # plan did commit


def test_a_plan_with_no_assumptions_does_not_pause(git_repo: Path, fake_cli: FakeCLI):
    result, _ = run(
        git_repo,
        fake_cli,
        [PLAN_OK, BUILD_OK, REVIEW_OK, DOCUMENT_OK],
        interview_flag=True,
    )

    assert not result.paused
    assert result.accepted, result.reason
    assert not (run_dir_of(git_repo) / interview.QUESTIONS_FILENAME).exists()


def test_a_non_interview_run_never_writes_interview_files(git_repo: Path, fake_cli: FakeCLI):
    """Regression: no --interview means the pipeline behaves exactly as before."""
    result, _ = run(git_repo, fake_cli, [PLAN_WITH_ASSUMPTIONS, BUILD_OK, REVIEW_OK, DOCUMENT_OK])

    assert result.accepted, result.reason
    assert not result.paused
    assert not (run_dir_of(git_repo) / interview.QUESTIONS_FILENAME).exists()


# --- resuming ------------------------------------------------------------------


def test_resume_without_answers_refuses_and_calls_no_agent(git_repo: Path, fake_cli: FakeCLI):
    run(git_repo, fake_cli, [PLAN_WITH_ASSUMPTIONS], interview_flag=True)
    calls_before = len(fake_cli.calls)
    root = git_repo.parent / "runs"

    exit_code = cli.main(cli_args(git_repo, root, "--resume", RUN_ID))

    assert exit_code == 2
    assert len(fake_cli.calls) == calls_before  # not one more token spent


def test_resume_with_answers_folds_them_into_the_build_prompt(git_repo: Path, fake_cli: FakeCLI):
    run(git_repo, fake_cli, [PLAN_WITH_ASSUMPTIONS], interview_flag=True)
    run_dir = run_dir_of(git_repo)
    answers_path = run_dir / interview.ANSWERS_FILENAME
    answers_path.write_text(
        json.dumps(
            {
                "answered_at": "2026-08-14T10:05:00+00:00",
                "answers": [
                    {"id": "q1", "question": ASSUMPTIONS[0], "answer": "Use Postgres instead"},
                    {"id": "q2", "question": ASSUMPTIONS[1], "answer": "Require a bearer token"},
                ],
            }
        ),
        encoding="utf-8",
    )

    plan = runs.plan_resume(git_repo, run_dir, RUN_ID)
    answers = interview.read_answers(run_dir)
    fake_cli.script([BUILD_OK, REVIEW_OK, DOCUMENT_OK])
    pipeline, telemetry = build_pipeline(git_repo, checks=[PASSING_CHECK], resume=plan, answers=answers)
    result = pipeline.run()
    telemetry.close()

    assert result.accepted, result.reason
    build_prompt = fake_cli.calls[0]["prompt"]
    assert "Use Postgres instead" in build_prompt
    assert "Require a bearer token" in build_prompt
    assert ASSUMPTIONS[0] in build_prompt
    assert ASSUMPTIONS[1] in build_prompt

    saved_prompt = (run_dir / "prompts" / "build" / "1.user.md").read_text()
    assert "Use Postgres instead" in saved_prompt
    assert "Require a bearer token" in saved_prompt


def test_answers_for_the_wrong_or_missing_questions_are_refused(git_repo: Path, fake_cli: FakeCLI):
    run(git_repo, fake_cli, [PLAN_WITH_ASSUMPTIONS], interview_flag=True)
    run_dir = run_dir_of(git_repo)
    (run_dir / interview.ANSWERS_FILENAME).write_text(
        json.dumps({"answered_at": "x", "answers": [{"id": "q1", "answer": "only one of two"}]}),
        encoding="utf-8",
    )

    exit_code = cli.main(cli_args(git_repo, git_repo.parent / "runs", "--resume", RUN_ID))
    assert exit_code == 2


def test_malformed_answers_json_is_refused(git_repo: Path, fake_cli: FakeCLI):
    run(git_repo, fake_cli, [PLAN_WITH_ASSUMPTIONS], interview_flag=True)
    (run_dir_of(git_repo) / interview.ANSWERS_FILENAME).write_text("not json", encoding="utf-8")

    exit_code = cli.main(cli_args(git_repo, git_repo.parent / "runs", "--resume", RUN_ID))
    assert exit_code == 2


# --- --run-id ------------------------------------------------------------------


def test_run_id_places_the_run_where_asked(git_repo: Path, fake_cli: FakeCLI):
    fake_cli.script([PLAN_OK, BUILD_OK])
    root = git_repo.parent / "runs"

    exit_code = cli.main(
        cli_args(git_repo, root, "--workflow", "plan_build", "--run-id", "customid123", REQUEST)
    )

    assert exit_code == 0
    record = runs.read(root / "customid123")
    assert record is not None
    assert record.run_id == "customid123"


def test_run_id_refuses_a_reused_id(git_repo: Path, fake_cli: FakeCLI):
    root = git_repo.parent / "runs"
    runs.write(root / "taken", runs.RunRecord(run_id="taken", repo=str(git_repo)))

    exit_code = cli.main(cli_args(git_repo, root, "--run-id", "taken", REQUEST))
    assert exit_code == 2


def test_run_id_refuses_traversal_and_bad_characters(git_repo: Path, fake_cli: FakeCLI):
    root = git_repo.parent / "runs"
    for bad in ("../escape", "has/slash", ".."):
        assert cli.main(cli_args(git_repo, root, "--run-id", bad, REQUEST)) == 2


def test_run_id_together_with_resume_is_refused(git_repo: Path, capsys):
    root = git_repo.parent / "runs"
    exit_code = cli.main(cli_args(git_repo, root, "--resume", "whatever", "--run-id", "x"))
    assert exit_code == 2
    assert "--run-id" in capsys.readouterr().err


def test_interview_without_a_plan_stage_is_refused(git_repo: Path, capsys):
    # "document" has no checks stage either, so this fails on --interview and
    # not on the unrelated "nothing to verify" refusal.
    root = git_repo.parent / "runs"
    exit_code = cli.main(
        cli_args(git_repo, root, "--interview", "--workflow", "document", REQUEST)
    )
    assert exit_code == 2
    assert "--interview" in capsys.readouterr().err
