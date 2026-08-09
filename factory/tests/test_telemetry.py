"""Telemetry: durable JSONL, fail-silent POST, context accounting, the v1.13 body."""

from __future__ import annotations

import json
from pathlib import Path

from adw.telemetry import (
    AGENT_COLORS,
    DEFAULT_AGENT_COLOR,
    GIT_AGENT,
    MAX_POST_FAILURES,
    Telemetry,
    agent_color,
    context_window_for,
)
from conftest import PostSpy

# Port 9 (discard) refuses instantly — a stand-in for "the collector is down".
DEAD_URL = "http://127.0.0.1:9/api/v1/hooks/events"
COLLECTOR = "http://localhost:8008/api/v1/hooks/events"
REQUEST = "Add a /health endpoint"

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


def make(tmp_path: Path, url: str | None = None) -> Telemetry:
    return Telemetry(
        run_id="abc12345", repo=tmp_path, run_dir=tmp_path / "factory/runs/abc12345", url=url
    )


def read(tel: Telemetry) -> list[dict]:
    return [json.loads(line) for line in tel.path.read_text().splitlines() if line.strip()]


def test_every_event_lands_in_the_jsonl(tmp_path: Path):
    tel = make(tmp_path)
    tel.emit("phase_start", phase="build", agent="build")
    tel.emit("tool_call", phase="build", tool_name="Write", detail="app.py")
    tel.emit("gate_fail", phase="build", result="fail", detail="changed_files: mismatch")
    tel.close()

    records = read(tel)
    assert [r["event"] for r in records] == ["phase_start", "tool_call", "gate_fail"]
    assert records[1]["tool_name"] == "Write"
    assert records[2]["result"] == "fail"
    for record in records:
        assert set(record) >= JSONL_KEYS
        assert record["run"] == "abc12345"


def test_run_dir_is_created_and_self_ignoring(tmp_path: Path):
    tel = make(tmp_path)
    tel.close()
    assert tel.path.is_file()
    assert (tmp_path / "factory" / "runs" / ".gitignore").read_text() == "*\n"


def test_context_pct_grows_with_cumulative_input(tmp_path: Path):
    tel = make(tmp_path)
    tel.context_window = 1000
    assert tel.note_input_tokens(250) == 25.0
    assert tel.note_input_tokens(250) == 50.0
    tel.close()


def test_a_dead_collector_never_breaks_a_run(tmp_path: Path):
    tel = make(tmp_path, url=DEAD_URL)
    for i in range(MAX_POST_FAILURES + 3):
        tel.emit("agent_turn", phase="build", detail=f"turn {i}")
    tel.close()

    assert len(read(tel)) == MAX_POST_FAILURES + 3  # the JSONL is unaffected
    assert tel._post_failures == MAX_POST_FAILURES  # and posting gave up


def test_posts_carry_the_masterwork_hook_shape(tmp_path: Path, post_spy: PostSpy):
    tel = make(tmp_path, url=COLLECTOR)
    tel.emit("tool_call", phase="build", model="sonnet", tool_name="Edit", detail="app.py")
    tel.emit("run_end", phase="run", detail="done", ended=True, stats={"cost_usd": 1.5})
    tel.close()

    assert post_spy.requests[0] == {"url": COLLECTOR, "method": "POST"}
    first = post_spy.bodies[0]
    assert first["session_id"] == "factory-abc12345"
    assert first["event_type"] == "tool_call"
    assert first["cwd"] == str(tmp_path)
    assert first["model"] == "sonnet"
    assert first["tool_name"] == "Edit"
    assert first["payload"]["phase"] == "build"
    last = post_spy.bodies[1]
    assert last["ended"] is True
    assert last["stats"] == {"cost_usd": 1.5}


