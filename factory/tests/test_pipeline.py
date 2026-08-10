"""End-to-end pipeline runs against a real temp git repo and the fake CLI."""

from __future__ import annotations

import json
from pathlib import Path

from adw.agent import RUN_ID_ENV, STAGE_ENV
from adw.config import DEFAULT_MODELS, load_config
from adw.pipeline import Pipeline, RunResult, format_summary
from adw.telemetry import AGENT_COLORS, Telemetry
from conftest import FakeCLI, PostSpy, envelope, git

COLLECTOR = "http://localhost:8008/api/v1/hooks/events"

# The JSONL record's fixed shape — pinned here too, so a POST-only addition that
# leaked into the local log would fail an end-to-end test, not just a unit one.
JSONL_KEYS = {
    "ts",
    "run",
    "phase",
    "event",
    "agent",
    "duration_ms",
    "tokens_in",
    "tokens_out",
    "cost_usd",
    "context_pct",
    "result",
    "detail",
}

PASSING_CHECK = "python3 -c pass"
MARKER_CHECK = (
    "python3 -c \"import pathlib,sys; sys.exit(0 if pathlib.Path('fixed.txt').exists() else 1)\""
)

REQUEST = "Add a /health endpoint"


def build_pipeline(
    repo: Path, checks: list[str] | None = None, **overrides
) -> tuple[Pipeline, Telemetry]:
    data: dict = {
        "telemetry_url": None,
        "checks": checks if checks is not None else [PASSING_CHECK],
        # Keep run logs out of both the fixture repo and the developer's real home.
        "runs_dir": str(repo.parent / "runs"),
    }
    data.update(overrides)
    (repo / "factory.config.json").write_text(json.dumps(data), encoding="utf-8")
    cfg = load_config(repo, run_id="testrun1")
    telemetry = Telemetry(run_id=cfg.run_id, repo=repo, run_dir=cfg.run_dir, url=cfg.telemetry_url)
    return Pipeline(cfg, REQUEST, telemetry), telemetry


def events(telemetry: Telemetry) -> list[dict]:
    return [json.loads(line) for line in telemetry.path.read_text().splitlines() if line.strip()]


def subjects(repo: Path) -> list[str]:
    return git(repo, "log", "--pretty=%s").strip().splitlines()


PLAN_OK = {
    "session_id": "plan-session",
    "text": "Let me read the repo. TRANSCRIPT_MARKER",
    "envelope": envelope(
        summary="Plan the health endpoint",
        artifacts=["plan.md"],
        changed_files=["plan.md"],
        notes_for_next_agent="Put the route in app.py",
    ),
    "write_files": {"plan.md": "# Plan\nAdd GET /health returning {'status': 'ok'}.\n"},
}
BUILD_OK = {
    "session_id": "build-session",
    "envelope": envelope(summary="Add the health route", changed_files=["app.py"]),
    "write_files": {"app.py": "def health():\n    return {'status': 'ok'}\n"},
}
REVIEW_OK = {
    "session_id": "review-session",
    "envelope": envelope(summary="Looks good", approved=True, blocking=[], changed_files=[]),
}
DOCUMENT_OK = {
    "session_id": "doc-session",
    "envelope": envelope(summary="Document the endpoint", changed_files=["docs/api.md"]),
    "write_files": {"docs/api.md": "## GET /health\n"},
}


def run(repo: Path, fake_cli: FakeCLI, script: list[dict], **kwargs) -> tuple[RunResult, Telemetry]:
    fake_cli.script(script)
    pipeline, telemetry = build_pipeline(repo, **kwargs)
    result = pipeline.run()
    telemetry.close()
    return result, telemetry


# --- happy path ------------------------------------------------------------


def test_a_clean_run_commits_every_stage_and_exits_zero(git_repo: Path, fake_cli: FakeCLI):
    result, telemetry = run(git_repo, fake_cli, [PLAN_OK, BUILD_OK, REVIEW_OK, DOCUMENT_OK])

    assert result.accepted
    assert result.exit_code == 0
    assert [o.status for o in result.outcomes] == ["passed"] * 5
    assert subjects(git_repo)[:3] == [
        "document: Document the endpoint",
        "build: Add the health route",
        "plan: Plan the health endpoint",
    ]
    assert (git_repo / "app.py").is_file()
    assert (git_repo / "docs" / "api.md").is_file()
    assert result.corrections == 0
    assert result.cost_usd > 0


def test_each_stage_gets_the_envelope_and_artifacts_but_never_a_transcript(
    git_repo: Path, fake_cli: FakeCLI
):
    run(git_repo, fake_cli, [PLAN_OK, BUILD_OK, REVIEW_OK, DOCUMENT_OK])

    build_prompt = fake_cli.calls[1]["prompt"]
    assert "Plan the health endpoint" in build_prompt  # the previous envelope
    assert "Add GET /health" in build_prompt  # the artifact it named
    assert "Put the route in app.py" in build_prompt  # notes_for_next_agent
    assert REQUEST in build_prompt  # the original request
    assert "TRANSCRIPT_MARKER" not in build_prompt  # never the conversation
    assert "WRITE BOUNDARY: any path inside this repository." in build_prompt

    plan_prompt = fake_cli.calls[0]["prompt"]
    assert "docs/specs/**" in plan_prompt
    review_prompt = fake_cli.calls[2]["prompt"]
    assert "WRITE BOUNDARY: NOTHING" in review_prompt


def test_the_role_identity_travels_as_a_system_prompt(git_repo: Path, fake_cli: FakeCLI):
    run(git_repo, fake_cli, [PLAN_OK, BUILD_OK, REVIEW_OK, DOCUMENT_OK])

    argv = fake_cli.calls[0]["argv"]
    system = argv[argv.index("--append-system-prompt") + 1]
    assert system.startswith("You are the PLAN stage")
    assert REQUEST not in system  # the task rides the user prompt, not the identity
    assert "You are the PLAN stage" not in fake_cli.calls[0]["prompt"]


