"""Resume and the process registry: what a run leaves behind, and who can act on it."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import run as cli
from adw import agent, gitwork, runs
from adw.agent import RUN_ID_ENV, STAGE_ENV, AgentSession
from adw.config import load_config
from adw.pipeline import REUSED, Pipeline, RunResult, format_summary
from adw.telemetry import Telemetry
from conftest import FakeCLI, envelope, git
from test_pipeline import (
    BUILD_OK,
    COLLECTOR,
    DOCUMENT_OK,
    PLAN_OK,
    REQUEST,
    REVIEW_OK,
    events,
    run,
    subjects,
)

RUN_ID = "testrun1"
BRANCH = f"factory/{RUN_ID}"


def priced(spec: dict, cost: float) -> dict:
    return dict(spec, cost_usd=cost)


# Plan is cheap and commits; build breaks a $0.25 cap; the rest is what a resume runs.
STOPPING_SCRIPT = [
    priced(PLAN_OK, 0.01),
    priced(BUILD_OK, 0.40),
    BUILD_OK,
    REVIEW_OK,
    DOCUMENT_OK,
]


def run_dir_of(repo: Path) -> Path:
    return repo.parent / "runs" / RUN_ID


def stop_on_budget(repo: Path, fake_cli: FakeCLI, **kwargs) -> RunResult:
    """A run that a cost cap stops inside `build`, with `plan` already committed."""
    result, _ = run(repo, fake_cli, STOPPING_SCRIPT, max_cost_usd=0.25, **kwargs)
    assert result.budget_stop, result.reason
    return result


def resume_run(repo: Path, cap: float = 5.0) -> tuple[RunResult, Telemetry, runs.ResumePlan]:
    """Exactly what run.py does: plan first, then a pipeline on the same run id, with
    the raised cap arriving as a flag over the config the first attempt left behind."""
    plan = runs.plan_resume(repo, run_dir_of(repo), RUN_ID)
    cfg = load_config(repo, run_id=RUN_ID, max_cost_usd=cap)
    telemetry = Telemetry(
        run_id=cfg.run_id,
        repo=repo,
        run_dir=cfg.run_dir,
        url=cfg.telemetry_url,
        seq_start=runs.next_seq(cfg.run_dir),
    )
    result = Pipeline(cfg, plan.record.request, telemetry, resume=plan).run()
    telemetry.close()
    return result, telemetry, plan


def record_of(repo: Path) -> runs.RunRecord:
    record = runs.read(run_dir_of(repo))
    assert record is not None
    return record


# --- the record a live run leaves ------------------------------------------


def test_the_run_is_on_the_registry_before_any_agent_could_have_run(
    git_repo: Path, fake_cli: FakeCLI
):
    """A hung run emits nothing, so the pid cannot wait until things are going well.
    An existing branch name aborts before the first turn — the record is there anyway."""
    git(git_repo, "branch", BRANCH)

    result, _ = run(git_repo, fake_cli, [PLAN_OK, BUILD_OK, REVIEW_OK, DOCUMENT_OK])

    assert not result.accepted
    assert fake_cli.calls == []  # not one token was spent
    record = record_of(git_repo)
    assert record.run_id == RUN_ID
    assert record.request == REQUEST
    assert record.cmdline  # whatever launched this process, verbatim
    assert record.pid is None and record.state == runs.FINISHED  # …and already cleaned


def test_the_record_names_the_run_its_workflow_and_its_branch(git_repo: Path, fake_cli: FakeCLI):
    result, _ = run(git_repo, fake_cli, [PLAN_OK, BUILD_OK, REVIEW_OK, DOCUMENT_OK])
    assert result.accepted, result.reason

    record = record_of(git_repo)
    assert record.workflow == ["plan", "build", "checks", "review", "document"]
    assert record.workflow_name == "full"
    assert record.branch == BRANCH
    assert record.branch_origin == "main"
    assert record.attempt == 1


def test_the_pid_record_is_cleaned_when_the_run_ends_normally(git_repo: Path, fake_cli: FakeCLI):
    result, _ = run(git_repo, fake_cli, [PLAN_OK, BUILD_OK, REVIEW_OK, DOCUMENT_OK])
    assert result.accepted, result.reason

    record = record_of(git_repo)
    assert record.pid is None
    assert record.state == runs.FINISHED
    assert record.accepted is True
    assert record.ended
    # And --list-runs therefore shows no ghost holding a pid.
    listed = cli.format_runs(git_repo.parent / "runs", runs.list_runs(git_repo.parent / "runs"))
    assert "finished" in listed
    assert "running" not in listed


def test_a_budget_stop_is_recorded_as_stopped_not_finished(git_repo: Path, fake_cli: FakeCLI):
    stop_on_budget(git_repo, fake_cli)

    record = record_of(git_repo)
    assert record.state == runs.STOPPED
    assert record.pid is None
    assert record.accepted is False
    assert "cost cap reached" in record.reason


def test_only_a_stage_that_committed_leaves_a_stage_record(git_repo: Path, fake_cli: FakeCLI):
    """The interrupted stage wrote app.py, but nothing gated or committed it."""
    stop_on_budget(git_repo, fake_cli)

    saved = runs.stage_records(run_dir_of(git_repo))
    assert list(saved) == ["plan"]
    assert saved["plan"].envelope["summary"] == "Plan the health endpoint"
    assert saved["plan"].commit == git(git_repo, "rev-parse", BRANCH).strip()
    assert (git_repo / "app.py").is_file()  # the build's work is there, just ungated


# --- resume: what it skips, and on what evidence ----------------------------


def test_resume_skips_the_committed_stage_and_re_runs_the_interrupted_one(
    git_repo: Path, fake_cli: FakeCLI
):
    stop_on_budget(git_repo, fake_cli)
    calls_before = len(fake_cli.calls)

    result, _, plan = resume_run(git_repo)

    assert result.accepted, result.reason
    assert result.resumed and result.attempt == 2
    assert plan.first_stage == "build"
    assert result.reused == ["plan"]
    assert [o.status for o in result.outcomes] == [REUSED, "passed", "passed", "passed", "passed"]
    # The planner was never called again; every later call is the resumed work.
    assert [call["n"] for call in fake_cli.calls[calls_before:]] == [2, 3, 4]
    assert "Plan the health endpoint" in fake_cli.calls[2]["prompt"]  # the reused envelope


def test_a_resumed_run_labels_its_children_with_the_run_it_is_resuming(
    git_repo: Path, fake_cli: FakeCLI
):
    """Attempt two is the same run, so its children must not claim a new id."""
    stop_on_budget(git_repo, fake_cli)
    calls_before = len(fake_cli.calls)

    result, _, _ = resume_run(git_repo)
    assert result.accepted, result.reason

    resumed_calls = fake_cli.calls[calls_before:]
    assert resumed_calls  # the resumed attempt really did launch children
    assert {call["env"][RUN_ID_ENV] for call in fake_cli.calls} == {RUN_ID}
    assert [call["env"][STAGE_ENV] for call in resumed_calls] == ["build", "review", "document"]


def test_resume_lands_on_the_original_branch_with_one_continuous_history(
    git_repo: Path, fake_cli: FakeCLI
):
    stop_on_budget(git_repo, fake_cli)
    plan_sha = git(git_repo, "rev-parse", BRANCH).strip()

    result, _, _ = resume_run(git_repo)

    assert result.accepted, result.reason
    assert gitwork.current_branch(git_repo) == BRANCH
    assert result.branch is not None and result.branch.resumed
    assert result.branch.name == BRANCH
    # One branch, one history: the plan commit the first attempt made is still the
    # parent of everything the second attempt added.
    assert subjects(git_repo) == [
        "document: Document the endpoint",
        "build: Add the health route",
        "plan: Plan the health endpoint",
        "initial",
    ]
    assert gitwork.is_ancestor(git_repo, plan_sha, BRANCH)
    assert git(git_repo, "branch", "--format=%(refname:short)").split() == [BRANCH, "main"]
    assert git(git_repo, "log", "--pretty=%s", "main").strip() == "initial"


def test_the_interrupted_stages_leftovers_belong_to_the_stage_that_re_runs(
    git_repo: Path, fake_cli: FakeCLI
):
    """app.py was written but never committed; the re-run must not be told it did
    not change the file it just wrote."""
    stop_on_budget(git_repo, fake_cli)
    assert "app.py" in git(git_repo, "status", "--porcelain")

    result, _, plan = resume_run(git_repo)

    assert "app.py" in plan.dirty
    assert result.accepted, result.reason
    assert result.corrections == 0
    assert git(git_repo, "status", "--porcelain").strip() == ""


def test_a_budget_stopped_run_completes_when_the_cap_is_raised(
    git_repo: Path, fake_cli: FakeCLI
):
    stopped = stop_on_budget(git_repo, fake_cli)
    assert stopped.exit_code == 1

    result, _, _ = resume_run(git_repo)

    assert result.accepted and result.exit_code == 0
    assert "STOPPED ON BUDGET" not in format_summary(result)
    summary = format_summary(result)
    assert f"RESUMED — attempt 2 of run {RUN_ID}" in summary
    assert "reused (already committed): plan " in summary
    assert "ran this attempt: build, checks, review, document" in summary
    assert "1 reused (plan)" in result.reason


def test_the_evidence_for_every_stage_is_stated_before_anything_is_spent(
    git_repo: Path, fake_cli: FakeCLI
):
    stop_on_budget(git_repo, fake_cli)
    plan = runs.plan_resume(git_repo, run_dir_of(git_repo), RUN_ID)

    assert plan.evidence[0].startswith("plan: done — ")
    assert f"on {BRANCH}, logged and gated" in plan.evidence[0]
    assert plan.evidence[1] == "build: re-runs — no committed stage record"


def test_a_stage_whose_commit_is_not_on_the_branch_is_not_trusted(
    git_repo: Path, fake_cli: FakeCLI
):
    """The stage record is the optimistic source; git is the one that decides."""
    stop_on_budget(git_repo, fake_cli)
    forged = run_dir_of(git_repo) / runs.STAGES_DIRNAME / "plan.json"
    body = json.loads(forged.read_text())
    body["commit"] = "0" * 40
    forged.write_text(json.dumps(body))

    plan = runs.plan_resume(git_repo, run_dir_of(git_repo), RUN_ID)
    assert plan.done == {}
    assert "is not in the run's telemetry" in plan.evidence[0]


def test_the_resumed_run_stays_in_the_same_telemetry_session(git_repo: Path, fake_cli: FakeCLI):
    stop_on_budget(git_repo, fake_cli)
    first = runs.telemetry_events(run_dir_of(git_repo))

    result, telemetry, _ = resume_run(git_repo)

    assert result.accepted, result.reason
    assert telemetry.session_id == f"factory-{RUN_ID}"
    # One file, appended to: the first attempt's lines are all still there, in order.
    both = events(telemetry)
    assert [e["ts"] for e in both][: len(first)] == [e["ts"] for e in first]
    assert {e["run"] for e in both} == {RUN_ID}
    resumed = [
        e for e in both if (e.get("payload") or {}).get("resume")
    ]
    assert len(resumed) == 1
    payload = resumed[0]["payload"]["resume"]
    assert payload["attempt"] == 2
    assert payload["continues_at"] == "build"
    assert list(payload["reused"]) == ["plan"]


def test_the_second_attempts_phases_do_not_re_use_the_firsts_sequence_numbers(
    git_repo: Path, fake_cli: FakeCLI, post_spy
):
    """Same session id means the collector merges both attempts — so a phase seq the
    first attempt already used must never come round again."""
    stop_on_budget(git_repo, fake_cli, telemetry_url=COLLECTOR)
    first = [b["phase"]["seq"] for b in post_spy.bodies if "phase" in b]

    result, _, _ = resume_run(git_repo)
    assert result.accepted, result.reason

    everything = [b["phase"]["seq"] for b in post_spy.bodies if "phase" in b]
    second = everything[len(first) :]
    assert first and second
    assert min(second) > max(first)


# --- resume: the refusals ----------------------------------------------------


def test_an_unknown_run_id_is_refused(git_repo: Path):
    with pytest.raises(runs.RunError) as exc:
        runs.plan_resume(git_repo, git_repo.parent / "runs" / "nope", "nope")
    assert "unknown run 'nope'" in str(exc.value)
    assert "--list-runs" in str(exc.value)


def test_a_completed_run_is_refused(git_repo: Path, fake_cli: FakeCLI):
    result, _ = run(git_repo, fake_cli, [PLAN_OK, BUILD_OK, REVIEW_OK, DOCUMENT_OK])
    assert result.accepted

    with pytest.raises(runs.RunError) as exc:
        runs.plan_resume(git_repo, run_dir_of(git_repo), RUN_ID)
    assert "already completed" in str(exc.value)


def test_a_run_whose_branch_is_gone_is_refused(git_repo: Path, fake_cli: FakeCLI):
    stop_on_budget(git_repo, fake_cli)
    git(git_repo, "checkout", "-q", "main")
    git(git_repo, "branch", "-D", BRANCH)

    with pytest.raises(runs.RunError) as exc:
        runs.plan_resume(git_repo, run_dir_of(git_repo), RUN_ID)
    assert f"('{BRANCH}') is gone" in str(exc.value)


def test_a_branch_that_moved_on_is_refused(git_repo: Path, fake_cli: FakeCLI):
    stop_on_budget(git_repo, fake_cli)
    (git_repo / "someone_elses.txt").write_text("not the factory's\n", encoding="utf-8")
    git(git_repo, "add", "-A")
    git(git_repo, "commit", "-q", "-m", "a hand-made commit on the run branch")

    with pytest.raises(runs.RunError) as exc:
        runs.plan_resume(git_repo, run_dir_of(git_repo), RUN_ID)
    assert f"branch '{BRANCH}' has moved on" in str(exc.value)
    assert "would build on work this run never did" in str(exc.value)


def test_a_run_from_another_repo_is_refused(git_repo: Path, fake_cli: FakeCLI, tmp_path: Path):
    stop_on_budget(git_repo, fake_cli)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    with pytest.raises(runs.RunError) as exc:
        runs.plan_resume(elsewhere.resolve(), run_dir_of(git_repo), RUN_ID)
    assert f"ran against {git_repo}" in str(exc.value)


def test_a_run_that_is_still_running_is_refused(git_repo: Path, fake_cli: FakeCLI):
    stop_on_budget(git_repo, fake_cli)
    runs.update(run_dir_of(git_repo), state=runs.RUNNING, pid=os.getpid())

    with pytest.raises(runs.RunError) as exc:
        runs.plan_resume(git_repo, run_dir_of(git_repo), RUN_ID)
    assert "still running" in str(exc.value)
    assert f"--kill {RUN_ID}" in str(exc.value)


# --- the CLI surface ---------------------------------------------------------


def cli_args(repo: Path, *args: str) -> list[str]:
    return ["--repo", str(repo), "--runs-dir", str(repo.parent / "runs"), *args]


def test_resume_refuses_flags_that_would_contradict_the_record(git_repo: Path, capsys):
    for extra, expected in (
        (["a different request"], "takes the request from the recorded run"),
        (["--branch", "other"], "lands on the branch the original run created"),
        (["--workflow", "build_test"], "replays the workflow the original run recorded"),
    ):
        assert cli.main(cli_args(git_repo, "--resume", RUN_ID, *extra)) == 2
        assert expected in capsys.readouterr().err


def test_resume_of_an_unknown_run_exits_two_from_the_cli(git_repo: Path, capsys):
    assert cli.main(cli_args(git_repo, "--resume", "deadbeef")) == 2
    assert "unknown run 'deadbeef'" in capsys.readouterr().err


def test_list_runs_reflects_the_real_state_not_the_recorded_one(git_repo: Path, tmp_path: Path):
    root = tmp_path / "runs"
    for run_id, state, pid, started in (
        ("aaaa1111", runs.FINISHED, None, "2026-08-10T09:00:00+00:00"),
        ("bbbb2222", runs.RUNNING, os.getpid(), "2026-08-10T10:00:00+00:00"),
        ("cccc3333", runs.RUNNING, 999_999, "2026-08-10T11:00:00+00:00"),
    ):
        directory = root / run_id
        directory.mkdir(parents=True)
        runs.write(
            directory,
            runs.RunRecord(
                run_id=run_id,
                repo=str(git_repo),
                request="Add a /health endpoint",
                branch=f"factory/{run_id}",
                pid=pid,
                state=state,
                started=started,
            ),
        )

    known = {"aaaa1111", "bbbb2222", "cccc3333"}
    listed = [
        line.split() for line in cli.format_runs(root, runs.list_runs(root)).splitlines()
    ]
    body = [parts for parts in listed if parts and parts[0] in known]
    assert [parts[0] for parts in body] == ["cccc3333", "bbbb2222", "aaaa1111"]  # newest first

    rows = {parts[0]: parts for parts in body}
    assert rows["aaaa1111"][1:3] == ["finished", "-"]
    assert rows["bbbb2222"][1:3] == ["running", str(os.getpid())]
    # The record still claims `running`; the process does not exist, so the listing
    # says stopped rather than repeating the record's last hope.
    assert rows["cccc3333"][1:3] == ["stopped", "-"]


def test_list_runs_says_so_when_there_are_none(git_repo: Path, capsys):
    assert cli.main(cli_args(git_repo, "--list-runs")) == 0
    assert "no runs recorded under" in capsys.readouterr().out


def test_list_runs_needs_no_roles_no_checks_and_no_request(
    git_repo: Path, capsys, isolated_roles: Path
):
    """The registry must answer for a repo a real run would refuse to start in —
    this fixture repo has no pyproject.toml, so a run would exit 2 on the checks."""
    assert cli.main(cli_args(git_repo, "--list-runs")) == 0
    assert "no runs recorded" in capsys.readouterr().out
    assert not isolated_roles.exists()  # nothing was seeded on the way past


# --- kill --------------------------------------------------------------------


@pytest.fixture
def sleeper(tmp_path: Path):
    """A process whose command line looks like a factory run, and nothing else."""
    script = tmp_path / "run.py"
    script.write_text("import time\ntime.sleep(120)\n", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(script), "--repo", str(tmp_path)])
    # Reaped promptly, so `alive()` tells the truth instead of seeing a zombie child.
    threading.Thread(target=proc.wait, daemon=True).start()
    yield proc
    if proc.poll() is None:
        proc.kill()


def registered(root: Path, run_id: str, **fields) -> Path:
    directory = root / run_id
    directory.mkdir(parents=True, exist_ok=True)
    base = {"run_id": run_id, "repo": str(root), "state": runs.RUNNING}
    base.update(fields)
    runs.write(directory, runs.RunRecord(**base))
    return directory


def test_kill_terminates_a_verified_run_and_records_the_stop(tmp_path: Path, sleeper, capsys):
    root = tmp_path / "runs"
    registered(root, "live0001", pid=sleeper.pid, cmdline=runs.process_cmdline(sleeper.pid) or "")

    assert cli.kill_run(root, "live0001") == 0
    out = capsys.readouterr().out
    assert f"pid {sleeper.pid} verified as this run" in out
    assert f"SIGTERM sent to pid {sleeper.pid}" in out
    assert "is gone, state is now stopped" in out
    assert not runs.alive(sleeper.pid)

    record = runs.read(root / "live0001")
    assert record is not None
    assert record.state == runs.STOPPED and record.pid is None


def test_kill_refuses_when_the_pid_is_no_longer_this_run(tmp_path: Path, capsys):
    """The pid is THIS pytest process. If the refusal ever failed, this test would
    be killed by its own assertion — which is exactly the bug being guarded."""
    root = tmp_path / "runs"
    registered(
        root,
        "recycl01",
        pid=os.getpid(),
        cmdline="/usr/bin/python3 factory/run.py --repo /somewhere/else a request",
    )

    assert cli.kill_run(root, "recycl01") == 1
    err = capsys.readouterr().err
    assert "is NOT this run — the pid has been recycled" in err
    assert str(os.getpid()) in err
    # Nothing was signalled, and the record was left exactly as it was.
    record = runs.read(root / "recycl01")
    assert record is not None and record.state == runs.RUNNING and record.pid == os.getpid()


def test_kill_refuses_a_live_pid_that_is_not_a_factory_at_all(tmp_path: Path, capsys):
    """No recorded command line to compare against: the marker check still refuses."""
    root = tmp_path / "runs"
    registered(root, "nomark01", pid=os.getpid(), cmdline="")

    assert cli.kill_run(root, "nomark01") == 1
    assert "does not look like a factory run" in capsys.readouterr().err


def test_kill_refuses_a_stale_pid_without_signalling(tmp_path: Path, capsys):
    root = tmp_path / "runs"
    registered(root, "stale001", pid=999_999, cmdline="python3 factory/run.py x")

    assert cli.kill_run(root, "stale001") == 1
    assert "the pid record is stale, nothing was signalled" in capsys.readouterr().err


def test_kill_refuses_a_run_that_already_ended(tmp_path: Path, capsys):
    root = tmp_path / "runs"
    registered(root, "done0001", pid=None, state=runs.FINISHED)

    assert cli.kill_run(root, "done0001") == 1
    assert "is not running (state: finished)" in capsys.readouterr().err


def test_kill_of_an_unknown_run_exits_two(tmp_path: Path, capsys):
    assert cli.kill_run(tmp_path / "runs", "nosuchid") == 2
    assert "unknown run 'nosuchid'" in capsys.readouterr().err


def test_a_turn_that_never_comes_back_is_still_stoppable(tmp_path: Path, fake_cli: FakeCLI):
    """A hung run is hung inside an agent subprocess — SIGTERM must reach that too,
    or `--kill` just orphans a live `claude` behind the runner it terminated."""
    fake_cli.script([{"session_id": "s", "envelope": envelope(), "sleep_seconds": 60}])
    session = AgentSession(stage="build", model="haiku", cwd=tmp_path)
    turn: dict = {}
    sending = threading.Thread(target=lambda: turn.update(result=session.send("go")))
    sending.start()
    try:
        for _ in range(400):
            if agent._LIVE:
                break
            time.sleep(0.05)
        assert len(agent._LIVE) == 1
        assert agent.terminate_live() == 1
    finally:
        sending.join(timeout=30)

    assert not sending.is_alive()
    assert not agent._LIVE  # and the registry does not leak the dead process
    assert not turn["result"].ok


def test_terminate_never_signals_without_verifying_first(tmp_path: Path):
    record = runs.RunRecord(run_id="x", repo=str(tmp_path), pid=os.getpid(), cmdline="something else")
    with pytest.raises(runs.RunError):
        runs.terminate(record)