def test_no_url_means_no_post(tmp_path: Path, monkeypatch):
    def explode(*args, **kwargs):
        raise AssertionError("telemetry must not POST when no url is configured")

    monkeypatch.setattr("adw.telemetry.urllib.request.urlopen", explode)
    tel = make(tmp_path, url=None)
    tel.emit("run_end", phase="run")
    tel.close()
    assert len(read(tel)) == 1


# --- v1.13 first-class POST fields -----------------------------------------


def test_run_start_posts_title_workflow_and_running_status(tmp_path: Path, post_spy: PostSpy):
    tel = make(tmp_path, url=COLLECTOR)
    tel.emit("phase_start", phase="run", detail="Add a /health endpoint", title=REQUEST)
    tel.close()

    body = post_spy.bodies[0]
    assert body["title"] == REQUEST
    assert body["workflow"] == "factory"
    assert body["status"] == "running"
    assert "phase" not in body  # the run is the session, never a phase row


def test_a_long_title_is_truncated(tmp_path: Path, post_spy: PostSpy):
    tel = make(tmp_path, url=COLLECTOR)
    tel.emit("phase_start", phase="run", title="x" * 900)
    tel.close()
    assert post_spy.bodies[0]["title"] == "x" * 300


def test_phase_events_post_a_first_class_phase(tmp_path: Path, post_spy: PostSpy):
    tel = make(tmp_path, url=COLLECTOR)
    tel.emit("phase_start", phase="build", agent="build", model="sonnet", detail="(unrestricted)")
    tel.emit(
        "agent_turn",
        phase="build",
        agent="build",
        model="sonnet",
        tokens_in=1200,
        tokens_out=300,
        cost_usd=0.02,
    )
    tel.emit(
        "phase_end",
        phase="build",
        agent="build",
        model="sonnet",
        duration_ms=4200,
        cost_usd=0.02,
        detail="Add the health route",
        payload={"corrections": 1, "commit": "abc1234"},
    )
    tel.close()

    assert post_spy.of("phase_start")[0]["phase"] == {
        "name": "build",
        "seq": 1,
        "kind": "agent",
        "status": "running",
        "agent": "build",
        "description": "(unrestricted)",
    }
    assert post_spy.of("phase_end")[0]["phase"] == {
        "name": "build",
        "seq": 1,  # the same row the start opened
        "kind": "agent",
        "status": "passed",
        "duration_ms": 4200,
        "cost_usd": 0.02,
        "tokens_in": 1200,  # harvested from the turns inside the phase
        "tokens_out": 300,
        "corrections": 1,
        "commit_sha": "abc1234",
        "agent": "build",
        "description": "Add the health route",
    }
    # In-phase events carry the key so the backend can link them to that row.
    assert post_spy.of("agent_turn")[0]["phase"] == {"name": "build", "seq": 1}


def test_phase_kind_follows_what_did_the_work(tmp_path: Path, post_spy: PostSpy):
    tel = make(tmp_path, url=COLLECTOR)
    for name in ("plan", "checks", "review", "document"):
        tel.emit("phase_start", phase=name, agent=name)
    tel.emit(
        "commit", phase="build", agent="build", detail="build: add it", payload={"sha": "deadbee"}
    )
    tel.close()

    kinds = {body["phase"]["name"]: body["phase"]["kind"] for body in post_spy.bodies}
    assert kinds == {
        "plan": "agent",
        "checks": "code",
        "review": "agent",
        "document": "agent",
        "commit:build": "git",
    }
    commit = post_spy.of("commit")[0]["phase"]
    assert commit["commit_sha"] == "deadbee"
    assert commit["status"] == "passed"
    # The committing is git's work, not the builder's — a lane of its own.
    assert commit["agent"] == GIT_AGENT
    assert post_spy.of("commit")[0]["agent"] == {"name": GIT_AGENT, "color": AGENT_COLORS[GIT_AGENT]}