def test_every_child_of_the_run_is_labelled_with_the_run_id_and_its_stage(
    git_repo: Path, fake_cli: FakeCLI
):
    """How masterwork links children to the run — so it must not depend on which
    stage, nor on whether the turn is a first one or a correction."""
    reject = {
        "session_id": "review-session",
        "envelope": envelope(
            summary="Needs error handling",
            approved=False,
            blocking=["app.py: health() has no error handling"],
            changed_files=[],
        ),
    }
    fix = {
        "session_id": "build-session",
        "envelope": envelope(summary="Handle errors", changed_files=["app.py"]),
        "write_files": {"app.py": "def health():\n    return {}\n"},
    }
    result, _ = run(git_repo, fake_cli, [PLAN_OK, BUILD_OK, reject, fix, REVIEW_OK, DOCUMENT_OK])
    assert result.accepted

    calls = fake_cli.calls
    assert [c["env"][STAGE_ENV] for c in calls] == [
        "plan",
        "build",
        "review",
        "build",  # the review correction — its own process, same stage session
        "review",
        "document",
    ]
    assert {c["env"][RUN_ID_ENV] for c in calls} == {"testrun1"}
    assert calls[3]["resume"] == "build-session"


def test_every_turn_leaves_the_compiled_prompts_in_the_run_dir(git_repo: Path, fake_cli: FakeCLI):
    """The prompt as actually sent — without it a bad run cannot be diagnosed."""
    result, telemetry = run(git_repo, fake_cli, [PLAN_OK, BUILD_OK, REVIEW_OK, DOCUMENT_OK])
    assert result.accepted

    prompts_dir = telemetry.path.parent / "prompts"
    assert sorted(p.name for p in prompts_dir.iterdir()) == ["build", "document", "plan", "review"]
    for role in ("plan", "build", "review", "document"):
        assert sorted(p.name for p in (prompts_dir / role).iterdir()) == [
            "1.system.md",
            "1.user.md",
        ]
    assert (prompts_dir / "plan" / "1.system.md").read_text().startswith("You are the PLAN stage")
    build_user = (prompts_dir / "build" / "1.user.md").read_text()
    assert build_user == fake_cli.calls[1]["prompt"]  # byte-identical to what was sent
    assert "Add GET /health" in build_user  # the artifact the plan named

    turns = [e for e in events(telemetry) if e["event"] == "agent_turn"]
    saved = turns[0]["payload"]["prompts"]
    assert Path(saved["system"]) == prompts_dir / "plan" / "1.system.md"
    assert Path(saved["user"]).is_file()


def test_a_correction_turn_is_saved_next_to_the_first(git_repo: Path, fake_cli: FakeCLI):
    chatty = dict(PLAN_OK)
    chatty["trailing"] = "Hope that helps!"
    _, telemetry = run(git_repo, fake_cli, [chatty, PLAN_OK, BUILD_OK, REVIEW_OK, DOCUMENT_OK])

    plan_prompts = telemetry.path.parent / "prompts" / "plan"
    assert sorted(p.name for p in plan_prompts.iterdir()) == [
        "1.system.md",
        "1.user.md",
        "2.system.md",
        "2.user.md",
    ]
    assert "GATE FAILURE" in (plan_prompts / "2.user.md").read_text()
    # A correction is a follow-up in the same session, so the identity is unchanged.
    assert (plan_prompts / "2.system.md").read_text() == (
        plan_prompts / "1.system.md"
    ).read_text()


def test_an_edited_role_file_shows_up_in_the_prompt_and_the_audit_copy(
    git_repo: Path, fake_cli: FakeCLI, isolated_roles: Path
):
    identity = isolated_roles / "build" / "system.md"
    identity.parent.mkdir(parents=True)
    identity.write_text("You are the BUILD stage.\nALWAYS ADD A HAIKU TO THE README.\n", "utf-8")

    result, telemetry = run(git_repo, fake_cli, [PLAN_OK, BUILD_OK, REVIEW_OK, DOCUMENT_OK])
    assert result.accepted

    argv = fake_cli.calls[1]["argv"]
    assert "ALWAYS ADD A HAIKU" in argv[argv.index("--append-system-prompt") + 1]
    saved = (telemetry.path.parent / "prompts" / "build" / "1.system.md").read_text()
    assert "ALWAYS ADD A HAIKU TO THE README." in saved


def test_assumptions_land_in_the_stage_commit_message(git_repo: Path, fake_cli: FakeCLI):
    build = dict(BUILD_OK)
    build["envelope"] = envelope(
        summary="Add the health route",
        changed_files=["app.py"],
        assumptions=["No auth is required on /health"],
    )
    run(git_repo, fake_cli, [PLAN_OK, build, REVIEW_OK, DOCUMENT_OK])
    body = git(git_repo, "log", "-1", "--skip", "1", "--pretty=%B")
    assert "Assumption: No auth is required on /health" in body


# --- gate 4: boundary ------------------------------------------------------


