"""The six deterministic gates. Every one is post-hoc, code-run, and agent-proof."""

from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from adw.envelopes import ENVELOPE_REMINDER, Envelope, ParseResult

CHECK_OUTPUT_TAIL = 2000


@dataclass(frozen=True)
class GateCheck:
    name: str
    ok: bool
    note: str = ""


@dataclass
class GateReport:
    checks: list[GateCheck] = field(default_factory=list)

    def add(self, check: GateCheck) -> GateCheck:
        self.checks.append(check)
        return check

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    @property
    def failures(self) -> list[GateCheck]:
        return [c for c in self.checks if not c.ok]

    def correction_text(self) -> str:
        """The one correction message a failed gate sends back into the session."""
        lines = [
            "GATE FAILURE — the runner verified your reply against the real repo and it failed."
        ]
        for check in self.failures:
            lines.append(f"\n[{check.name}] {check.note}")
        lines.append(
            "\nFix exactly this, change nothing else, and re-emit the full envelope. "
            + ENVELOPE_REMINDER
        )
        return "\n".join(lines)


# --- glob boundaries -------------------------------------------------------


def translate_glob(pattern: str) -> str:
    """Glob → regex where `*` never crosses `/` but `**` may."""
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        char = pattern[i]
        if char == "*":
            j = i
            while j < n and pattern[j] == "*":
                j += 1
            stars = j - i
            at_segment_start = i == 0 or pattern[i - 1] == "/"
            if stars >= 2 and at_segment_start and j < n and pattern[j] == "/":
                out.append("(?:.*/)?")  # `**/` may match zero directories
                i = j + 1
                continue
            out.append(".*" if stars >= 2 else "[^/]*")
            i = j
            continue
        if char == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(char))
        i += 1
    return "".join(out)


def normalize_path(path: str) -> str:
    """Repo-relative, forward slashes, no leading `./` (but `.env` keeps its dot)."""
    cleaned = path.replace("\\", "/").strip()
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned


def matches_boundary(path: str, boundary: list[str]) -> bool:
    normalized = normalize_path(path)
    return any(re.fullmatch(translate_glob(p), normalized) for p in boundary)


# --- gate 1: envelope ------------------------------------------------------


def gate_envelope(result: ParseResult, stage: str) -> GateCheck:
    if result.ok:
        return GateCheck("envelope", True, f"parsed a valid {stage} envelope")
    return GateCheck("envelope", False, f"{result.error}. {ENVELOPE_REMINDER}")


# --- gate 2: artifacts exist ----------------------------------------------


def gate_artifacts(repo: Path, envelope: Envelope) -> GateCheck:
    problems = []
    for rel in envelope.artifacts:
        path = repo / rel
        if not path.is_file():
            problems.append(f"{rel} (declared but does not exist)")
        elif path.stat().st_size == 0:
            problems.append(f"{rel} (exists but is empty)")
    if problems:
        return GateCheck("artifacts", False, "declared artifacts unusable: " + "; ".join(problems))
    return GateCheck("artifacts", True, f"{len(envelope.artifacts)} artifact(s) present")


# --- gate 3: changed-files truth ------------------------------------------


def gate_changed_files(actual: list[str], claimed: list[str]) -> GateCheck:
    actual_set = {normalize_path(p) for p in actual}
    claimed_set = {normalize_path(p) for p in claimed}
    invented = sorted(claimed_set - actual_set)
    undeclared = sorted(actual_set - claimed_set)
    if not invented and not undeclared:
        return GateCheck("changed_files", True, f"{len(actual_set)} file(s) match the claim")
    parts = []
    if invented:
        parts.append(f"claimed but not changed on disk: {', '.join(invented)}")
    if undeclared:
        parts.append(f"changed on disk but not declared: {', '.join(undeclared)}")
    return GateCheck("changed_files", False, "; ".join(parts))


# --- gate 4: write boundary ------------------------------------------------


@dataclass(frozen=True)
class BoundaryResult:
    check: GateCheck
    offending: list[str]


def gate_boundary(actual: list[str], boundary: list[str] | None) -> BoundaryResult:
    if boundary is None:
        return BoundaryResult(GateCheck("boundary", True, "unrestricted within the repo"), [])
    offending = sorted(p for p in actual if not matches_boundary(p, boundary))
    if not offending:
        allowed = ", ".join(boundary) if boundary else "read-only"
        return BoundaryResult(GateCheck("boundary", True, f"all writes inside [{allowed}]"), [])
    allowed = ", ".join(boundary) if boundary else "nothing (this role is read-only)"
    note = (
        f"wrote outside the boundary: {', '.join(offending)}. "
        f"You may only write: {allowed}. Those files have been REVERTED by the runner."
    )
    return BoundaryResult(GateCheck("boundary", False, note), offending)


# --- gate 5: verdict consistency ------------------------------------------


def gate_verdict(envelope: Envelope) -> GateCheck:
    if envelope.approved is None:
        return GateCheck("verdict", False, '"approved" must be present and be true or false')
    if envelope.approved and envelope.blocking:
        return GateCheck(
            "verdict",
            False,
            f"approved: true with {len(envelope.blocking)} blocking finding(s) still listed — "
            "either clear the blocking list or set approved: false",
        )
    if not envelope.approved and not envelope.blocking and envelope.status == "ok":
        return GateCheck(
            "verdict",
            False,
            "approved: false with an empty blocking list and no reason — "
            "list what blocks approval, or approve",
        )
    return GateCheck(
        "verdict", True, f"approved={envelope.approved}, {len(envelope.blocking)} blocking"
    )


# --- gate 6: executed checks ----------------------------------------------


@dataclass(frozen=True)
class CheckRun:
    command: str
    exit_code: int
    output: str

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def run_checks(repo: Path, commands: list[str], timeout: int) -> list[CheckRun]:
    """Run the repo's own quality commands. An agent's 'tests pass' counts for nothing."""
    runs: list[CheckRun] = []
    for command in commands:
        try:
            proc = subprocess.run(
                shlex.split(command),
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            output = (proc.stdout or "") + (proc.stderr or "")
            runs.append(CheckRun(command, proc.returncode, output[-CHECK_OUTPUT_TAIL:]))
        except FileNotFoundError as exc:
            runs.append(CheckRun(command, 127, f"command not found: {exc}"))
        except subprocess.TimeoutExpired:
            runs.append(CheckRun(command, 124, f"timed out after {timeout}s"))
    return runs


def gate_checks(runs: list[CheckRun]) -> GateCheck:
    failed = [r for r in runs if not r.ok]
    if not runs:
        return GateCheck("checks", True, "no checks configured (nothing was verified)")
    if not failed:
        return GateCheck("checks", True, f"{len(runs)} check(s) passed")
    detail = "; ".join(f"`{r.command}` exited {r.exit_code}" for r in failed)
    tails = "\n\n".join(f"$ {r.command}\n{r.output.strip()}" for r in failed)
    return GateCheck("checks", False, f"{detail}\n\n{tails}")
