"""What a run leaves behind for a later process: its pid, and what it really finished.

A run that dies, hangs or is killed strands everything it did unless it wrote it
down first. This module owns that record. Two questions are asked of it later:

* `--list-runs` / `--kill`: which runs exist, which are still alive, and is the pid
  in the record still the process that run started (pids get recycled).
* `--resume`: which stages genuinely completed. "Completed" here is never the
  runner's memory of what it meant to do — it is a commit that three independent
  places agree on: a stage record written *after* the commit landed, a `commit`
  event in the run's own telemetry carrying the same sha, and git itself reporting
  that sha as an ancestor of the branch tip. A stage that was mid-turn when the
  process died satisfies none of them and runs again.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from adw import gitwork

RECORD_FILENAME = "run.json"
STAGES_DIRNAME = "stages"
TELEMETRY_FILENAME = "telemetry.jsonl"

RUNNING = "running"
FINISHED = "finished"
STOPPED = "stopped"

# What a factory process's command line always contains. Checked in addition to the
# recorded command line, so a record written by an older version still cannot point
# `--kill` at an unrelated pid.
FACTORY_MARKER = "run.py"


class RunError(Exception):
    """The run cannot be read back, or cannot be resumed or killed safely."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


# --- the record -------------------------------------------------------------


@dataclass(frozen=True)
class StageRecord:
    """A stage that passed its gates AND landed a commit — written after the commit,
    so its existence already means the work is in git and not merely attempted."""

    stage: str
    commit: str
    envelope: dict[str, Any] = field(default_factory=dict)
    ts: str = ""


@dataclass
class RunRecord:
    """One run, as a later process finds it."""

    run_id: str
    repo: str
    request: str = ""
    workflow: list[str] = field(default_factory=list)
    workflow_name: str = ""
    branch: str | None = None
    branch_origin: str = ""
    # The tip the branch had before this attempt committed anything: the fallback
    # "where we left it" for a run that stopped before its first commit.
    base_sha: str = ""
    pid: int | None = None
    cmdline: str = ""
    state: str = RUNNING
    started: str = ""
    ended: str = ""
    attempt: int = 1
    accepted: bool | None = None
    reason: str = ""
    run_dir: Path | None = None  # where it was read from; never serialized

    FIELDS = (
        "run_id", "repo", "request", "workflow", "workflow_name", "branch",
        "branch_origin", "base_sha", "pid", "cmdline", "state", "started",
        "ended", "attempt", "accepted", "reason",
    )

    def to_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.FIELDS}

    @classmethod
    def from_dict(cls, data: dict[str, Any], run_dir: Path) -> RunRecord:
        known = {k: v for k, v in data.items() if k in cls.FIELDS}
        known.setdefault("run_id", run_dir.name)
        known.setdefault("repo", "")
        return cls(run_dir=run_dir, **known)

    @property
    def finished(self) -> bool:
        return self.state == FINISHED


def record_path(run_dir: Path) -> Path:
    return run_dir / RECORD_FILENAME