def test_an_out_of_boundary_write_is_reverted_and_corrected(git_repo: Path, fake_cli: FakeCLI):
    rogue = {
        "session_id": "plan-session",
        "envelope": envelope(
            summary="Plan the health endpoint",
            artifacts=["plan.md"],
            changed_files=["plan.md", "src/hack.py"],
        ),
        "write_files": {
            "plan.md": "# Plan\nAdd GET /health.\n",
            "src/hack.py": "# the planner should never write this\n",
        },
    }
    corrected = {
        "session_id": "plan-session",
        "envelope": envelope(
            summary="Plan the health endpoint", artifacts=["plan.md"], changed_files=["plan.md"]
        ),
    }
    result, telemetry = run(
        git_repo, fake_cli, [rogue, corrected, BUILD_OK, REVIEW_OK, DOCUMENT_OK]
    )

    assert not (git_repo / "src" / "hack.py").exists()  # reverted before the correction
    assert (git_repo / "plan.md").is_file()  # in-boundary work survived
    assert result.outcomes[0].corrections == 1
    assert result.accepted

    correction_call = fake_cli.calls[1]
    assert correction_call["resume"] == "plan-session"  # same session, not a cold restart
    assert "GATE FAILURE" in correction_call["prompt"]
    assert "src/hack.py" in correction_call["prompt"]

    failures = [e for e in events(telemetry) if e["event"] == "gate_fail"]
    assert any("reverted out-of-boundary paths: src/hack.py" in e["detail"] for e in failures)
    assert any(e["payload"].get("gate") == "boundary" for e in failures if "payload" in e)


def test_a_reverted_write_is_one_complaint_not_two(git_repo: Path, fake_cli: FakeCLI):
    """The revert removes the path from disk while the claim still lists it — the
    agent must hear about the boundary only, never 'you claimed a file you didn't change'."""
    rogue = {
        "session_id": "plan-session",
        "envelope": envelope(
            summary="Plan the health endpoint",
            artifacts=["plan.md"],
            changed_files=["plan.md", "src/hack.py"],
        ),
        "write_files": {"plan.md": "# Plan\n", "src/hack.py": "# out of bounds\n"},
    }
    corrected = {
        "session_id": "plan-session",
        "envelope": envelope(
            summary="Plan the health endpoint", artifacts=["plan.md"], changed_files=["plan.md"]
        ),
    }
    result, telemetry = run(
        git_repo, fake_cli, [rogue, corrected, BUILD_OK, REVIEW_OK, DOCUMENT_OK]
    )
    assert result.accepted

    prompt = fake_cli.calls[1]["prompt"]
    assert "[boundary]" in prompt
    assert "[changed_files]" not in prompt
    assert "claimed but not changed on disk" not in prompt

    gates_named = [
        e["payload"]["gate"]
        for e in events(telemetry)
        if e["event"] == "gate_fail" and e.get("payload", {}).get("gate")
    ]
    assert gates_named == ["boundary"]  # changed_files passed on the same turn


def test_a_reviewer_that_writes_is_reverted(git_repo: Path, fake_cli: FakeCLI):
    rogue_review = {
        "session_id": "review-session",
        "envelope": envelope(summary="Fixed it myself", approved=True, changed_files=["app.py"]),
        "write_files": {"app.py": "def health():\n    return {'status': 'PATCHED BY REVIEWER'}\n"},
    }
    result, _ = run(git_repo, fake_cli, [PLAN_OK, BUILD_OK, rogue_review, REVIEW_OK, DOCUMENT_OK])

    assert "PATCHED BY REVIEWER" not in (git_repo / "app.py").read_text()
    assert result.accepted
    review_outcome = next(o for o in result.outcomes if o.name == "review")
    assert review_outcome.corrections == 1


# --- gate 3: changed-files truth -------------------------------------------


def test_a_builder_that_invents_work_fails_the_gate(git_repo: Path, fake_cli: FakeCLI):
    liar = {
        "session_id": "build-session",
        "envelope": envelope(summary="Add it all", changed_files=["app.py", "tests/test_app.py"]),
        "write_files": {"app.py": "x = 1\n"},
    }
    honest = {
        "session_id": "build-session",
        "envelope": envelope(summary="Add the route", changed_files=["app.py"]),
    }
    result, _ = run(git_repo, fake_cli, [PLAN_OK, liar, honest, REVIEW_OK, DOCUMENT_OK])

    assert result.accepted
    assert result.outcomes[1].corrections == 1
    assert "tests/test_app.py" in fake_cli.calls[2]["prompt"]


def test_an_undeclared_file_also_fails_the_gate(git_repo: Path, fake_cli: FakeCLI):
    sneaky = {
        "session_id": "build-session",
        "envelope": envelope(summary="Add the route", changed_files=["app.py"]),
        "write_files": {"app.py": "x = 1\n", "sneaky.py": "import os\n"},
    }
    honest = {
        "session_id": "build-session",
        "envelope": envelope(summary="Add the route", changed_files=["app.py", "sneaky.py"]),
    }
    result, _ = run(git_repo, fake_cli, [PLAN_OK, sneaky, honest, REVIEW_OK, DOCUMENT_OK])

    assert result.accepted
    assert "changed on disk but not declared: sneaky.py" in fake_cli.calls[2]["prompt"]


# --- gate 1 + correction cap ----------------------------------------------


def test_the_correction_cap_aborts_the_run(git_repo: Path, fake_cli: FakeCLI):
    no_envelope = {"session_id": "plan-session", "raw_reply": "Done! Everything works."}
    result, telemetry = run(
        git_repo, fake_cli, [no_envelope, no_envelope, no_envelope, no_envelope]
    )

    assert not result.accepted
    assert result.exit_code == 1
    assert len(fake_cli.calls) == 3  # first turn + exactly max_corrections retries
    assert result.outcomes[0].corrections == 2
    assert "correction cap (2) reached" in result.outcomes[0].detail
    assert subjects(git_repo) == ["initial"]  # nothing committed
    assert [e for e in events(telemetry) if e["event"] == "run_end"][0]["result"] == "fail"