def test_every_commit_is_its_own_row_with_its_own_sha(tmp_path: Path, post_spy: PostSpy):
    """One shared `commit` name merged four commits into one row downstream."""
    tel = make(tmp_path, url=COLLECTOR)
    for stage, sha in (("plan", "aaa1111"), ("build", "bbb2222"), ("document", "ccc3333")):
        tel.emit("commit", phase=stage, agent=stage, detail=f"{stage}: done", payload={"sha": sha})
    tel.close()

    commits = [b["phase"] for b in post_spy.of("commit")]
    assert [p["name"] for p in commits] == ["commit:plan", "commit:build", "commit:document"]
    assert [p["commit_sha"] for p in commits] == ["aaa1111", "bbb2222", "ccc3333"]
    assert [p["seq"] for p in commits] == [1, 2, 3]
    assert [p["description"] for p in commits] == ["plan: done", "build: done", "document: done"]


def test_a_stage_that_commits_twice_gets_two_rows(tmp_path: Path, post_spy: PostSpy):
    """A correction sends the builder round again — and commits again."""
    tel = make(tmp_path, url=COLLECTOR)
    for sha in ("aaa1111", "bbb2222", "ccc3333"):
        tel.emit("commit", phase="build", agent="build", payload={"sha": sha})
    tel.close()

    commits = [b["phase"] for b in post_spy.of("commit")]
    assert [p["name"] for p in commits] == ["commit:build", "commit:build#2", "commit:build#3"]
    assert len({p["name"] for p in commits}) == 3


def test_a_failed_phase_end_posts_status_failed(tmp_path: Path, post_spy: PostSpy):
    tel = make(tmp_path, url=COLLECTOR)
    tel.emit("phase_start", phase="checks")
    tel.emit("phase_end", phase="checks", result="fail", detail="pytest exited 1")
    tel.close()

    body = post_spy.of("phase_end")[0]
    assert body["phase"]["status"] == "failed"
    assert body["ok"] is False


def test_a_nested_correction_keeps_its_own_seq(tmp_path: Path, post_spy: PostSpy):
    """A build correction runs inside the checks phase — the rows must not collide."""
    tel = make(tmp_path, url=COLLECTOR)
    tel.emit("phase_start", phase="checks")
    tel.emit("phase_start", phase="build", agent="build")
    tel.emit("phase_end", phase="build", agent="build")
    tel.emit("phase_end", phase="checks")
    tel.close()

    assert [b["phase"]["seq"] for b in post_spy.bodies] == [1, 2, 2, 1]


def test_agent_turns_post_the_lane_with_colour_and_context(tmp_path: Path, post_spy: PostSpy):
    tel = make(tmp_path, url=COLLECTOR)
    tel.emit("phase_start", phase="plan", agent="plan", model="opus")
    tel.emit(
        "agent_turn",
        phase="plan",
        agent="plan",
        model="opus",
        tokens_in=9000,
        tokens_out=800,
        cost_usd=0.5,
        context_tokens=48_000,
    )
    tel.emit(
        "agent_turn",
        phase="plan",
        agent="plan",
        model="opus",
        tokens_in=11_000,
        tokens_out=200,
        cost_usd=0.25,
        context_tokens=61_000,
    )
    tel.close()

    assert post_spy.of("phase_start")[0]["agent"] == {
        "name": "plan",
        "color": AGENT_COLORS["plan"],
        "context_window": 200_000,
        "model": "opus",
    }
    assert post_spy.of("agent_turn")[-1]["agent"] == {
        "name": "plan",
        "color": AGENT_COLORS["plan"],
        "context_window": 200_000,
        "model": "opus",
        "cost_usd": 0.75,  # cumulative: the server merges, it does not add
        "tokens_in": 20_000,
        "tokens_out": 1000,
        "context_tokens": 61_000,  # the last turn's prompt, not the sum
    }


def test_a_turn_without_a_context_reading_falls_back_to_input(tmp_path: Path, post_spy: PostSpy):
    tel = make(tmp_path, url=COLLECTOR)
    tel.emit("agent_turn", phase="build", agent="build", model="sonnet", tokens_in=1234)
    tel.close()
    assert post_spy.bodies[0]["agent"]["context_tokens"] == 1234