def read(run_dir: Path) -> RunRecord | None:
    path = record_path(run_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return RunRecord.from_dict(data, run_dir) if isinstance(data, dict) else None


def write(run_dir: Path, record: RunRecord) -> None:
    """Atomic: a reader never sees a half-written record, however the run ends."""
    run_dir.mkdir(parents=True, exist_ok=True)
    target = record_path(run_dir)
    temp = target.with_suffix(".json.tmp")
    temp.write_text(json.dumps(record.to_dict(), indent=2, default=str), encoding="utf-8")
    os.replace(temp, target)


def update(run_dir: Path, **fields: Any) -> RunRecord | None:
    """Merge into whatever is on disk. Absent keys keep the value they had."""
    record = read(run_dir)
    if record is None:
        return None
    for name, value in fields.items():
        setattr(record, name, value)
    write(run_dir, record)
    return record


def open_record(
    run_dir: Path,
    *,
    run_id: str,
    repo: Path,
    request: str,
    workflow: tuple[str, ...],
    workflow_name: str,
    attempt: int = 1,
) -> RunRecord:
    """Claim the run dir for this process: the pid goes down before any agent runs,
    because the moment you need a hung run's pid is the moment it stops emitting."""
    previous = read(run_dir)
    record = RunRecord(
        run_id=run_id,
        repo=str(repo),
        request=request,
        workflow=list(workflow),
        workflow_name=workflow_name,
        branch=previous.branch if previous else None,
        branch_origin=previous.branch_origin if previous else "",
        pid=os.getpid(),
        cmdline=own_cmdline(),
        state=RUNNING,
        started=previous.started if previous and previous.started else _now(),
        attempt=attempt,
    )
    write(run_dir, record)
    return record


def close_record(run_dir: Path, *, state: str, accepted: bool, reason: str) -> None:
    """The pid record is cleared here, so a finished run is never a ghost in --list-runs."""
    update(run_dir, pid=None, state=state, accepted=accepted, reason=reason, ended=_now())


# --- per-stage evidence -----------------------------------------------------


def save_stage(run_dir: Path, stage: str, envelope: dict[str, Any], commit: str) -> None:
    """Written only once the commit exists — an unwritten record is the honest answer
    for a stage that produced nothing git can be pointed at."""
    directory = run_dir / STAGES_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    body = {"stage": stage, "commit": commit, "envelope": envelope, "ts": _now()}
    (directory / f"{stage}.json").write_text(json.dumps(body, indent=2, default=str), "utf-8")


def stage_records(run_dir: Path) -> dict[str, StageRecord]:
    out: dict[str, StageRecord] = {}
    directory = run_dir / STAGES_DIRNAME
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("commit"):
            stage = str(data.get("stage") or path.stem)
            out[stage] = StageRecord(
                stage=stage,
                commit=str(data["commit"]),
                envelope=data.get("envelope") or {},
                ts=str(data.get("ts") or ""),
            )
    return out


def telemetry_events(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / TELEMETRY_FILENAME
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            out.append(event)
    return out


def telemetry_commits(run_dir: Path) -> list[tuple[str, str]]:
    """(stage, sha) for every commit the run's own log says it made, in order."""
    out: list[tuple[str, str]] = []
    for event in telemetry_events(run_dir):
        if event.get("event") != "commit":
            continue
        sha = str((event.get("payload") or {}).get("sha") or "")
        if sha:
            out.append((str(event.get("agent") or event.get("phase") or ""), sha))
    return out


def next_seq(run_dir: Path) -> int:
    """Where a resumed process must start numbering phases so it cannot collide with
    the phases the interrupted process already reported under the same session."""
    consumers = ("phase_start", "phase_end", "commit")
    used = sum(1 for e in telemetry_events(run_dir) if e.get("event") in consumers)
    return used + 1


# --- the process ------------------------------------------------------------


def own_cmdline() -> str:
    return process_cmdline(os.getpid()) or ""


def process_cmdline(pid: int) -> str | None:
    """The live process's own argv, or None if it is gone or unreadable."""
    proc = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or None


def alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # someone else's process — alive, just not ours
        return True
    return True


def live_state(record: RunRecord) -> str:
    """The record's claim, corrected by the operating system. A run whose process is
    gone is stopped, whatever the file it never got to update still says."""
    if record.state != RUNNING:
        return record.state
    return RUNNING if alive(record.pid) else STOPPED


def refusal_to_signal(record: RunRecord) -> str:
    """Empty when this pid is provably still this run. Otherwise the reason, in words.

    Killing an unrelated process because a stale pid file pointed at it is the worst
    bug this feature could have, so the pid must clear every one of these.
    """
    pid = record.pid
    if record.state != RUNNING:
        return f"run {record.run_id} is not running (state: {record.state})"
    if not pid:
        return f"run {record.run_id} recorded no pid, so there is nothing to signal"
    if not alive(pid):
        return f"no process {pid} is running — the pid record is stale, nothing was signalled"
    live = process_cmdline(pid)
    if live is None:
        return f"could not read the command line of process {pid} — refusing to signal it"
    if record.cmdline and live != record.cmdline:
        return (
            f"process {pid} is NOT this run — the pid has been recycled.\n"
            f"  now:      {live}\n"
            f"  recorded: {record.cmdline}"
        )
    if FACTORY_MARKER not in live:
        return (
            f"process {pid} does not look like a factory run "
            f"(no {FACTORY_MARKER!r} in its command line): {live}"
        )
    return ""


def terminate(record: RunRecord) -> None:
    """SIGTERM only — the run installs a handler that stops its agent and says so."""
    refusal = refusal_to_signal(record)
    if refusal:
        raise RunError(refusal)
    os.kill(int(record.pid or 0), signal.SIGTERM)


# --- listing ----------------------------------------------------------------


def list_runs(root: Path, limit: int = 20) -> list[RunRecord]:
    """Recent runs under a runs root, newest first, read straight from the run dirs."""
    if not root.is_dir():
        return []
    found = [read(child) for child in root.iterdir() if child.is_dir()]
    records = [r for r in found if r is not None]
    records.sort(key=lambda r: (r.started, r.run_id), reverse=True)
    return records[:limit]


# --- resume -----------------------------------------------------------------


@dataclass(frozen=True)
class ResumePlan:
    """What a resumed run may skip, what it must re-run, and why."""

    record: RunRecord
    done: dict[str, StageRecord]
    ref: str  # the branch the resumed run must land back on
    tip: str  # where that branch stood when the plan was made
    evidence: list[str] = field(default_factory=list)
    dirty: tuple[str, ...] = ()

    @property
    def attempt(self) -> int:
        return self.record.attempt + 1

    @property
    def first_stage(self) -> str:
        """The stage the resumed run starts at — the first with no evidence behind it."""
        for name in self.record.workflow:
            if name not in self.done:
                return name
        return ""


def _looks_like_sha(text: str) -> bool:
    return len(text) == 40 and all(c in "0123456789abcdef" for c in text.lower())


def _resume_ref(record: RunRecord) -> str:
    """The ref this run's commits went to. Named, so resume can refuse if it is gone."""
    if record.branch:
        return record.branch
    # A --no-branch run started on a detached HEAD recorded a sha, not a branch:
    # there is no ref that still means "where this run was working".
    return "" if _looks_like_sha(record.branch_origin) else record.branch_origin


def plan_resume(repo: Path, run_dir: Path, run_id: str) -> ResumePlan:
    """Everything that must be true before a resumed run is allowed to touch git."""
    record = read(run_dir)
    if record is None:
        raise RunError(
            f"unknown run '{run_id}' — no {RECORD_FILENAME} under {run_dir}. "
            "Use --list-runs to see the runs this repo has."
        )
    if Path(record.repo).resolve() != repo:
        raise RunError(
            f"run {run_id} ran against {record.repo}, not {repo} — resume it from there."
        )
    if live_state(record) == RUNNING:
        raise RunError(
            f"run {run_id} is still running (pid {record.pid}) — "
            f"wait for it, or stop it with --kill {run_id}."
        )
    if record.finished and record.accepted:
        raise RunError(f"run {run_id} already completed on {record.ended} — nothing to resume.")

    ref = _resume_ref(record)
    if not ref:
        raise RunError(
            f"run {run_id} recorded no branch to come back to, so there is nothing "
            "safe to resume onto."
        )
    if not gitwork.branch_exists(repo, ref):
        raise RunError(
            f"the branch run {run_id} worked on ('{ref}') is gone — its commits cannot "
            "be built on, so resuming would silently start over somewhere else."
        )

    commits = telemetry_commits(run_dir)
    expected = commits[-1][1] if commits else record.base_sha
    tip = gitwork.rev_parse(repo, ref) or ""
    if expected and tip != expected:
        raise RunError(
            f"branch '{ref}' has moved on: its tip is {tip[:8]}, but run {run_id} last "
            f"left it at {expected[:8]}. Resuming would build on work this run never did."
        )

    done, evidence = _verified_stages(repo, run_dir, record, ref, {sha for _, sha in commits})
    if record.workflow and all(name in done for name in record.workflow):
        raise RunError(f"run {run_id} already completed every stage of its workflow.")

    # Nothing here touches git's state: planning a resume must be safe to do twice,
    # and safe to do under --dry-run. The checkout is the pipeline's first step.
    return ResumePlan(
        record=record,
        done=done,
        ref=ref,
        tip=tip,
        evidence=evidence,
        dirty=tuple(sorted(gitwork.dirty_paths(repo))),
    )


def _verified_stages(
    repo: Path, run_dir: Path, record: RunRecord, ref: str, logged: set[str]
) -> tuple[dict[str, StageRecord], list[str]]:
    """Walk the workflow in order and stop at the first stage the evidence does not
    cover — a later stage cannot be "done" if the one feeding it is not."""
    saved = stage_records(run_dir)
    done: dict[str, StageRecord] = {}
    evidence: list[str] = []
    for name in record.workflow:
        entry = saved.get(name)
        if entry is None:
            evidence.append(f"{name}: re-runs — no committed stage record")
            break
        if not entry.envelope:
            evidence.append(f"{name}: re-runs — its stage record carries no envelope to hand on")
            break
        if entry.commit not in logged:
            evidence.append(f"{name}: re-runs — {entry.commit[:8]} is not in the run's telemetry")
            break
        if not gitwork.is_ancestor(repo, entry.commit, ref):
            evidence.append(f"{name}: re-runs — {entry.commit[:8]} is not on {ref}")
            break
        done[name] = entry
        evidence.append(f"{name}: done — {entry.commit[:8]} on {ref}, logged and gated")
    return done, evidence
