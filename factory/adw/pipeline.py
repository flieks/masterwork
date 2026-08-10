"""Stage order, correction caps, the review loop, and run acceptance — all in code."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from adw import envelopes, gates, gitwork, prompts, runs, workflows
from adw.agent import AgentError, AgentSession, AgentTurn
from adw.config import FactoryConfig, Stage
from adw.envelopes import Envelope
from adw.gates import GateReport
from adw.telemetry import Telemetry
from adw.workflows import CHECKS

PASSED = "passed"
FAILED = "failed"
BLOCKED = "blocked"
# A stage a resumed run did not have to run: its commit is already on the branch.
REUSED = "reused"

UNVERIFIED_VERDICT = "UNVERIFIED — no checks ran"
UNREVIEWED_VERDICT = "UNREVIEWED — no review stage in this workflow"

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
    tokens: int = 0
    unresolved: list[str] = field(default_factory=list)
    telemetry_path: Path | None = None
    # Where the run worked, and how to get back. None only before the branch step ran.
    branch: gitwork.RunBranch | None = None
    # Non-empty when a budget cap stopped the run: the figure and the cap, verbatim.
    budget_stop: str = ""
    # Whether the tree still holds work no stage ever committed — only asked on a stop.
    left_uncommitted: bool = False
    # False when the run executed no checks: accepted, but nothing was verified.
    verified: bool = True
    # False when no review stage ran: accepted, but nobody judged the work.
    reviewed: bool = True
    workflow: tuple[str, ...] = ()
    workflow_name: str = workflows.DEFAULT_PRESET
    # Which attempt at this run_id this was: 1 for a fresh run, 2+ after --resume.
    attempt: int = 1
    resumed: bool = False

    @property
    def exit_code(self) -> int:
        return 0 if self.accepted else 1

    @property
    def committed(self) -> list[tuple[str, str]]:
        """(stage, sha) for every commit on this run's branch — including the ones a
        resumed run inherited, because they are just as much this run's history."""
        return [(o.name, o.commit) for o in self.outcomes if o.commit]

    @property
    def reused(self) -> list[str]:
        """Stages a resumed run skipped because their commit was already on the branch."""
        return [o.name for o in self.outcomes if o.status == REUSED]

    @property
    def skipped(self) -> list[str]:
        """Stages the workflow declared that the run never reached."""
        reached = {o.name for o in self.outcomes}
        return [name for name in self.workflow if name not in reached]


SessionFactory = Callable[[Stage], AgentSession]