def test_trailing_prose_is_a_correctable_gate_failure(git_repo: Path, fake_cli: FakeCLI):
    chatty = dict(PLAN_OK)
    chatty["trailing"] = "Hope that helps! Let me know."
    result, _ = run(git_repo, fake_cli, [chatty, PLAN_OK, BUILD_OK, REVIEW_OK, DOCUMENT_OK])

    assert result.accepted
    assert "text after the envelope block" in fake_cli.calls[1]["prompt"]


# --- gate 6: executed checks ----------------------------------------------


def test_failing_checks_send_a_correction_into_the_builder_session(
    git_repo: Path, fake_cli: FakeCLI
):
    fixer = {
        "session_id": "build-session",
        "envelope": envelope(summary="Fix the failing check", changed_files=["fixed.txt"]),
        "write_files": {"fixed.txt": "green\n"},
    }
    result, telemetry = run(
        git_repo,
        fake_cli,
        [PLAN_OK, BUILD_OK, fixer, REVIEW_OK, DOCUMENT_OK],
        checks=[MARKER_CHECK],
    )

    assert result.accepted
    checks_outcome = next(o for o in result.outcomes if o.name == "checks")
    assert checks_outcome.corrections == 1
    correction = fake_cli.calls[2]
    assert correction["resume"] == "build-session"
    assert correction["env"][STAGE_ENV] == "build"  # a checks correction is a build child
    assert "EXECUTED CHECKS FAILED" in correction["prompt"]
    assert "do not disable, skip, or weaken" in correction["prompt"]
    assert any(e["event"] == "gate_fail" and "checks" in e["detail"] for e in events(telemetry))


def test_checks_that_never_go_green_abort_the_run(git_repo: Path, fake_cli: FakeCLI):
    no_op = {
        "session_id": "build-session",
        "envelope": envelope(summary="I tried", changed_files=[]),
    }
    result, _ = run(
        git_repo,
        fake_cli,
        [PLAN_OK, BUILD_OK, no_op, no_op, REVIEW_OK],
        checks=[MARKER_CHECK],
    )

    assert not result.accepted
    assert result.exit_code == 1
    assert "executed checks failed" in result.reason
    assert "review" not in {o.name for o in result.outcomes}  # never reached


def test_an_explicitly_unverified_run_never_looks_like_a_verified_one(
    git_repo: Path, fake_cli: FakeCLI
):
    result, telemetry = run(
        git_repo, fake_cli, [PLAN_OK, BUILD_OK, REVIEW_OK, DOCUMENT_OK], checks=[]
    )

    assert result.accepted  # an explicit opt-out is allowed to run…
    assert not result.verified  # …but it is on the record as unverified
    assert "ACCEPTED (UNVERIFIED — no checks ran)" in format_summary(result)

    stats = [e for e in events(telemetry) if e["event"] == "run_end"][0]["stats"]
    assert stats["accepted"] is True
    assert stats["verified"] is False


def test_a_verified_run_says_so_in_the_summary_and_the_stats(git_repo: Path, fake_cli: FakeCLI):
    result, telemetry = run(git_repo, fake_cli, [PLAN_OK, BUILD_OK, REVIEW_OK, DOCUMENT_OK])
    summary = format_summary(result)

    assert result.verified
    assert "ACCEPTED —" in summary and "UNVERIFIED" not in summary
    stats = [e for e in events(telemetry) if e["event"] == "run_end"][0]["stats"]
    assert stats["verified"] is True


def test_an_unverified_run_that_fails_is_labelled_too(git_repo: Path, fake_cli: FakeCLI):
    no_envelope = {"session_id": "plan-session", "raw_reply": "Done! Everything works."}
    result, _ = run(git_repo, fake_cli, [no_envelope] * 4, checks=[])
    assert not result.accepted
    assert "NOT ACCEPTED (UNVERIFIED — no checks ran)" in format_summary(result)


def test_run_logs_land_outside_the_target_repo(git_repo: Path, fake_cli: FakeCLI):
    result, telemetry = run(git_repo, fake_cli, [PLAN_OK, BUILD_OK, REVIEW_OK, DOCUMENT_OK])

    assert result.accepted
    assert git_repo not in telemetry.path.parents
    assert not (git_repo / "factory" / "runs").exists()
    assert not (git_repo / "factory").exists()  # not even a .gitignore was planted


def test_an_agents_claim_that_tests_pass_counts_for_nothing(git_repo: Path, fake_cli: FakeCLI):
    liar = {
        "session_id": "build-session",
        "envelope": envelope(summary="All tests pass!", changed_files=["app.py"]),
        "write_files": {"app.py": "x = 1\n"},
    }
    result, _ = run(
        git_repo,
        fake_cli,
        [PLAN_OK, liar, liar, liar, REVIEW_OK],
        checks=['python3 -c "import sys; sys.exit(1)"'],
    )
    assert not result.accepted


# --- review loop -----------------------------------------------------------


def test_a_rejected_review_loops_back_into_the_builder_then_approves(
    git_repo: Path, fake_cli: FakeCLI
):
    reject = {
        "session_id": "review-session",
        "envelope": envelope(
            summary="Needs error handling",
            approved=False,
            blocking=["app.py: health() has no error handling"],
            changed_files=[],
        ),
    }
    fix = {
        "session_id": "build-session",
        "envelope": envelope(summary="Handle errors", changed_files=["app.py"]),
        "write_files": {
            "app.py": "def health():\n    try:\n        return {}\n    finally:\n        pass\n"
        },
    }
    result, _ = run(git_repo, fake_cli, [PLAN_OK, BUILD_OK, reject, fix, REVIEW_OK, DOCUMENT_OK])

    assert result.accepted
    assert result.unresolved == []
    fix_call = fake_cli.calls[3]
    assert fix_call["resume"] == "build-session"
    assert "REVIEW REJECTED (round 1 of 2)" in fix_call["prompt"]
    assert "health() has no error handling" in fix_call["prompt"]
    # The correction is gated against a fresh snapshot, so the claim is turn-scoped.
    assert "files you change in THIS turn" in fix_call["prompt"]
    # The builder is told the findings and nothing else from the review.
    assert "Needs error handling" not in fix_call["prompt"]
    assert [o.name for o in result.outcomes] == [
        "plan",
        "build",
        "checks",
        "review",
        "build",
        "checks",
        "review",
        "document",
    ]


