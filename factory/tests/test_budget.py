"""Cost and token caps: a run that stops itself, without abandoning what it built."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import run as cli
from adw.config import ConfigError, load_config
from adw.pipeline import budget_report, format_summary
from conftest import FakeCLI, git
from test_pipeline import BUILD_OK, DOCUMENT_OK, PLAN_OK, REVIEW_OK, events, run, subjects

SCRIPT = [PLAN_OK, BUILD_OK, REVIEW_OK, DOCUMENT_OK]


def budget_events(telemetry) -> list[dict]:
    return [e for e in events(telemetry) if (e.get("payload") or {}).get("gate") == "budget"]


def priced(spec: dict, cost: float) -> dict:
    return dict(spec, cost_usd=cost)


# --- the case that hurts: a cap that fires mid-run ---------------------------


def test_a_cost_cap_stops_the_run_and_keeps_what_was_already_committed(
    git_repo: Path, fake_cli: FakeCLI
):
    result, _ = run(
        git_repo,
        fake_cli,
        [priced(PLAN_OK, 0.01), priced(BUILD_OK, 0.40), REVIEW_OK, DOCUMENT_OK],
        max_cost_usd=0.25,
    )

    assert not result.accepted
    assert result.exit_code == 1
    assert result.reason == "cost cap reached: $0.4100 of $0.25 budget"
    # Stopped inside `build`, so nothing downstream of it was ever launched.
    assert [o.name for o in result.outcomes] == ["plan", "build"]
    assert result.outcomes[1].status == "blocked"
    assert len(fake_cli.calls) == 2
    assert result.skipped == ["checks", "review", "document"]

    # The plan's commit is untouched — a stopped run is not a reverted one.
    assert subjects(git_repo) == ["plan: Plan the health endpoint", "initial"]
    assert [name for name, _ in result.committed] == ["plan"]
    # …and the build's own work is still on disk, just never committed.
    assert (git_repo / "app.py").is_file()
    assert "app.py" in git(git_repo, "status", "--porcelain")


def test_the_summary_states_the_figure_the_cap_and_what_survived(
    git_repo: Path, fake_cli: FakeCLI
):
    result, _ = run(
        git_repo,
        fake_cli,
        [priced(PLAN_OK, 0.01), priced(BUILD_OK, 0.40), REVIEW_OK, DOCUMENT_OK],
        max_cost_usd=0.25,
    )
    summary = format_summary(result)

    assert "STOPPED ON BUDGET — cost cap reached: $0.4100 of $0.25 budget" in summary
    assert "kept (already committed): plan " in summary
    assert "skipped: checks, review, document" in summary
    assert "the stopping stage's own changes are in the tree, uncommitted" in summary
    assert result.left_uncommitted
    assert "NOT ACCEPTED — cost cap reached: $0.4100 of $0.25 budget" in summary


def test_the_breach_is_on_the_record_as_its_own_gate(git_repo: Path, fake_cli: FakeCLI):
    result, telemetry = run(
        git_repo,
        fake_cli,
        [priced(PLAN_OK, 0.01), priced(BUILD_OK, 0.40), REVIEW_OK, DOCUMENT_OK],
        max_cost_usd=0.25,
    )
    assert not result.accepted

    stated = budget_events(telemetry)
    assert len(stated) == 1
    assert stated[0]["event"] == "gate_fail"
    assert stated[0]["phase"] == "build"
    assert stated[0]["detail"] == "cost cap reached: $0.4100 of $0.25 budget"
    assert stated[0]["payload"]["cost_usd"] == 0.41

    end = [e for e in events(telemetry) if e["event"] == "run_end"][0]
    assert end["result"] == "fail"
    assert end["detail"] == "cost cap reached: $0.4100 of $0.25 budget"
    assert end["stats"]["accepted"] is False


def test_the_gate_block_reaches_the_collector(git_repo: Path, fake_cli: FakeCLI, post_spy):
    result, _ = run(
        git_repo,
        fake_cli,
        [priced(PLAN_OK, 0.01), priced(BUILD_OK, 0.40), REVIEW_OK, DOCUMENT_OK],
        max_cost_usd=0.25,
        telemetry_url="http://localhost:8008/api/v1/hooks/events",
    )
    assert not result.accepted

    blocks = [b["gate"] for b in post_spy.bodies if b.get("gate", {}).get("name") == "budget"]
    assert blocks == [
        {
            "name": "budget",
            "attempt": 1,
            "ok": False,
            "note": "cost cap reached: $0.4100 of $0.25 budget",
        }
    ]


# --- a single runaway turn ---------------------------------------------------


def test_one_runaway_turn_is_caught_without_a_second_call(git_repo: Path, fake_cli: FakeCLI):
    """The case a between-stages check would miss entirely."""
    result, _ = run(git_repo, fake_cli, [priced(PLAN_OK, 99.0), BUILD_OK], max_cost_usd=0.25)

    assert not result.accepted
    assert result.reason == "cost cap reached: $99.0000 of $0.25 budget"
    assert len(fake_cli.calls) == 1
    assert subjects(git_repo) == ["initial"]  # nothing had been committed yet
    assert "kept: nothing had been committed yet" in format_summary(result)


def test_the_cap_is_read_after_a_correction_turn_too(git_repo: Path, fake_cli: FakeCLI):
    """Corrections are agent turns like any other, and they are billed like any other."""
    cheap_but_wrong = {"session_id": "plan-session", "raw_reply": "Done!", "cost_usd": 0.01}
    result, _ = run(
        git_repo,
        fake_cli,
        [cheap_but_wrong, priced(PLAN_OK, 0.50), BUILD_OK],
        max_cost_usd=0.25,
    )

    assert result.reason == "cost cap reached: $0.5100 of $0.25 budget"
    assert len(fake_cli.calls) == 2  # the correction turn breached; no third call
    assert result.outcomes[0].status == "blocked"


# --- tokens ------------------------------------------------------------------


def test_a_token_cap_stops_the_run_with_the_count_and_the_cap(git_repo: Path, fake_cli: FakeCLI):
    # The fake CLI reports 1000 in + 200 out per turn, so the second turn crosses 2000.
    result, _ = run(git_repo, fake_cli, SCRIPT, max_tokens=2000)

    assert not result.accepted
    assert result.reason == "token cap reached: 2,400 of 2,000 token budget"
    assert result.tokens == 2400
    assert [o.name for o in result.outcomes] == ["plan", "build"]
    assert subjects(git_repo) == ["plan: Plan the health endpoint", "initial"]
    assert "STOPPED ON BUDGET — token cap reached: 2,400 of 2,000 token budget" in format_summary(
        result
    )


def test_both_caps_may_be_set_and_the_one_that_trips_is_named(git_repo: Path, fake_cli: FakeCLI):
    result, _ = run(git_repo, fake_cli, SCRIPT, max_cost_usd=100.0, max_tokens=2000)

    assert result.reason.startswith("token cap reached")
    assert "cost cap" not in result.reason


# --- absent caps change nothing ---------------------------------------------


def test_no_caps_is_todays_run_exactly(git_repo: Path, fake_cli: FakeCLI):
    result, telemetry = run(git_repo, fake_cli, SCRIPT)

    assert result.accepted, result.reason
    assert result.budget_stop == ""
    assert budget_report(result) == []
    assert "STOPPED ON BUDGET" not in format_summary(result)
    assert budget_events(telemetry) == []
    # Nothing was enforced, but the run still measured what a cap would have read.
    assert result.tokens > 0 and result.cost_usd > 0


def test_a_run_that_stays_inside_its_caps_is_accepted_normally(git_repo: Path, fake_cli: FakeCLI):
    result, telemetry = run(git_repo, fake_cli, SCRIPT, max_cost_usd=100.0, max_tokens=10_000_000)

    assert result.accepted, result.reason
    assert result.budget_stop == ""
    assert budget_events(telemetry) == []
    assert subjects(git_repo)[0] == "document: Document the endpoint"


# --- configuration ------------------------------------------------------------


def test_caps_default_to_off(tmp_path: Path):
    cfg = load_config(tmp_path)
    assert cfg.max_cost_usd is None
    assert cfg.max_tokens is None
    assert cfg.budget_text == "(uncapped)"


def test_caps_come_from_the_config_file_and_the_flags(tmp_path: Path):
    (tmp_path / "factory.config.json").write_text(
        '{"max_cost_usd": 2.5, "max_tokens": 900000}', encoding="utf-8"
    )
    assert load_config(tmp_path).max_cost_usd == 2.5
    assert load_config(tmp_path).max_tokens == 900_000
    assert load_config(tmp_path).budget_text == "$2.5 and 900,000 tokens"
    # A flag beats the file, as everywhere else.
    assert load_config(tmp_path, max_cost_usd=0.25).max_cost_usd == 0.25


@pytest.mark.parametrize(
    "data",
    [
        {"max_cost_usd": 0},
        {"max_cost_usd": -1},
        {"max_cost_usd": "cheap"},
        {"max_cost_usd": True},
        {"max_tokens": 0},
        {"max_tokens": 1.5},
        {"max_tokens": "lots"},
    ],
)
def test_a_cap_that_cannot_be_met_is_refused_at_startup(tmp_path: Path, data: dict):
    (tmp_path / "factory.config.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(tmp_path)


def test_the_dry_run_prints_the_budget(git_repo: Path, capsys):
    (git_repo / "pyproject.toml").write_text("[project]\nname='x'\n")

    assert cli.main(["--repo", str(git_repo), "--dry-run", "x"]) == 0
    assert "budget:                (uncapped)" in capsys.readouterr().out

    args = ["--max-cost-usd", "0.25", "--max-tokens", "500000"]
    assert cli.main(["--repo", str(git_repo), "--dry-run", *args, "x"]) == 0
    assert "budget:                $0.25 and 500,000 tokens" in capsys.readouterr().out
