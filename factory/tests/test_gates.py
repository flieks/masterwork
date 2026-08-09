"""The six gates, each in isolation."""

from __future__ import annotations

import json
from pathlib import Path

from adw.envelopes import parse_envelope
from adw.gates import (
    gate_artifacts,
    gate_boundary,
    gate_changed_files,
    gate_checks,
    gate_envelope,
    gate_verdict,
    run_checks,
)
from conftest import envelope


def parsed(payload: dict, stage: str = "build"):
    return parse_envelope("```json\n" + json.dumps(payload) + "\n```", stage)


# --- gate 1 ---------------------------------------------------------------


def test_gate_envelope_passes_and_fails():
    assert gate_envelope(parsed(envelope(changed_files=[])), "build").ok
    bad = parse_envelope("no envelope here", "build")
    check = gate_envelope(bad, "build")
    assert not check.ok
    assert "fenced ```json" in check.note


# --- gate 2 ---------------------------------------------------------------


def test_gate_artifacts_requires_existing_non_empty_files(tmp_path: Path):
    (tmp_path / "plan.md").write_text("a plan")
    (tmp_path / "empty.md").write_text("")
    env = parsed(envelope(artifacts=["plan.md"], changed_files=[])).envelope
    assert gate_artifacts(tmp_path, env).ok

    env = parsed(envelope(artifacts=["empty.md"], changed_files=[])).envelope
    assert "empty" in gate_artifacts(tmp_path, env).note

    env = parsed(envelope(artifacts=["missing.md"], changed_files=[])).envelope
    check = gate_artifacts(tmp_path, env)
    assert not check.ok
    assert "does not exist" in check.note


# --- gate 3 ---------------------------------------------------------------


def test_changed_files_must_match_exactly_in_both_directions():
    assert gate_changed_files(["a.py", "b.py"], ["b.py", "a.py"]).ok

    invented = gate_changed_files(["a.py"], ["a.py", "ghost.py"])
    assert not invented.ok
    assert "claimed but not changed on disk: ghost.py" in invented.note

    undeclared = gate_changed_files(["a.py", "sneaky.py"], ["a.py"])
    assert not undeclared.ok
    assert "changed on disk but not declared: sneaky.py" in undeclared.note

    both = gate_changed_files(["a.py"], ["b.py"])
    assert "claimed but not changed" in both.note
    assert "not declared" in both.note


def test_changed_files_normalizes_claimed_paths():
    assert gate_changed_files(["app/main.py"], ["./app/main.py"]).ok


# --- gate 4 ---------------------------------------------------------------


def test_boundary_none_is_unrestricted():
    result = gate_boundary(["anything/at/all.py"], None)
    assert result.check.ok
    assert result.offending == []


def test_boundary_empty_means_read_only():
    result = gate_boundary(["notes.md"], [])
    assert not result.check.ok
    assert result.offending == ["notes.md"]
    assert "read-only" in result.check.note


def test_boundary_reports_only_the_offending_paths():
    result = gate_boundary(
        ["plan.md", "app/main.py", "docs/specs/x.md"], ["plan.md", "docs/specs/**"]
    )
    assert result.offending == ["app/main.py"]
    assert "REVERTED" in result.check.note


# --- gate 5 ---------------------------------------------------------------


def test_verdict_consistency():
    approved_clean = parsed(envelope(approved=True, blocking=[]), "review").envelope
    assert gate_verdict(approved_clean).ok

    rejected_with_reason = parsed(
        envelope(approved=False, blocking=["no tests"]), "review"
    ).envelope
    assert gate_verdict(rejected_with_reason).ok

    contradiction = parsed(envelope(approved=True, blocking=["no tests"]), "review").envelope
    assert not gate_verdict(contradiction).ok

    empty_rejection = parsed(envelope(approved=False, blocking=[]), "review").envelope
    check = gate_verdict(empty_rejection)
    assert not check.ok
    assert "empty blocking list" in check.note


def test_verdict_allows_a_blocked_stage_without_findings():
    blocked = parsed(
        envelope(status="blocked", summary="Needs a destructive migration.", approved=False),
        "review",
    ).envelope
    assert gate_verdict(blocked).ok


def test_verdict_requires_the_approved_field():
    missing = parsed({"status": "ok", "summary": "s", "blocking": []}, "review")
    assert not missing.ok  # caught by the envelope gate first


# --- gate 6 ---------------------------------------------------------------


def test_run_checks_records_exit_codes(tmp_path: Path):
    runs = run_checks(
        tmp_path,
        ["python3 -c pass", 'python3 -c "import sys; sys.exit(3)"', "definitely-not-a-command"],
        timeout=30,
    )
    assert [r.exit_code for r in runs] == [0, 3, 127]
    check = gate_checks(runs)
    assert not check.ok
    assert "exited 3" in check.note
    assert "definitely-not-a-command" in check.note


def test_checks_run_in_the_repo_directory(tmp_path: Path):
    (tmp_path / "marker.txt").write_text("here")
    command = (
        'python3 -c "import pathlib, sys; '
        "sys.exit(0 if pathlib.Path('marker.txt').exists() else 1)\""
    )
    runs = run_checks(tmp_path, [command], timeout=30)
    assert runs[0].ok


def test_no_checks_passes_but_says_nothing_was_verified():
    check = gate_checks([])
    assert check.ok
    assert "nothing was verified" in check.note
