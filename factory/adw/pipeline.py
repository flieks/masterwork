"""Stage order, correction caps, the review loop, and run acceptance — all in code."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from adw import envelopes, gates, gitwork, prompts
from adw.agent import AgentError, AgentSession, AgentTurn
from adw.config import FactoryConfig, Stage
from adw.envelopes import Envelope
from adw.gates import GateReport
from adw.telemetry import Telemetry

PASSED = "passed"
FAILED = "failed"
BLOCKED = "blocked"

UNVERIFIED_VERDICT = "UNVERIFIED — no checks ran"

# A review that files findings has reviewed. Saying `status: "blocked"` on top of
# them does not make the run stoppable — it is a rejection, and the summary and the
# telemetry both say so rather than quietly rewriting what the agent returned.
REJECTION_DESPITE_STATUS = "read as a rejection despite status"


@dataclass
class StageOutcome:
    name: str
    status: str
    corrections: int = 0
    envelope: Envelope | None = None
    commit: str | None = None
    cost_usd: float = 0.0
    duration_ms: int = 0
    detail: str = ""

    @property
    def passed(self) -> bool:
        return self.status == PASSED


@dataclass
class RunResult:
    run_id: str
    accepted: bool
    reason: str
    outcomes: list[StageOutcome] = field(default_factory=list)
    cost_usd: float = 0.0
    corrections: int = 0
    turns: int = 0
    unresolved: list[str] = field(default_factory=list)
    telemetry_path: Path | None = None
    # False when the run executed no checks: accepted, but nothing was verified.
    verified: bool = True

    @property
    def exit_code(self) -> int:
        return 0 if self.accepted else 1


SessionFactory = Callable[[Stage], AgentSession]


class Pipeline:
    """plan → build → checks → review (capped loop) → document."""

    def __init__(
        self,
        config: FactoryConfig,
        request: str,
        telemetry: Telemetry,
        *,
        session_factory: SessionFactory | None = None,
    ) -> None:
        self.cfg = config
        self.request = request
        self.tel = telemetry
        self.repo = config.repo
        self.outcomes: list[StageOutcome] = []
        self.sessions: dict[str, AgentSession] = {}
        self.cost_usd = 0.0
        self.corrections = 0
        self.turns = 0
        self.checks_passed = False
        self.review_approved = False
        self.unresolved: list[str] = []
        self._session_factory = session_factory or self._make_session

    # --- entry point -------------------------------------------------------

    def run(self) -> RunResult:
        self.tel.emit("phase_start", phase="run", detail=self.request[:400], title=self.request)
        for warning in self.cfg.warnings:
            self.tel.emit("phase_start", phase="run", result="warn", detail=warning)
        try:
            return self._run()
        except AgentError as exc:
            return self._finish(f"could not run the agent CLI: {exc}", aborted=True)

    def _run(self) -> RunResult:
        plan = self._record(self._agent_stage("plan"))
        if not plan.passed:
            return self._finish(f"plan stage {plan.status}: {plan.detail}", aborted=True)

        build = self._record(
            self._agent_stage("build", previous=plan.envelope, previous_stage="plan")
        )
        if not build.passed:
            return self._finish(f"build stage {build.status}: {build.detail}", aborted=True)

        checks = self._record(self._checks_stage())
        if not checks.passed:
            return self._finish(f"executed checks failed: {checks.detail}", aborted=True)

        build_envelope, approved, stopper = self._review_loop(build.envelope)
        if stopper is not None:
            return self._finish(
                f"{stopper.name} stage {stopper.status}: {stopper.detail}", aborted=True
            )
        if not approved:
            return self._finish(
                f"review did not approve within {self.cfg.max_review_rounds} round(s)",
                aborted=False,
            )

        document = self._record(
            self._agent_stage("document", previous=build_envelope, previous_stage="build")
        )
        if not document.passed:
            return self._finish(
                f"document stage {document.status}: {document.detail}", aborted=True
            )

        return self._finish("all stages passed, checks green, review approved", aborted=False)

    # --- stages ------------------------------------------------------------

    def _agent_stage(
        self,
        name: str,
        *,
        previous: Envelope | None = None,
        previous_stage: str | None = None,
    ) -> StageOutcome:
        stage = self.cfg.stages[name]
        session = self._session(name)
        started = time.monotonic()
        self.tel.emit(
            "phase_start", phase=name, agent=name, model=stage.model, detail=stage.boundary_text
        )
        snap = gitwork.snapshot(self.repo)
        compiled = prompts.compile_prompt(
            role=self.cfg.roles[name],
            stage=stage,
            request=self.request,
            repo=self.repo,
            previous_stage=previous_stage,
            previous=previous,
            artifact_max_bytes=self.cfg.artifact_max_bytes,
        )
        session.system_prompt = compiled.system
        turn = self._dispatch(stage, session, compiled.user)
        outcome = self._gate_loop(stage, session, snap, turn)
        outcome.duration_ms = int((time.monotonic() - started) * 1000)
        if outcome.passed:
            self._commit(stage.name, outcome)
        self.tel.emit(
            "phase_end",
            phase=name,
            agent=name,
            model=stage.model,
            result="ok" if outcome.passed else "fail",
            duration_ms=outcome.duration_ms,
            cost_usd=outcome.cost_usd,
            detail=outcome.detail,
            payload={"corrections": outcome.corrections, "commit": outcome.commit},
        )
        return outcome

    def _checks_stage(self) -> StageOutcome:
        """Pure code: the runner executes the repo's own commands and reads exit codes."""
        started = time.monotonic()
        self.tel.emit("phase_start", phase="checks", detail=f"{len(self.cfg.checks)} command(s)")
        outcome = StageOutcome("checks", FAILED)
        attempt = 0
        while True:
            runs = gates.run_checks(self.repo, self.cfg.checks, self.cfg.timeout_seconds)
            check = gates.gate_checks(runs)
            self._emit_gate("checks", check, attempt=attempt + 1)
            if check.ok:
                outcome.status = PASSED
                outcome.detail = check.note
                self.checks_passed = True
                break
            if attempt >= self.cfg.max_corrections:
                outcome.detail = (
                    f"checks still failing after {attempt} correction(s): {check.note[:400]}"
                )
                self.checks_passed = False
                break
            attempt += 1
            outcome.corrections = attempt
            fix = self._correct_build(
                prompts.checks_correction(check.note, attempt, self.cfg.max_corrections)
            )
            self._record(fix)
            if not fix.passed:
                outcome.detail = f"builder could not fix the checks: {fix.detail}"
                self.checks_passed = False
                break
        outcome.duration_ms = int((time.monotonic() - started) * 1000)
        self.tel.emit(
            "phase_end",
            phase="checks",
            result="ok" if outcome.passed else "fail",
            duration_ms=outcome.duration_ms,
            detail=outcome.detail,
            payload={"corrections": outcome.corrections},
        )
        return outcome

    def _review_loop(
        self, build_envelope: Envelope | None
    ) -> tuple[Envelope | None, bool, StageOutcome | None]:
        """Rejected review → blocking list into the builder session → re-check → re-review."""
        for round_number in range(1, self.cfg.max_review_rounds + 1):
            review = self._record(
                self._agent_stage("review", previous=build_envelope, previous_stage="build")
            )
            if not review.passed:
                return build_envelope, False, review
            envelope = review.envelope
            if envelope is not None and envelope.approved:
                self.review_approved = True
                return build_envelope, True, None
            self.unresolved = list(envelope.blocking) if envelope else []
            if round_number == self.cfg.max_review_rounds:
                return build_envelope, False, None

            fix = self._record(
                self._correct_build(
                    prompts.review_correction(
                        self.unresolved, round_number, self.cfg.max_review_rounds
                    )
                )
            )
            if not fix.passed:
                return build_envelope, False, fix
            build_envelope = fix.envelope
            checks = self._record(self._checks_stage())
            if not checks.passed:
                return build_envelope, False, checks
        return build_envelope, False, None

    def _correct_build(self, instruction: str) -> StageOutcome:
        """One more turn in the SAME builder session, then the build gates again."""
        stage = self.cfg.stages["build"]
        session = self._session("build")
        started = time.monotonic()
        self.tel.emit(
            "phase_start", phase="build", agent="build", model=stage.model, detail="correction"
        )
        snap = gitwork.snapshot(self.repo)
        turn = self._dispatch(stage, session, instruction)
        outcome = self._gate_loop(stage, session, snap, turn)
        outcome.duration_ms = int((time.monotonic() - started) * 1000)
        if outcome.passed:
            self._commit("build", outcome)
        self.tel.emit(
            "phase_end",
            phase="build",
            agent="build",
            model=stage.model,
            result="ok" if outcome.passed else "fail",
            duration_ms=outcome.duration_ms,
            cost_usd=outcome.cost_usd,
            detail=outcome.detail,
            payload={"corrections": outcome.corrections, "commit": outcome.commit},
        )
        return outcome

    # --- gates + corrections ----------------------------------------------

    def _gate_loop(
        self, stage: Stage, session: AgentSession, snap: gitwork.Snapshot, turn: AgentTurn
    ) -> StageOutcome:
        outcome = StageOutcome(stage.name, FAILED)
        corrections = 0
        while True:
            outcome.cost_usd += turn.cost_usd
            if not turn.ok:
                outcome.detail = turn.error or f"the CLI exited {turn.exit_code}"
                return outcome

            parsed = envelopes.parse_envelope(turn.text, stage.name)
            attempt = corrections + 1
            # The attempt rides the envelope gate's event below, which is where a
            # replay would look for it too — one row per turn, parsed or not.
            attempted = envelopes.attempt_block(
                parsed, role=stage.name, attempt=attempt, raw_text=turn.text
            )
            envelope = parsed.envelope
            rejection = False
            if envelope is not None and envelope.status != "ok":
                rejection = self._is_rejection(stage, envelope)
                if rejection:
                    # Recorded, then gated and looped like any other rejection:
                    # `blocked` does not buy a reviewer a silent exit.
                    self._note_rejection(stage.name, envelope)
                elif self._is_clean_stop(stage, envelope):
                    # A blocked/failed stage is a clean stop, not something to correct.
                    outcome.status = BLOCKED if envelope.status == "blocked" else FAILED
                    outcome.envelope = envelope
                    outcome.corrections = corrections
                    outcome.detail = envelope.summary_line or envelope.status
                    stopped = f"stage returned status={envelope.status}: {outcome.detail}"
                    # This turn never reaches the gates, so its envelope has no gate
                    # event to ride: state both here, including the stage-level
                    # verdict the server would otherwise have mined off this line.
                    self.tel.emit(
                        "gate_fail",
                        phase=stage.name,
                        agent=stage.name,
                        result="fail",
                        detail=stopped,
                        envelope=attempted,
                        gate=gates.GateCheck(gates.STAGE_GATE, False, stopped).block(attempt),
                    )
                    return outcome

            report = self._evaluate(stage, snap, parsed)
            self._emit_gates(stage.name, report, attempt=attempt, envelope=attempted)
            if report.ok and envelope is not None:
                outcome.status = PASSED
                outcome.envelope = envelope
                outcome.corrections = corrections
                outcome.detail = envelope.summary_line
                if rejection:
                    outcome.detail = (
                        f'{REJECTION_DESPITE_STATUS}="{envelope.status}": {outcome.detail}'
                    )
                return outcome

            if corrections >= self.cfg.max_corrections:
                outcome.corrections = corrections
                still_failing = "; ".join(f"{c.name}: {c.note[:160]}" for c in report.failures)
                outcome.detail = (
                    f"correction cap ({self.cfg.max_corrections}) reached "
                    f"with gates still failing: {still_failing}"
                )
                return outcome

            corrections += 1
            self.corrections += 1
            turn = self._dispatch(stage, session, report.correction_text())

    def _evaluate(
        self, stage: Stage, snap: gitwork.Snapshot, parsed: envelopes.ParseResult
    ) -> GateReport:
        """The six gates, in order — the boundary is enforced even if the envelope failed."""
        report = GateReport()
        report.add(gates.gate_envelope(parsed, stage.name))

        actual = self._changed(snap)
        boundary = gates.gate_boundary(actual, stage.boundary)
        reverted: list[str] = []
        if boundary.offending:
            reverted = gitwork.revert(self.repo, boundary.offending)
            self.tel.emit(
                "gate_fail",
                phase=stage.name,
                agent=stage.name,
                result="fail",
                detail=f"reverted out-of-boundary paths: {', '.join(reverted)}",
                payload={"reverted": reverted},
            )
            actual = self._changed(snap)

        envelope = parsed.envelope
        if envelope is not None:
            report.add(gates.gate_artifacts(self.repo, envelope))
            # Reverted paths are excluded on both sides: the agent hears "you wrote
            # outside your boundary", not that plus "you claimed a file you didn't change".
            report.add(gates.gate_changed_files(actual, envelope.changed_files, ignore=reverted))
        report.add(boundary.check)
        if envelope is not None and envelopes.owes_a_verdict(stage.name):
            report.add(gates.gate_verdict(envelope))
        return report

    @staticmethod
    def _is_rejection(stage: Stage, envelope: Envelope) -> bool:
        """A role that owes a verdict and filed findings has judged the work, whatever
        its `status` claims — the one shape that must never end the run quietly."""
        return envelopes.owes_a_verdict(stage.name) and gates.verdict_despite_status(envelope)

    @classmethod
    def _is_clean_stop(cls, stage: Stage, envelope: Envelope) -> bool:
        """Whether a non-ok envelope may end the run with nobody answering for it.

        A role that owes a verdict only stops when its envelope says so consistently:
        a `blocked` with neither findings nor a stated reason goes to the gates for a
        correction, like any other reply that fails one."""
        if envelope.status == "ok" or cls._is_rejection(stage, envelope):
            return False
        return not envelopes.owes_a_verdict(stage.name) or gates.gate_verdict(envelope).ok

    def _note_rejection(self, phase: str, envelope: Envelope) -> None:
        """Say out loud that the runner is not taking `blocked` at face value."""
        self.tel.emit(
            "gate_fail",
            phase=phase,
            agent=phase,
            result="fail",
            detail=(
                f"stage returned status={envelope.status} with {len(envelope.blocking)} "
                f"blocking finding(s) — {REJECTION_DESPITE_STATUS}, not a clean stop"
            ),
            payload={
                "verdict": "rejection",
                "status": envelope.status,
                "blocking": list(envelope.blocking),
            },
        )

    # --- plumbing ----------------------------------------------------------

    def _changed(self, snap: gitwork.Snapshot) -> list[str]:
        return gitwork.changed_paths(self.repo, snap, exclude=self.cfg.run_dir_exclusions)

    def _dispatch(self, stage: Stage, session: AgentSession, prompt: str) -> AgentTurn:
        # Written before the send, so a turn that never returns is still diagnosable.
        saved = prompts.save_prompt_copy(
            self.cfg.run_dir, stage.name, session.turns + 1, session.system_prompt, prompt
        )
        role = self.cfg.roles.get(stage.name)
        turn = session.send(prompt)
        self.turns += 1
        self.cost_usd += turn.cost_usd
        context_pct = self.tel.note_input_tokens(turn.input_tokens)
        self.tel.emit(
            "agent_turn",
            phase=stage.name,
            agent=stage.name,
            model=stage.model,
            result="ok" if turn.ok else "fail",
            duration_ms=turn.duration_ms,
            tokens_in=turn.input_tokens,
            tokens_out=turn.output_tokens,
            cost_usd=turn.cost_usd,
            context_pct=context_pct,
            detail=turn.error or f"{len(turn.tool_events)} tool event(s)",
            payload={
                "session_id": session.session_id,
                "turn": session.turns,
                "prompts": saved,
                # Which layer each file came from: the prompt masterwork displays is
                # the library copy, and a repo override wins over it silently.
                "role_layers": dict(role.layers) if role is not None else {},
            },
            context_tokens=turn.context_tokens,
        )
        return turn

    def _commit(self, stage_name: str, outcome: StageOutcome) -> None:
        envelope = outcome.envelope
        if envelope is None:
            return
        message = f"{stage_name}: {envelope.summary_line or 'no summary'}"
        if envelope.assumptions:
            message += "\n\n" + "\n".join(f"Assumption: {a}" for a in envelope.assumptions)
        sha = gitwork.commit(self.repo, message)
        outcome.commit = sha
        if sha:
            self.tel.emit(
                "commit",
                phase=stage_name,
                agent=stage_name,
                detail=message.splitlines()[0],
                payload={"sha": sha, "assumptions": envelope.assumptions},
            )

    def _session(self, name: str) -> AgentSession:
        if name not in self.sessions:
            self.sessions[name] = self._session_factory(self.cfg.stages[name])
        return self.sessions[name]

    def _make_session(self, stage: Stage) -> AgentSession:
        def on_event(event_type: str, payload: dict[str, Any]) -> None:
            # The measured tool duration is a v1.13 POST field only — lift it out
            # so the JSONL payload keeps exactly the shape it has always had.
            duration_ms = payload.pop("duration_ms", None)
            errored = payload.get("is_error")
            self.tel.emit(
                event_type,
                phase=stage.name,
                agent=stage.name,
                model=stage.model,
                tool_name=str(payload.get("name") or ""),
                detail=str(payload.get("input") or payload.get("kind") or ""),
                payload=payload,
                tool_duration_ms=duration_ms,
                ok=(not errored) if errored is not None else None,
            )

        return AgentSession(
            stage=stage.name,
            model=stage.model or "sonnet",
            cwd=self.repo,
            disallowed_tools=stage.disallowed_tools,
            claude_bin=self.cfg.claude_bin,
            timeout_seconds=self.cfg.timeout_seconds,
            on_event=on_event,
        )

    def _emit_gates(
        self, phase: str, report: GateReport, *, attempt: int, envelope: dict[str, Any]
    ) -> None:
        for check in report.checks:
            # The envelope attempt rides its own gate's event: one event, one source.
            rider = envelope if check.name == gates.ENVELOPE_GATE else None
            self._emit_gate(phase, check, attempt=attempt, envelope=rider)

    def _emit_gate(
        self,
        phase: str,
        check: gates.GateCheck,
        *,
        attempt: int = 1,
        envelope: dict[str, Any] | None = None,
    ) -> None:
        self.tel.emit(
            "gate_pass" if check.ok else "gate_fail",
            phase=phase,
            agent=phase,
            result="ok" if check.ok else "fail",
            # `detail` is capped for the log line; the gate block carries it whole.
            detail=f"{check.name}: {check.note}"[:1000],
            payload={"gate": check.name},
            gate=check.block(attempt),
            envelope=envelope,
        )

    def _record(self, outcome: StageOutcome) -> StageOutcome:
        self.outcomes.append(outcome)
        return outcome

    # --- finish ------------------------------------------------------------

    def _finish(self, reason: str, *, aborted: bool) -> RunResult:
        # Acceptance is not "every phase ran": checks must be green, review must
        # have approved, and nothing may have aborted.
        accepted = (not aborted) and self.checks_passed and self.review_approved
        result = RunResult(
            run_id=self.cfg.run_id,
            accepted=accepted,
            reason=reason,
            outcomes=list(self.outcomes),
            cost_usd=round(self.cost_usd, 6),
            corrections=self.corrections,
            turns=self.turns,
            unresolved=list(self.unresolved) if not accepted else [],
            telemetry_path=self.tel.path,
            verified=self.cfg.verified,
        )
        self.tel.emit(
            "run_end",
            phase="run",
            result="ok" if accepted else "fail",
            detail=reason,
            cost_usd=result.cost_usd,
            ended=True,
            stats=run_stats(result),
        )
        return result