def test_tool_calls_post_the_lane_the_verdict_and_the_duration(tmp_path: Path, post_spy: PostSpy):
    tel = make(tmp_path, url=COLLECTOR)
    tel.emit(
        "tool_call",
        phase="build",
        agent="build",
        model="sonnet",
        tool_name="Write",
        payload={"kind": "use", "name": "Write"},
    )
    tel.emit(
        "tool_call",
        phase="build",
        agent="build",
        model="sonnet",
        tool_name="tool_result",
        payload={"kind": "result", "name": "tool_result", "is_error": True},
        ok=False,
        tool_duration_ms=812,
    )
    tel.close()

    use, result = post_spy.of("tool_call")
    assert use["tool_name"] == "Write"
    assert use["agent"]["name"] == "build"
    assert use["agent"]["color"] == AGENT_COLORS["build"]
    assert "ok" not in use  # a tool_use has no verdict yet
    assert result["ok"] is False
    assert result["duration_ms"] == 812
    assert result["tool_name"] == "tool_result"


def test_run_end_posts_the_final_status(tmp_path: Path, post_spy: PostSpy):
    tel = make(tmp_path, url=COLLECTOR)
    tel.emit("phase_start", phase="run", title=REQUEST)
    tel.emit(
        "run_end",
        phase="run",
        result="fail",
        detail="review did not approve",
        ended=True,
        stats={"accepted": False, "turns": 4},
    )
    tel.close()

    body = post_spy.of("run_end")[0]
    assert body["status"] == "failed"
    assert body["ended"] is True
    assert body["ok"] is False
    assert body["title"] == REQUEST
    assert body["workflow"] == "factory"
    assert body["stats"] == {"accepted": False, "turns": 4}
    assert "phase" not in body


def test_an_accepted_run_ends_successful(tmp_path: Path, post_spy: PostSpy):
    tel = make(tmp_path, url=COLLECTOR)
    tel.emit("run_end", phase="run", detail="all stages passed", ended=True)
    tel.close()
    assert post_spy.bodies[0]["status"] == "success"


def test_the_new_post_fields_never_reach_the_jsonl(tmp_path: Path, post_spy: PostSpy):
    tel = make(tmp_path, url=COLLECTOR)
    tel.emit(
        "phase_end",
        phase="build",
        agent="build",
        model="sonnet",
        duration_ms=10,
        detail="done",
        payload={"corrections": 1, "commit": "abc1234"},
        title="a title",
        context_tokens=50_000,
        ok=False,
        tool_duration_ms=7,
    )
    tel.close()

    record = read(tel)[0]
    assert set(record) == JSONL_KEYS | {"payload"}
    assert record["payload"] == {"corrections": 1, "commit": "abc1234"}
    assert record["duration_ms"] == 10  # not the tool measurement
    # The POST still nests the untouched record under `payload`.
    body = post_spy.bodies[0]
    assert body["payload"] == {k: v for k, v in record.items() if k not in ("ts", "run")}


def test_the_context_window_map_is_a_display_aid(tmp_path: Path):
    assert context_window_for("opus") == 200_000
    assert context_window_for("claude-sonnet-4-5-20250929") == 200_000
    assert context_window_for("sonnet[1m]") == 1_000_000
    assert context_window_for(None) == 200_000
    assert context_window_for("some-future-model", default=64_000) == 64_000


def test_every_stage_has_its_own_stable_swatch():
    stages = ("plan", "build", "checks", "review", "document", GIT_AGENT)
    colors = [agent_color(name) for name in stages]
    assert len(set(colors)) == len(stages)
    assert colors == [agent_color(name) for name in stages]  # stable across calls
    assert agent_color("some-subagent") == DEFAULT_AGENT_COLOR