def test_review_at_the_cap_stops_with_unresolved_findings(git_repo: Path, fake_cli: FakeCLI):
    reject = {
        "session_id": "review-session",
        "envelope": envelope(
            summary="Still not right",
            approved=False,
            blocking=["app.py: still no tests"],
            changed_files=[],
        ),
    }
    fix = {
        "session_id": "build-session",
        "envelope": envelope(summary="Tried again", changed_files=["app.py"]),
        "write_files": {"app.py": "x = 2\n"},
    }
    result, _ = run(git_repo, fake_cli, [PLAN_OK, BUILD_OK, reject, fix, reject, DOCUMENT_OK])

    assert not result.accepted
    assert result.exit_code == 1
    assert result.unresolved == ["app.py: still no tests"]
    assert "review did not approve within 2 round(s)" in result.reason
    assert "document" not in subjects(git_repo)[0]  # documenting never ran
    assert len(fake_cli.calls) == 5


def test_a_contradictory_verdict_is_sent_back_to_the_reviewer(git_repo: Path, fake_cli: FakeCLI):
    contradiction = {
        "session_id": "review-session",
        "envelope": envelope(
            summary="Approved with concerns",
            approved=True,
            blocking=["app.py: no tests"],
            changed_files=[],
        ),
    }
    result, _ = run(git_repo, fake_cli, [PLAN_OK, BUILD_OK, contradiction, REVIEW_OK, DOCUMENT_OK])

    assert result.accepted
    assert "blocking finding(s) still listed" in fake_cli.calls[3]["prompt"]


def test_a_blocked_review_that_lists_findings_loops_back_to_the_builder(
    git_repo: Path, fake_cli: FakeCLI
):
    """The hole this closes: `status: "blocked"` used to end the run before the review
    loop ran, so a reviewer that disapproved on the wrong axis skipped the correction
    the loop exists for. Findings mean the work was reviewed — that is a rejection."""
    blocked_rejection = {
        "session_id": "review-session",
        "envelope": envelope(
            status="blocked",
            summary="The marker comments violate the repo's comment convention.",
            approved=False,
            blocking=["app.py: remove the MARKER comments"],
            changed_files=[],
        ),
    }
    fix = {
        "session_id": "build-session",
        "envelope": envelope(summary="Remove the markers", changed_files=["app.py"]),
        "write_files": {"app.py": "def health():\n    return {'ok': True}\n"},
    }
    result, telemetry = run(
        git_repo, fake_cli, [PLAN_OK, BUILD_OK, blocked_rejection, fix, REVIEW_OK, DOCUMENT_OK]
    )

    assert result.accepted  # the run recovered instead of stopping at the reviewer
    assert [o.name for o in result.outcomes] == [
        "plan",
        "build",
        "checks",
        "review",
        "build",  # the loop reached the builder
        "checks",
        "review",
        "document",
    ]
    fix_call = fake_cli.calls[3]
    assert fix_call["resume"] == "build-session"
    assert "REVIEW REJECTED (round 1 of 2)" in fix_call["prompt"]
    assert "remove the MARKER comments" in fix_call["prompt"]

    # …and nothing about it is silent: the stage row and the telemetry both say the
    # runner did not take `blocked` at face value.
    review_outcome = result.outcomes[3]
    assert review_outcome.status == "passed"
    assert review_outcome.detail.startswith('read as a rejection despite status="blocked"')
    assert 'read as a rejection despite status="blocked"' in format_summary(result)
    noted = [
        e
        for e in events(telemetry)
        if e["event"] == "gate_fail" and "read as a rejection" in e["detail"]
    ]
    assert len(noted) == 1
    assert noted[0]["payload"] == {
        "verdict": "rejection",
        "status": "blocked",
        "blocking": ["app.py: remove the MARKER comments"],
    }
    verdicts = [e for e in events(telemetry) if "read as a rejection" in e.get("detail", "")]
    assert any(e["event"] == "gate_pass" for e in verdicts)  # the gate agrees, on the record


def test_a_reviewer_that_truly_cannot_review_still_stops_the_run_cleanly(
    git_repo: Path, fake_cli: FakeCLI
):
    """The genuine case: no findings, a stated reason — `blocked` still means stop."""
    cannot_review = {
        "session_id": "review-session",
        "envelope": envelope(
            status="blocked",
            summary="The plan artifact is gone from the tree; there is nothing to review.",
            approved=False,
            blocking=[],
            changed_files=[],
        ),
    }
    result, telemetry = run(git_repo, fake_cli, [PLAN_OK, BUILD_OK, cannot_review, DOCUMENT_OK])

    assert not result.accepted
    assert result.exit_code == 1
    assert result.outcomes[3].status == "blocked"
    assert "nothing to review" in result.reason
    assert len(fake_cli.calls) == 3  # no correction, and document never ran
    assert "document" not in subjects(git_repo)[0]
    assert any(
        e["event"] == "gate_fail" and "status=blocked" in e["detail"] for e in events(telemetry)
    )
    assert not any("read as a rejection" in e["detail"] for e in events(telemetry))