class Pipeline:
    """Runs the stages this config declares, in order — `full` is plan → build →
    checks → review (capped loop) → document, and every preset is the same machinery
    with fewer stages."""

    def __init__(
        self,
        config: FactoryConfig,
        request: str,
        telemetry: Telemetry,
        *,
        session_factory: SessionFactory | None = None,
        resume: runs.ResumePlan | None = None,
    ) -> None:
        self.cfg = config
        self.request = request
        self.tel = telemetry
        self.repo = config.repo
        self.resume = resume
        # Writes the interrupted attempt left in the tree, never gated and never
        # committed. They are neither charged to nor credited to the stage that runs
        # again — until a commit sweeps them up, the gates simply do not count them.
        self._leftovers: tuple[str, ...] = resume.dirty if resume else ()
        self.outcomes: list[StageOutcome] = []
        self.sessions: dict[str, AgentSession] = {}
        self.cost_usd = 0.0
        self.corrections = 0
        self.turns = 0
        self.tokens = 0
        self.checks_passed = False
        self.review_approved = False
        self.unresolved: list[str] = []
        self.branch: gitwork.RunBranch | None = None
        self.budget_stop = ""
        self._session_factory = session_factory or self._make_session

    # --- entry point -------------------------------------------------------

    def run(self) -> RunResult:
        self.tel.emit("phase_start", phase="run", detail=self.request[:400], title=self.request)
        self._open_record()
        for warning in self.cfg.warnings:
            self.tel.emit("phase_start", phase="run", result="warn", detail=warning)
        try:
            self._start_branch()
        except gitwork.GitError as exc:
            # Nothing has been spent and nothing has been written: refuse here rather
            # than commit a run's work into a branch that is not ours.
            return self._finish(f"could not start the run branch: {exc}", aborted=True)
        try:
            return self._run()
        except AgentError as exc:
            return self._finish(f"could not run the agent CLI: {exc}", aborted=True)

    @property
    def attempt(self) -> int:
        return self.resume.attempt if self.resume else 1

    def _open_record(self) -> None:
        """The pid goes on disk before the first agent turn: the moment you need a
        hung run's pid is the moment it has stopped emitting anything."""
        runs.open_record(
            self.cfg.run_dir,
            run_id=self.cfg.run_id,
            repo=self.repo,
            request=self.request,
            workflow=self.cfg.workflow,
            workflow_name=self.cfg.workflow_name,
            attempt=self.attempt,
        )
        self.tel.emit(
            "phase_start",
            phase="run",
            detail=f"pid {os.getpid()}, attempt {self.attempt}",
            payload={
                "pid": os.getpid(),
                "attempt": self.attempt,
                "run_dir": str(self.cfg.run_dir),
            },
        )

    def _start_branch(self) -> None:
        """Runs before the first stage snapshot, so no gate baseline can see it."""
        if self.resume is not None:
            plan = self.resume
            self.branch = gitwork.resume_run_branch(
                self.repo, plan.ref, plan.record.branch, plan.record.branch_origin
            )
            self._announce_resume()
        else:
            self.branch = gitwork.start_run_branch(self.repo, self.cfg.branch)
        self.tel.emit(
            "phase_start",
            phase="run",
            result="ok" if self.branch.created or self.branch.resumed else "warn",
            detail=branch_line(self.branch),
            payload={
                "branch": self.branch.name,
                "origin": self.branch.origin,
                "detached": self.branch.detached,
                "carried": list(self.branch.carried),
                "resumed": self.branch.resumed,
            },
        )
        runs.update(
            self.cfg.run_dir,
            branch=self.branch.name,
            branch_origin=self.branch.origin,
            base_sha=gitwork.head_sha(self.repo) or "",
        )

    def _announce_resume(self) -> None:
        """Say on the record what was trusted and why, before anything is spent."""
        plan = self.resume
        assert plan is not None
        self.tel.emit(
            "phase_start",
            phase="run",
            detail=(
                f"resumed run {self.cfg.run_id} (attempt {self.attempt}) on {plan.ref} "
                f"at {plan.tip[:8]} — reusing {len(plan.done)} committed stage(s), "
                f"continuing at {plan.first_stage or 'the end of the workflow'}"
            ),
            payload={
                "resume": {
                    "attempt": self.attempt,
                    "ref": plan.ref,
                    "tip": plan.tip,
                    "reused": {name: entry.commit for name, entry in plan.done.items()},
                    "continues_at": plan.first_stage,
                    "evidence": plan.evidence,
                    "uncommitted": list(plan.dirty),
                }
            },
        )

    def _run(self) -> RunResult:
        # What the next stage is handed: the last PRODUCER's envelope. A review is a
        # judge, not a producer — which is why `document` reads the builder's envelope
        # (as corrected by the review loop) and never the reviewer's.
        upstream: Envelope | None = None
        upstream_stage: str | None = None

        for name in self.cfg.workflow:
            done = self._already_done(name)
            if done is not None:
                upstream, upstream_stage = done.envelope, name
                continue

            if name == CHECKS:
                checks = self._record(self._checks_stage())
                if not checks.passed:
                    return self._finish(f"executed checks failed: {checks.detail}", aborted=True)
                continue

            if name == "review":
                upstream, approved, stopper = self._review_loop(upstream, upstream_stage)
                if stopper is not None:
                    return self._finish(
                        f"{stopper.name} stage {stopper.status}: {stopper.detail}", aborted=True
                    )
                if not approved:
                    return self._finish(
                        f"review did not approve within {self.cfg.max_review_rounds} round(s)",
                        aborted=False,
                    )
                continue

            outcome = self._record(
                self._agent_stage(name, previous=upstream, previous_stage=upstream_stage)
            )
            if not outcome.passed:
                return self._finish(
                    f"{name} stage {outcome.status}: {outcome.detail}", aborted=True
                )
            upstream, upstream_stage = outcome.envelope, name

        return self._finish(self._success_reason(), aborted=False)

    def _already_done(self, name: str) -> StageOutcome | None:
        """A stage a resumed run does not have to run again — with the evidence for
        it already checked against git before the run was allowed to start."""
        entry = self.resume.done.get(name) if self.resume else None
        if entry is None:
            return None
        detail = f"reused from attempt {self.resume.record.attempt}: {entry.commit[:8]}"
        payload = {"reused": True, "commit": entry.commit, "stage": name}
        self.tel.emit("phase_start", phase=name, agent=name, detail=detail, payload=payload)
        self.tel.emit(
            "phase_end", phase=name, agent=name, result="ok", detail=detail, payload=payload
        )
        return self._record(
            StageOutcome(
                name,
                REUSED,
                envelope=envelopes.from_raw(entry.envelope),
                commit=entry.commit,
                detail=detail,
            )
        )

    def _success_reason(self) -> str:
        """Only what this workflow actually did — no stage, no claim."""
        total = len(self.cfg.workflow)
        reused = [o.name for o in self.outcomes if o.status == REUSED]
        parts = [
            f"{total - len(reused)} stage(s) passed, {len(reused)} reused ({', '.join(reused)})"
            if reused
            else f"all {total} stage(s) passed"
        ]
        if self.cfg.runs_checks:
            parts.append("checks green")
        if self.cfg.runs_review:
            parts.append("review approved")
        return ", ".join(parts)

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
            conventions=self.cfg.conventions,
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
        self, build_envelope: Envelope | None, previous_stage: str | None = None
    ) -> tuple[Envelope | None, bool, StageOutcome | None]:
        """Rejected review → blocking list into the builder session → re-check → re-review.

        Returns the builder's envelope, not the reviewer's: corrections happen in the
        build session, so what the next stage should read is the corrected build.
        """
        for round_number in range(1, self.cfg.max_review_rounds + 1):
            review = self._record(
                self._agent_stage(
                    "review", previous=build_envelope, previous_stage=previous_stage
                )
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
            # Only re-run what this workflow runs at all: a shape with no `checks`
            # stage must not acquire one by way of the correction loop.
            if self.cfg.runs_checks:
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
            # Every agent turn in the run arrives here, so this is the one place a
            # cap has to be read — a single runaway turn is caught by the same check
            # as a slow drift across five stages.
            breach = self._budget_breach()
            if breach:
                outcome.status = BLOCKED
                outcome.corrections = corrections
                outcome.detail = breach
                self._note_budget_stop(stage.name, breach, attempt=corrections + 1)
                return outcome
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
            report.add(
                gates.gate_changed_files(
                    actual, envelope.changed_files, ignore=[*reverted, *self._leftovers]
                )
            )
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

    # --- budget caps -------------------------------------------------------

    def _budget_breach(self) -> str:
        """The stated figure and the cap it passed — never a bare "over budget"."""
        cost_cap = self.cfg.max_cost_usd
        if cost_cap is not None and self.cost_usd > cost_cap:
            return f"cost cap reached: ${self.cost_usd:.4f} of ${cost_cap:g} budget"
        token_cap = self.cfg.max_tokens
        if token_cap is not None and self.tokens > token_cap:
            return f"token cap reached: {self.tokens:,} of {token_cap:,} token budget"
        return ""

    def _note_budget_stop(self, phase: str, breach: str, *, attempt: int) -> None:
        """One stated verdict, on the same block every other gate uses."""
        self.budget_stop = breach
        self.tel.emit(
            "gate_fail",
            phase=phase,
            agent=phase,
            result="fail",
            detail=breach,
            payload={"gate": gates.BUDGET_GATE, "cost_usd": round(self.cost_usd, 6),
                     "tokens": self.tokens},
            gate=gates.GateCheck(gates.BUDGET_GATE, False, breach).block(attempt),
        )

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
        self.tokens += turn.input_tokens + turn.output_tokens
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
            # Written only now: a stage record that exists means the gates passed AND
            # the work is in git, which is exactly what --resume is allowed to skip.
            runs.save_stage(self.cfg.run_dir, stage_name, envelope.raw, sha)
            # `git add -A` has just swept the resumed leftovers into this commit, so
            # there is nothing left for the later gates to be told to overlook.
            self._leftovers = ()
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
        # A cap that fired owns the reason: whatever the stage machinery said next is
        # downstream of the stop, and the figure must not be buried behind it.
        if self.budget_stop:
            reason, aborted = self.budget_stop, True
        # Acceptance is not "every phase ran": every gate this workflow HAS must have
        # passed, and nothing may have aborted. A stage the workflow leaves out is not
        # a passed one — it drops out of acceptance and out of what the run may claim.
        accepted = (
            (not aborted)
            and (self.checks_passed or not self.cfg.runs_checks)
            and (self.review_approved or not self.cfg.runs_review)
        )
        result = RunResult(
            run_id=self.cfg.run_id,
            accepted=accepted,
            reason=reason,
            outcomes=list(self.outcomes),
            cost_usd=round(self.cost_usd, 6),
            corrections=self.corrections,
            turns=self.turns,
            tokens=self.tokens,
            unresolved=list(self.unresolved) if not accepted else [],
            telemetry_path=self.tel.path,
            branch=self.branch,
            budget_stop=self.budget_stop,
            left_uncommitted=bool(self.budget_stop) and gitwork.has_changes(self.repo),
            verified=self.cfg.verified,
            reviewed=self.cfg.runs_review,
            workflow=self.cfg.workflow,
            workflow_name=self.cfg.workflow_name,
            attempt=self.attempt,
            resumed=self.resume is not None,
        )
        runs.close_record(
            self.cfg.run_dir,
            # A budget stop is not the run finishing: it is a run that can be resumed,
            # and --list-runs must not describe it as one that ran its course.
            state=runs.STOPPED if self.budget_stop else runs.FINISHED,
            accepted=accepted,
            reason=reason,
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
        "reviewed": result.reviewed,
        "workflow": result.workflow_name,
        "workflow_stages": list(result.workflow),
        "cost_usd": result.cost_usd,
        "turns": result.turns,
        "corrections": result.corrections,
        "stages": stages,
    }


def branch_line(branch: gitwork.RunBranch) -> str:
    """One sentence naming where the commits are going, or why they are not moving."""
    if branch.resumed:
        where = branch.name or branch.origin
        return f"resumed on {where}, exactly where the interrupted run left it"
    if not branch.created:
        return f"no run branch — commits land on {branch.origin} ({branch.note})"
    started = f"detached HEAD {branch.origin[:8]}" if branch.detached else branch.origin
    line = f"run branch {branch.name}, created from {started}"
    if branch.carried:
        line += f", carrying {len(branch.carried)} uncommitted path(s) along"
    return line


def branch_report(result: RunResult) -> list[str]:
    """Where the work is. A user who cannot find the commits assumes there are none."""
    branch = result.branch
    if branch is None:
        return []
    commits = result.committed
    if not branch.created:
        where = f"{len(commits)} commit(s) on {branch.origin}" if commits else "nothing committed"
        return [f"branch: none — {where} ({branch.note})"]
    verb = "resumed, created from" if branch.resumed else "created from"
    lines = [f"branch: {branch.name} ({verb} {branch.origin}) — you are on it now"]
    if commits:
        landed = ", ".join(f"{n} {s[:8]}" for n, s in commits)
        lines.append(f"  {len(commits)} commit(s): {landed}")
        lines.append(f"  see the work:   git log {branch.origin}..{branch.name}")
    else:
        lines.append(f"  nothing was committed — {branch.name} is still exactly {branch.origin}")
    if branch.carried:
        lines.append(
            f"  {len(branch.carried)} path(s) you had uncommitted came along onto this branch: "
            + ", ".join(branch.carried[:5])
            + (" …" if len(branch.carried) > 5 else "")
        )
    lines.append(f"  back to where you started: {branch.return_command}")
    return lines


def resume_report(result: RunResult) -> list[str]:
    """What this attempt did not have to pay for again, and where it picked up."""
    if not result.resumed:
        return []
    lines = [f"RESUMED — attempt {result.attempt} of run {result.run_id}"]
    if result.reused:
        kept = ", ".join(
            f"{o.name} {o.commit[:8]}" for o in result.outcomes if o.status == REUSED and o.commit
        )
        lines.append(f"  reused (already committed): {kept}")
    else:
        lines.append("  reused: nothing — no stage had committed, so the run started over")
    ran = [o.name for o in result.outcomes if o.status != REUSED]
    lines.append(f"  ran this attempt: {', '.join(ran) or 'nothing'}")
    return lines


def budget_report(result: RunResult) -> list[str]:
    """What the cap stopped, and — the part that matters — what it did not undo."""
    if not result.budget_stop:
        return []
    lines = [f"STOPPED ON BUDGET — {result.budget_stop}"]
    committed = ", ".join(f"{n} {s[:8]}" for n, s in result.committed)
    lines.append(
        f"  kept (already committed): {committed}"
        if committed
        else "  kept: nothing had been committed yet"
    )
    if result.skipped:
        lines.append("  skipped: " + ", ".join(result.skipped))
    if result.left_uncommitted:
        # Never gated, so never committed — but it is still there to look at.
        lines.append("  the stopping stage's own changes are in the tree, uncommitted")
    return lines


def format_summary(result: RunResult) -> str:
    """Per-stage table so runs are comparable over time."""
    lines = [f"workflow: {result.workflow_name} — {workflows.describe(result.workflow)}", ""]
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
    lines += [
        "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip() for row in rows
    ]
    for block in (branch_report(result), resume_report(result), budget_report(result)):
        if block:
            lines += ["", *block]

    verdict = "ACCEPTED" if result.accepted else "NOT ACCEPTED"
    # What the run did NOT do rides the verdict itself, so no reader of this line
    # can take "ACCEPTED" for "built, verified and reviewed".
    done = ((result.verified, UNVERIFIED_VERDICT), (result.reviewed, UNREVIEWED_VERDICT))
    caveats = [text for flag, text in done if not flag]
    if caveats:
        verdict += f" ({'; '.join(caveats)})"
    lines.append("")
    lines.append(
        f"{verdict} — {result.reason} "
        f"({result.turns} turns, {result.corrections} corrections, ${result.cost_usd:.4f})"
    )
    if result.unresolved:
        lines.append("Unresolved blocking findings:")
        lines += [f"  - {item}" for item in result.unresolved]
    return "\n".join(lines)