def run_stats(result: RunResult) -> dict[str, Any]:
    stages: dict[str, Any] = {}
    for outcome in result.outcomes:
        entry = stages.setdefault(
            outcome.name,
            {
                "status": outcome.status,
                "corrections": 0,
                "cost_usd": 0.0,
                "duration_ms": 0,
                "runs": 0,
            },
        )
        entry["status"] = outcome.status
        entry["corrections"] += outcome.corrections
        entry["cost_usd"] = round(entry["cost_usd"] + outcome.cost_usd, 6)
        entry["duration_ms"] += outcome.duration_ms
        entry["runs"] += 1
    return {
        "accepted": result.accepted,
        "verified": result.verified,
        "cost_usd": result.cost_usd,
        "turns": result.turns,
        "corrections": result.corrections,
        "stages": stages,
    }


def format_summary(result: RunResult) -> str:
    """Per-stage table so runs are comparable over time."""
    rows = [("STAGE", "STATUS", "CORR", "COMMIT", "COST", "DURATION", "SUMMARY")]
    for outcome in result.outcomes:
        rows.append(
            (
                outcome.name,
                outcome.status,
                str(outcome.corrections),
                (outcome.commit or "-")[:8],
                f"${outcome.cost_usd:.4f}",
                f"{outcome.duration_ms / 1000:.1f}s",
                outcome.detail[:60],
            )
        )
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    lines = [
        "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip() for row in rows
    ]
    verdict = "ACCEPTED" if result.accepted else "NOT ACCEPTED"
    if not result.verified:
        verdict += f" ({UNVERIFIED_VERDICT})"
    lines.append("")
    lines.append(
        f"{verdict} — {result.reason} "
        f"({result.turns} turns, {result.corrections} corrections, ${result.cost_usd:.4f})"
    )
    if result.unresolved:
        lines.append("Unresolved blocking findings:")
        lines += [f"  - {item}" for item in result.unresolved]
    return "\n".join(lines)