def test_a_blocked_review_with_neither_findings_nor_a_reason_is_corrected(
    git_repo: Path, fake_cli: FakeCLI
):
    """`approved: false` + empty blocking + a non-ok status is no longer a free pass:
    the verdict gate now runs on every review envelope, not only the `ok` ones."""
    mute = {
        "session_id": "review-session",
        "envelope": envelope(status="blocked", summary="   ", approved=False, changed_files=[]),
    }
    result, _ = run(git_repo, fake_cli, [PLAN_OK, BUILD_OK, mute, REVIEW_OK, DOCUMENT_OK])

    assert result.accepted
    assert fake_cli.calls[3]["resume"] == "review-session"  # the reviewer answers for it
    correction = fake_cli.calls[3]["prompt"]
    assert "GATE FAILURE" in correction
    assert "no findings and no reason" in correction
    assert next(o for o in result.outcomes if o.name == "review").corrections == 1


# --- unattended rule -------------------------------------------------------


def test_a_blocked_stage_stops_the_run_cleanly(git_repo: Path, fake_cli: FakeCLI):
    blocked = {
        "session_id": "build-session",
        "envelope": envelope(
            status="blocked",
            summary="This needs a destructive migration on the users table.",
            changed_files=[],
        ),
    }
    result, telemetry = run(git_repo, fake_cli, [PLAN_OK, blocked, REVIEW_OK])

    assert not result.accepted
    assert result.exit_code == 1
    assert result.outcomes[1].status == "blocked"
    assert "destructive migration" in result.reason
    assert len(fake_cli.calls) == 2  # no correction, no further stages
    assert subjects(git_repo)[0] == "plan: Plan the health endpoint"
    assert any(
        e["event"] == "gate_fail" and "status=blocked" in e["detail"] for e in events(telemetry)
    )


# --- telemetry -------------------------------------------------------------


def test_telemetry_covers_the_whole_run(git_repo: Path, fake_cli: FakeCLI):
    result, telemetry = run(git_repo, fake_cli, [PLAN_OK, BUILD_OK, REVIEW_OK, DOCUMENT_OK])
    records = events(telemetry)
    kinds = {r["event"] for r in records}

    assert {
        "phase_start",
        "phase_end",
        "agent_turn",
        "tool_call",
        "gate_pass",
        "commit",
        "run_end",
    } <= kinds
    assert [r["phase"] for r in records if r["event"] == "phase_end"] == [
        "plan",
        "build",
        "checks",
        "review",
        "document",
    ]
    turns = [r for r in records if r["event"] == "agent_turn"]
    assert len(turns) == 4
    assert all(r["tokens_in"] > 0 and r["cost_usd"] > 0 for r in turns)
    assert turns[-1]["context_pct"] > turns[0]["context_pct"]  # cumulative context

    end = [r for r in records if r["event"] == "run_end"][0]
    assert end["stats"]["accepted"] is True
    assert end["stats"]["turns"] == 4
    assert end["stats"]["corrections"] == 0
    assert set(end["stats"]["stages"]) == {"plan", "build", "checks", "review", "document"}
    assert end["stats"]["cost_usd"] == result.cost_usd
    assert [r for r in records if r["event"] == "commit"][0]["payload"]["sha"]


def test_every_turn_records_which_layer_each_prompt_file_came_from(
    git_repo: Path, fake_cli: FakeCLI, post_spy: PostSpy, isolated_roles: Path
):
    """The prompt masterwork displays is the library copy — a repo override wins
    over it silently, so the run record has to say which one actually ran."""
    library_user = isolated_roles / "build" / "user.md"
    library_user.parent.mkdir(parents=True)
    library_user.write_text(
        "## Original request\n{{request}}\n\n{{boundary}}\n\n{{envelope_contract}}\n",
        encoding="utf-8",
    )
    override = git_repo / ".masterwork" / "agents" / "build" / "system.md"
    override.parent.mkdir(parents=True)
    override.write_text("HOUSE BUILDER\n", encoding="utf-8")

    result, telemetry = run(
        git_repo, fake_cli, [PLAN_OK, BUILD_OK, REVIEW_OK, DOCUMENT_OK], telemetry_url=COLLECTOR
    )
    assert result.accepted

    layers = {
        r["agent"]: r["payload"]["role_layers"]
        for r in events(telemetry)
        if r["event"] == "agent_turn"
    }
    assert layers["build"] == {
        "system.md": "repo",
        "user.md": "library",
        "role.json": "builtin",
    }
    assert layers["plan"] == dict.fromkeys(("system.md", "user.md", "role.json"), "builtin")
    posted = {
        body["payload"]["agent"]: body["payload"]["payload"]["role_layers"]
        for body in post_spy.of("agent_turn")
    }
    assert posted["build"] == layers["build"]  # and it reaches the collector, not just the JSONL


