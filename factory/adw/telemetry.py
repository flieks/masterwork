"""Run telemetry: durable JSONL on disk, plus a fail-silent POST to masterwork.

The JSONL line is the local record of truth and keeps its shape forever. The POST
body carries the same line under `payload` *plus* the first-class fields of the
masterwork v1.13 hook contract (title/status/phase/agent), which the Sessions UI
needs to draw run cards, per-agent lanes and context bars without unpacking JSON,
and the v1.19 evidence blocks (`envelope`/`gate`), which carry the sentence a gate
wrote and the envelope an agent actually returned.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

POST_TIMEOUT_SECONDS = 2.0
# Stop posting after this many consecutive failures — a dead collector must never
# add seconds to an unattended run.
MAX_POST_FAILURES = 3

RUN_PHASE = "run"
COMMIT_PHASE = "commit"
# Committing is deterministic work the runner does, so it gets a lane of its own
# rather than being charged to whichever stage happened to produce the tree.
GIT_AGENT = "git"
DEFAULT_WORKFLOW = "factory"
DEFAULT_CONTEXT_WINDOW = 200_000
MAX_TITLE_CHARS = 300
MAX_DESCRIPTION_CHARS = 500

# Display aid for the per-agent context bar, NOT a hard limit — the CLI owns the
# real window. Matched as a substring of the model name, longest key first.
MODEL_CONTEXT_WINDOWS = {
    "sonnet[1m]": 1_000_000,
    "sonnet": 200_000,
    "opus": 200_000,
    "haiku": 200_000,
}

# One fixed swatch per lane so a stage keeps its colour across runs (SSSF convention).
AGENT_COLORS = {
    "plan": "#6aa9ff",
    "build": "#3ecf8e",
    "checks": "#f2b53b",
    "review": "#ff6b81",
    "document": "#b48cff",
    GIT_AGENT: "#22c1dc",
}
DEFAULT_AGENT_COLOR = "#8a94a6"

# What actually did the work in a phase: an agent, the runner's own code, or git.
PHASE_KINDS = {
    "plan": "agent",
    "build": "agent",
    "review": "agent",
    "document": "agent",
    "checks": "code",
    COMMIT_PHASE: "git",
}
DEFAULT_PHASE_KIND = "agent"

# Events whose `result` says something true about success.
OK_EVENTS = frozenset({"phase_end", "run_end", "gate_pass", "gate_fail", "agent_turn"})
PHASE_EVENTS = frozenset({"phase_start", "phase_end"})


def context_window_for(model: str | None, default: int = DEFAULT_CONTEXT_WINDOW) -> int:
    """The window a context percentage is drawn against — display only."""
    text = (model or "").lower()
    for key in sorted(MODEL_CONTEXT_WINDOWS, key=len, reverse=True):
        if key in text:
            return MODEL_CONTEXT_WINDOWS[key]
    return default


def agent_color(name: str) -> str:
    return AGENT_COLORS.get(name, DEFAULT_AGENT_COLOR)


def phase_kind(name: str) -> str:
    return PHASE_KINDS.get(name, DEFAULT_PHASE_KIND)


def commit_phase_name(stage: str, occurrence: int = 1) -> str:
    """`commit:build`, then `commit:build#2` — a phase name is a row identity
    downstream, so two commits must never answer to the same one."""
    base = f"{COMMIT_PHASE}:{stage}" if stage else COMMIT_PHASE
    return base if occurrence <= 1 else f"{base}#{occurrence}"


def _event_ok(event_type: str, result: str) -> bool | None:
    return (result != "fail") if event_type in OK_EVENTS else None


@dataclass
class Telemetry:
    """One JSONL line and one best-effort POST per pipeline event."""

    run_id: str
    repo: Path
    run_dir: Path
    url: str | None = None
    context_window: int = DEFAULT_CONTEXT_WINDOW
    echo: bool = False
    workflow: str = DEFAULT_WORKFLOW
    title: str = ""
    # A resumed run appends to the SAME session, so its phases must not re-use the
    # sequence numbers the interrupted process already reported under that session.
    seq_start: int = 1
    _handle: Any = field(default=None, init=False, repr=False)
    _post_failures: int = field(default=0, init=False, repr=False)
    _cumulative_input: int = field(default=0, init=False, repr=False)
    # v1.13 bookkeeping: phase sequence + per-phase and per-lane running totals.
    _next_seq: int = field(default=1, init=False, repr=False)
    _open_phases: list[tuple[str, int]] = field(default_factory=list, init=False, repr=False)
    _phase_totals: dict[int, dict[str, float]] = field(default_factory=dict, init=False, repr=False)
    _lanes: dict[str, dict[str, Any]] = field(default_factory=dict, init=False, repr=False)
    _commits: dict[str, int] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        # The run dir lives outside the target repo by default, so the runner
        # writes nothing — not even a .gitignore — into someone else's tree.
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._next_seq = max(1, int(self.seq_start))
        self._handle = (self.run_dir / "telemetry.jsonl").open("a", encoding="utf-8")

    @property
    def path(self) -> Path:
        return self.run_dir / "telemetry.jsonl"

    @property
    def session_id(self) -> str:
        return f"factory-{self.run_id}"

    def note_input_tokens(self, tokens: int) -> float:
        """Track context growth; returns the resulting context percentage."""
        self._cumulative_input += max(tokens, 0)
        return round(100.0 * self._cumulative_input / self.context_window, 2)

    def emit(
        self,
        event_type: str,
        *,
        phase: str = "",
        agent: str = "",
        model: str | None = None,
        tool_name: str | None = None,
        result: str = "ok",
        detail: str = "",
        duration_ms: int = 0,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_usd: float = 0.0,
        context_pct: float = 0.0,
        payload: dict[str, Any] | None = None,
        ended: bool = False,
        stats: dict[str, Any] | None = None,
        # --- v1.13 POST-only inputs: these never enter the JSONL record ---
        title: str | None = None,
        context_tokens: int = 0,
        ok: bool | None = None,
        tool_duration_ms: int | None = None,
        # --- v1.19 evidence blocks, POST-only for the same reason ---
        envelope: dict[str, Any] | None = None,
        gate: dict[str, Any] | None = None,
    ) -> None:
        if title:
            self.title = title.strip()[:MAX_TITLE_CHARS]
        record = {
            "ts": datetime.now(UTC).isoformat(),
            "run": self.run_id,
            "phase": phase,
            "event": event_type,
            "agent": agent,
            "duration_ms": duration_ms,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": round(cost_usd, 6),
            "context_pct": context_pct,
            "result": result,
            "detail": detail,
        }
        if tool_name:
            record["tool_name"] = tool_name
        if payload:
            record["payload"] = payload
        if stats:
            record["stats"] = stats
        self._write(record)
        self._post(
            event_type,
            record,
            model=model,
            tool_name=tool_name,
            ended=ended,
            stats=stats,
            context_tokens=context_tokens,
            ok=ok,
            tool_duration_ms=tool_duration_ms,
            envelope=envelope,
            gate=gate,
        )

    def _write(self, record: dict[str, Any]) -> None:
        self._handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        self._handle.flush()
        if self.echo:
            print(f"  · {record['event']:<12} {record['phase']:<9} {record['detail'][:90]}")

    def _post(
        self,
        event_type: str,
        record: dict[str, Any],
        *,
        model: str | None,
        tool_name: str | None,
        ended: bool,
        stats: dict[str, Any] | None,
        context_tokens: int = 0,
        ok: bool | None = None,
        tool_duration_ms: int | None = None,
        envelope: dict[str, Any] | None = None,
        gate: dict[str, Any] | None = None,
    ) -> None:
        if not self.url or self._post_failures >= MAX_POST_FAILURES:
            return
        body: dict[str, Any] = {
            "session_id": self.session_id,
            "event_type": event_type,
            "cwd": str(self.repo),
            "model": model,
            "tool_name": tool_name,
            "payload": {k: v for k, v in record.items() if k not in ("ts", "run")},
        }
        if ended:
            body["ended"] = True
        if stats:
            body["stats"] = stats
        # An unusable block is dropped whole by the server, never 422'd — so stating
        # one is always safe. Stating one also stops the event being mined for the
        # same verdict, which is why every block below carries the whole of it.
        if envelope:
            body["envelope"] = envelope
        if gate:
            body["gate"] = gate
        body.update(
            self._first_class(
                event_type,
                record,
                model=model,
                context_tokens=context_tokens,
                ok=ok,
                tool_duration_ms=tool_duration_ms,
            )
        )
        request = urllib.request.Request(
            self.url,
            data=json.dumps(body, default=str).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=POST_TIMEOUT_SECONDS):
                self._post_failures = 0
        except Exception:  # telemetry must never break a run
            self._post_failures += 1

    # --- v1.13 first-class fields ------------------------------------------

    def _first_class(
        self,
        event_type: str,
        record: dict[str, Any],
        *,
        model: str | None,
        context_tokens: int,
        ok: bool | None,
        tool_duration_ms: int | None,
    ) -> dict[str, Any]:
        """Promote phase/agent/status out of `payload` and onto the request body."""
        if event_type == "agent_turn":
            self._accumulate(record)
        fields: dict[str, Any] = {}
        if self._is_run_scoped(event_type, record):
            fields.update(self._run_fields(event_type, record))
        else:
            phase = self._phase_fields(event_type, record)
            if phase:
                fields["phase"] = phase
        lane = self._lane_fields(event_type, record, model=model, context_tokens=context_tokens)
        if lane:
            fields["agent"] = lane
        if tool_duration_ms is not None:  # a measured tool call, 0 ms included
            fields["duration_ms"] = max(tool_duration_ms, 0)
        elif record["duration_ms"]:
            fields["duration_ms"] = int(record["duration_ms"])
        resolved = ok if ok is not None else _event_ok(event_type, str(record["result"]))
        if resolved is not None:
            fields["ok"] = resolved
        return fields

    @staticmethod
    def _is_run_scoped(event_type: str, record: dict[str, Any]) -> bool:
        return event_type == "run_end" or (
            event_type in PHASE_EVENTS and record["phase"] == RUN_PHASE
        )

    def _run_fields(self, event_type: str, record: dict[str, Any]) -> dict[str, Any]:
        fields: dict[str, Any] = {"workflow": self.workflow}
        if self.title:
            fields["title"] = self.title
        if event_type == "run_end":
            fields["status"] = "failed" if record["result"] == "fail" else "success"
        else:
            fields["status"] = "running"
        return fields

    def _phase_fields(self, event_type: str, record: dict[str, Any]) -> dict[str, Any]:
        name = str(record["phase"])
        if not name:
            return {}
        if event_type == "commit":
            phase = self._commit_phase(record)
        elif event_type in PHASE_EVENTS:
            phase = self._stage_phase(event_type, name, record)
        else:
            # Any other in-phase event: just the key, so the backend can link it.
            seq = self._current_seq(name)
            return {"name": name, "seq": seq} if seq else {}
        # A commit names its own lane; everything else runs in its stage's.
        if record["agent"] and not phase.get("agent"):
            phase["agent"] = record["agent"]
        if record["detail"]:
            phase["description"] = str(record["detail"])[:MAX_DESCRIPTION_CHARS]
        return phase

    def _stage_phase(self, event_type: str, name: str, record: dict[str, Any]) -> dict[str, Any]:
        if event_type == "phase_start":
            seq = self._open_phase(name)
            closing: dict[str, Any] = {"status": "running"}
        else:
            seq = self._close_phase(name)
            closing = {"status": "failed" if record["result"] == "fail" else "passed"}
            closing.update(self._closing_totals(seq, record))
        return {"name": name, "seq": seq, "kind": phase_kind(name), **closing}

    def _commit_phase(self, record: dict[str, Any]) -> dict[str, Any]:
        """A commit is its own zero-length `git` phase between the agent phases,
        named after the stage it sealed so each one keeps its own row and sha."""
        payload = record.get("payload") or {}
        stage = str(record["agent"] or record["phase"])
        self._commits[stage] = self._commits.get(stage, 0) + 1
        phase: dict[str, Any] = {
            "name": commit_phase_name(stage, self._commits[stage]),
            "seq": self._take_seq(),
            "kind": phase_kind(COMMIT_PHASE),
            "status": "passed",
            "agent": GIT_AGENT,
        }
        if payload.get("sha"):
            phase["commit_sha"] = str(payload["sha"])
        return phase

    def _closing_totals(self, seq: int, record: dict[str, Any]) -> dict[str, Any]:
        # corrections/commit already ride the phase_end payload — promote them
        # rather than plumbing the same two values through the pipeline twice.
        payload = record.get("payload") or {}
        totals = self._phase_totals.pop(seq, {})
        out: dict[str, Any] = {}
        if record["duration_ms"]:
            out["duration_ms"] = int(record["duration_ms"])
        cost = float(record["cost_usd"]) or float(totals.get("cost_usd", 0.0))
        if cost:
            out["cost_usd"] = round(cost, 6)
        for key in ("tokens_in", "tokens_out"):
            if totals.get(key):
                out[key] = int(totals[key])
        if payload.get("corrections"):
            out["corrections"] = int(payload["corrections"])
        if payload.get("commit"):
            out["commit_sha"] = str(payload["commit"])
        return out

    def _lane_fields(
        self,
        event_type: str,
        record: dict[str, Any],
        *,
        model: str | None,
        context_tokens: int,
    ) -> dict[str, Any]:
        """One lane per stage; totals are cumulative because the server merges, not adds."""
        if event_type == "commit":
            # No model, no context: the git lane holds deterministic steps only.
            return dict(
                self._lanes.setdefault(
                    GIT_AGENT, {"name": GIT_AGENT, "color": agent_color(GIT_AGENT)}
                )
            )
        name = str(record["agent"])
        if not name:
            return {}
        lane = self._lanes.setdefault(
            name,
            {
                "name": name,
                "color": agent_color(name),
                "context_window": context_window_for(model, self.context_window),
            },
        )
        if model:
            lane["model"] = model
            lane["context_window"] = context_window_for(model, self.context_window)
        if event_type == "agent_turn":
            lane["cost_usd"] = round(lane.get("cost_usd", 0.0) + float(record["cost_usd"]), 6)
            lane["tokens_in"] = lane.get("tokens_in", 0) + int(record["tokens_in"])
            lane["tokens_out"] = lane.get("tokens_out", 0) + int(record["tokens_out"])
            # The live context is the last turn's prompt (input + cache reads), never a sum.
            live = context_tokens or int(record["tokens_in"])
            if live:
                lane["context_tokens"] = live
        return dict(lane)

    # --- phase sequencing ---------------------------------------------------

    def _take_seq(self) -> int:
        seq = self._next_seq
        self._next_seq += 1
        return seq

    def _open_phase(self, name: str) -> int:
        seq = self._take_seq()
        self._open_phases.append((name, seq))
        return seq

    def _close_phase(self, name: str) -> int:
        # Innermost match: a build correction nests inside the checks phase.
        for index in range(len(self._open_phases) - 1, -1, -1):
            if self._open_phases[index][0] == name:
                return self._open_phases.pop(index)[1]
        return self._take_seq()

    def _current_seq(self, name: str) -> int | None:
        for open_name, seq in reversed(self._open_phases):
            if open_name == name:
                return seq
        return None

    def _accumulate(self, record: dict[str, Any]) -> None:
        seq = self._current_seq(str(record["phase"]))
        if seq is None:
            return
        totals = self._phase_totals.setdefault(
            seq, {"tokens_in": 0.0, "tokens_out": 0.0, "cost_usd": 0.0}
        )
        totals["tokens_in"] += int(record["tokens_in"])
        totals["tokens_out"] += int(record["tokens_out"])
        totals["cost_usd"] += float(record["cost_usd"])

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None
