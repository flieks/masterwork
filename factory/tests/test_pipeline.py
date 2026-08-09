"""End-to-end pipeline runs against a real temp git repo and the fake CLI."""

from __future__ import annotations

import json
from pathlib import Path

from adw.config import DEFAULT_MODELS, load_config
from adw.pipeline import Pipeline, RunResult
from adw.telemetry import AGENT_COLORS, Telemetry
from conftest import FakeCLI, PostSpy, envelope, git

COLLECTOR = "http://localhost:8008/api/v1/hooks/events"

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


def test_run_logs_do_not_pollute_the_stage_gates(git_repo: Path, fake_cli: FakeCLI):
    result, telemetry = run(git_repo, fake_cli, [PLAN_OK, BUILD_OK, REVIEW_OK, DOCUMENT_OK])
    assert result.accepted
    assert telemetry.path.is_file()
    assert git(git_repo, "status", "--porcelain").strip() == ""