def test_the_run_posts_the_v113_contract(git_repo: Path, fake_cli: FakeCLI, post_spy: PostSpy):
    script = [
        dict(spec, cache_read_tokens=40_000)
        for spec in (PLAN_OK, BUILD_OK, REVIEW_OK, DOCUMENT_OK)
    ]
    result, _ = run(git_repo, fake_cli, script, telemetry_url=COLLECTOR)
    assert result.accepted

    start = post_spy.bodies[0]
    assert (start["title"], start["workflow"], start["status"]) == (REQUEST, "factory", "running")
    assert "phase" not in start

    starts = [b["phase"] for b in post_spy.of("phase_start") if "phase" in b]
    assert [(p["name"], p["kind"]) for p in starts] == [
        ("plan", "agent"),
        ("build", "agent"),
        ("checks", "code"),
        ("review", "agent"),
        ("document", "agent"),
    ]
    ends = {b["phase"]["name"]: b["phase"] for b in post_spy.of("phase_end")}
    assert [p["seq"] for p in starts] == [ends[p["name"]]["seq"] for p in starts]
    assert {p["status"] for p in ends.values()} == {"passed"}
    assert ends["build"]["cost_usd"] > 0
    assert ends["build"]["tokens_in"] > 0
    assert ends["build"]["commit_sha"]

    commits = [b["phase"] for b in post_spy.of("commit")]
    assert [p["kind"] for p in commits] == ["git"] * 3  # plan, build, document
    assert [p["name"] for p in commits] == ["commit:plan", "commit:build", "commit:document"]
    # One row per commit, each holding the sha of that commit and nobody else's.
    assert len({p["commit_sha"] for p in commits}) == 3
    assert {p["agent"] for p in commits} == {"git"}
    git_lane = {"name": "git", "color": AGENT_COLORS["git"]}
    assert [b["agent"] for b in post_spy.of("commit")] == [git_lane] * 3

    rows = sorted(p["seq"] for p in starts + commits)
    assert rows == list(range(1, len(rows) + 1))  # every row owns a slot, none skipped

    lanes = {b["agent"]["name"]: b["agent"] for b in post_spy.of("agent_turn")}
    assert set(lanes) == {"plan", "build", "review", "document"}
    for name, lane in lanes.items():
        assert lane["model"] == DEFAULT_MODELS[name]
        assert lane["color"] == AGENT_COLORS[name]
        assert lane["context_window"] == 200_000
        assert lane["context_tokens"] == 40_020  # 20 input + 40_000 cache read
        assert lane["cost_usd"] > 0 and lane["tokens_in"] > 0

    tools = post_spy.of("tool_call")
    results = [b for b in tools if b["tool_name"] == "tool_result"]
    assert results and all(b["ok"] is True and "duration_ms" in b for b in results)
    assert all(b["agent"]["name"] in lanes for b in tools)

    end = post_spy.of("run_end")[0]
    assert end["status"] == "success"
    assert end["ended"] is True
    assert end["title"] == REQUEST
    assert end["stats"]["accepted"] is True


def test_a_failed_run_posts_status_failed(git_repo: Path, fake_cli: FakeCLI, post_spy: PostSpy):
    no_envelope = {"session_id": "plan-session", "raw_reply": "Done! Everything works."}
    result, _ = run(git_repo, fake_cli, [no_envelope] * 4, telemetry_url=COLLECTOR)

    assert not result.accepted
    end = post_spy.of("run_end")[0]
    assert end["status"] == "failed"
    assert end["ok"] is False

    plan = post_spy.of("phase_end")[0]["phase"]
    assert (plan["name"], plan["seq"], plan["status"]) == ("plan", 1, "failed")
    assert plan["corrections"] == 2
    assert "commit_sha" not in plan  # nothing was committed
    assert post_spy.of("commit") == []


# --- v1.19 evidence: the gate's sentence and the envelope it judged ---------


def gate_blocks(spy: PostSpy) -> list[tuple[str, dict]]:
    """(phase, gate block) for every gate the runner stated on the wire."""
    return [(body["phase"]["name"], body["gate"]) for body in spy.bodies if "gate" in body]


def envelope_blocks(spy: PostSpy) -> list[dict]:
    return [body["envelope"] for body in spy.bodies if "envelope" in body]


def test_a_clean_run_posts_every_gates_note_and_every_envelope(
    git_repo: Path, fake_cli: FakeCLI, post_spy: PostSpy
):
    result, _ = run(
        git_repo, fake_cli, [PLAN_OK, BUILD_OK, REVIEW_OK, DOCUMENT_OK], telemetry_url=COLLECTOR
    )
    assert result.accepted

    posted = gate_blocks(post_spy)
    # Passing gates are stated too: which gates never fail is as informative as which do.
    assert all(block["ok"] and block["note"] for _, block in posted)
    assert [b["name"] for phase, b in posted if phase == "plan"] == [
        "envelope",
        "artifacts",
        "changed_files",
        "boundary",
    ]
    assert "verdict" in [b["name"] for phase, b in posted if phase == "review"]
    assert ("plan", "changed_files") in [(p, b["name"]) for p, b in posted]
    plan_files = next(b for p, b in posted if p == "plan" and b["name"] == "changed_files")
    assert plan_files["note"] == "1 file(s) match the claim"

    # The `checks` gate runs a command per row, so it carries a row per command.
    checks = next(b for _, b in posted if b["name"] == "checks")
    assert checks["checks"] == [{"item": PASSING_CHECK, "ok": True, "note": "exited 0"}]

    attempts = envelope_blocks(post_spy)
    assert [e["role"] for e in attempts] == ["plan", "build", "review", "document"]
    assert all(e["parsed"] and e["attempt"] == 1 and e["status"] == "ok" for e in attempts)
    assert attempts[0]["body"] == PLAN_OK["envelope"]  # the envelope verbatim
    assert "TRANSCRIPT_MARKER" in attempts[0]["raw_text"]  # the reply it was read from


def test_a_failing_gates_sentence_reaches_the_wire(
    git_repo: Path, fake_cli: FakeCLI, post_spy: PostSpy
):
    """The whole diagnostic value: not *3 failed*, but the sentence that says why."""
    liar = {
        "session_id": "build-session",
        "envelope": envelope(summary="Add it all", changed_files=["app.py", "tests/test_app.py"]),
        "write_files": {"app.py": "x = 1\n"},
    }
    honest = {
        "session_id": "build-session",
        "envelope": envelope(summary="Add the route", changed_files=["app.py"]),
    }
    result, _ = run(
        git_repo, fake_cli, [PLAN_OK, liar, honest, REVIEW_OK, DOCUMENT_OK], telemetry_url=COLLECTOR
    )
    assert result.accepted

    failed = [block for _, block in gate_blocks(post_spy) if not block["ok"]]
    assert [(b["name"], b["attempt"]) for b in failed] == [("changed_files", 1)]
    assert failed[0]["note"] == "claimed but not changed on disk: tests/test_app.py"


def test_a_reply_that_does_not_parse_posts_the_attempt_and_the_raw_text(
    git_repo: Path, fake_cli: FakeCLI, post_spy: PostSpy
):
    """The rows that were invisible before: a turn whose envelope never parsed."""
    no_envelope = {"session_id": "plan-session", "raw_reply": "Done! Everything works."}
    result, _ = run(
        git_repo,
        fake_cli,
        [no_envelope, PLAN_OK, BUILD_OK, REVIEW_OK, DOCUMENT_OK],
        telemetry_url=COLLECTOR,
    )
    assert result.accepted

    assert envelope_blocks(post_spy)[0] == {
        "role": "plan",
        "attempt": 1,
        "parsed": False,
        "raw_text": "Done! Everything works.",
        "parse_error": "no fenced code block found in the reply",
    }
    # The attempt rides its own gate's event, so one event states both.
    carrier = next(b for b in post_spy.bodies if "envelope" in b)
    assert carrier["gate"]["name"] == "envelope"
    assert carrier["gate"]["ok"] is False


def test_attempts_increment_across_a_correction_round(
    git_repo: Path, fake_cli: FakeCLI, post_spy: PostSpy
):
    chatty = dict(PLAN_OK)
    chatty["trailing"] = "Hope that helps!"
    result, _ = run(
        git_repo,
        fake_cli,
        [chatty, PLAN_OK, BUILD_OK, REVIEW_OK, DOCUMENT_OK],
        telemetry_url=COLLECTOR,
    )
    assert result.accepted

    plan = [e for e in envelope_blocks(post_spy) if e["role"] == "plan"]
    assert [(e["attempt"], e["parsed"]) for e in plan] == [(1, False), (2, True)]
    assert [(b["name"], b["attempt"]) for phase, b in gate_blocks(post_spy) if phase == "plan"] == [
        # The first turn never got past the envelope, so only two gates could run.
        ("envelope", 1),
        ("boundary", 1),
        ("envelope", 2),
        ("artifacts", 2),
        ("changed_files", 2),
        ("boundary", 2),
    ]


def test_a_rerun_check_is_attempt_two_and_names_the_command_that_failed(
    git_repo: Path, fake_cli: FakeCLI, post_spy: PostSpy
):
    fixer = {
        "session_id": "build-session",
        "envelope": envelope(summary="Fix the failing check", changed_files=["fixed.txt"]),
        "write_files": {"fixed.txt": "green\n"},
    }
    result, _ = run(
        git_repo,
        fake_cli,
        [PLAN_OK, BUILD_OK, fixer, REVIEW_OK, DOCUMENT_OK],
        checks=[MARKER_CHECK],
        telemetry_url=COLLECTOR,
    )
    assert result.accepted

    checks = [block for _, block in gate_blocks(post_spy) if block["name"] == "checks"]
    assert [(b["attempt"], b["ok"]) for b in checks] == [(1, False), (2, True)]
    assert checks[0]["checks"][0]["item"] == MARKER_CHECK
    assert checks[0]["checks"][0]["note"] == "exited 1"
    assert checks[1]["checks"][0]["ok"] is True


def test_a_blocked_stage_still_posts_the_envelope_that_stopped_the_run(
    git_repo: Path, fake_cli: FakeCLI, post_spy: PostSpy
):
    """A clean stop skips the gates — the envelope must not vanish with them."""
    blocked = {
        "session_id": "build-session",
        "envelope": envelope(
            status="blocked",
            summary="This needs a destructive migration on the users table.",
            changed_files=[],
        ),
    }
    result, _ = run(git_repo, fake_cli, [PLAN_OK, blocked, REVIEW_OK], telemetry_url=COLLECTOR)
    assert not result.accepted

    stopper = envelope_blocks(post_spy)[-1]
    assert (stopper["role"], stopper["parsed"], stopper["status"]) == ("build", True, "blocked")
    assert stopper["body"]["summary"].startswith("This needs a destructive migration")
    # The stage-level verdict is stated too, so nothing is lost by stating a block.
    carrier = [b for b in post_spy.bodies if "envelope" in b][-1]
    assert carrier["gate"] == {
        "name": "stage",
        "attempt": 1,
        "ok": False,
        "note": (
            "stage returned status=blocked: "
            "This needs a destructive migration on the users table."
        ),
    }


def test_the_evidence_blocks_never_enter_the_jsonl(git_repo: Path, fake_cli: FakeCLI):
    """v1.19 is POST-body only, exactly as v1.13 was: the local record is untouched."""
    result, telemetry = run(git_repo, fake_cli, [PLAN_OK, BUILD_OK, REVIEW_OK, DOCUMENT_OK])
    assert result.accepted

    for record in events(telemetry):
        assert set(record) <= JSONL_KEYS | {"tool_name", "payload", "stats"}
    gate_lines = [r for r in events(telemetry) if r["event"].startswith("gate_")]
    assert gate_lines and all(set(r["payload"]) == {"gate"} for r in gate_lines)


def test_run_logs_do_not_pollute_the_stage_gates(git_repo: Path, fake_cli: FakeCLI):
    result, telemetry = run(git_repo, fake_cli, [PLAN_OK, BUILD_OK, REVIEW_OK, DOCUMENT_OK])
    assert result.accepted
    assert telemetry.path.is_file()
    assert git(git_repo, "status", "--porcelain").strip() == ""
