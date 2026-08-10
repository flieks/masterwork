#!/usr/bin/env python3
"""CLI entry point for the agent-factory pipeline runner.

python3 factory/run.py [--repo PATH] [--config PATH] [--model X] [--dry-run] "request"
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from adw import agent, gitwork, runs, workflows  # noqa: E402
from adw.config import (  # noqa: E402
    NO_CHECKS_REFUSAL,
    STARTUP_ERRORS,
    FactoryConfig,
    load_config,
    runs_root_for,
)
from adw.pipeline import UNVERIFIED_VERDICT, Pipeline, format_summary  # noqa: E402
from adw.roles import EDITED, ROLE_FILES, LibraryFile, RoleStore  # noqa: E402
from adw.telemetry import Telemetry  # noqa: E402

# 128 + SIGTERM, the shell's own convention for "this process was terminated".
EXIT_TERMINATED = 128 + int(signal.SIGTERM)
# How long --kill waits for the run to actually go away before reporting.
KILL_WAIT_SECONDS = 5.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="factory", description="Run a development request as a deterministic pipeline."
    )
    parser.add_argument("request", nargs="?", help="What to build, in one paragraph.")
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Target repo (default: cwd).")
    parser.add_argument("--config", type=Path, help="Path to factory.config.json.")
    parser.add_argument("--model", help="Override the model for every stage.")
    parser.add_argument(
        "--workflow",
        help=(
            "Which stages run, as a preset: "
            f"{workflows.preset_names()} (default: {workflows.DEFAULT_PRESET}). "
            'A custom list goes in factory.config.json as "workflow": ["build", "checks"].'
        ),
    )
    parser.add_argument("--max-corrections", type=int, help="Gate corrections per stage.")
    parser.add_argument("--max-review-rounds", type=int, help="Review→build rounds.")
    branching = parser.add_mutually_exclusive_group()
    branching.add_argument(
        "--branch",
        help=(
            "Name the run branch (default: factory/<run_id>). It is created from HEAD "
            "at run start and must not already exist."
        ),
    )
    branching.add_argument(
        "--no-branch",
        action="store_true",
        help="Commit onto the branch that is already checked out, as before.",
    )
    parser.add_argument(
        "--max-cost-usd",
        type=float,
        help="Stop the run once this much has been spent. Off by default.",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        help="Stop the run once this many tokens have been used. Off by default.",
    )
    parser.add_argument(
        "--no-checks",
        action="store_true",
        help="Run with no executed checks. The run is marked UNVERIFIED.",
    )
    parser.add_argument(
        "--runs-dir", help="Where run logs go (default: ~/.masterwork/runs/<repo>/<run_id>)."
    )
    parser.add_argument(
        "--roles-dir", help="The global role library (default: ~/.masterwork/agents)."
    )
    parser.add_argument(
        "--seed-roles",
        action="store_true",
        help="Write any missing default role files into the library. Never overwrites.",
    )
    parser.add_argument(
        "--refresh-roles",
        action="store_true",
        help=(
            "Report which library files still match the built-in default they were "
            "seeded from and which ones you have edited. Writes nothing on its own."
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="With --refresh-roles: update the files that carry no edit of yours.",
    )
    parser.add_argument(
        "--overwrite-edited",
        action="store_true",
        help="With --refresh-roles --apply: replace edited files too, keeping a .bak.",
    )
    parser.add_argument(
        "--resume",
        metavar="RUN_ID",
        help=(
            "Pick a previous run up where it stopped: same run id, same branch, "
            "skipping only the stages whose commit is provably on that branch."
        ),
    )
    parser.add_argument(
        "--list-runs",
        action="store_true",
        help="Show recent runs for this repo with their state and pid, then exit.",
    )
    parser.add_argument(
        "--kill",
        metavar="RUN_ID",
        help="SIGTERM a running run, after re-verifying the pid is still that run.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the resolved stages and exit."
    )
    parser.add_argument("--quiet", action="store_true", help="Do not echo telemetry events.")
    return parser


def branch_plan(config: FactoryConfig) -> str:
    """What the run would do to git, checked against the repo as it stands now."""
    here = gitwork.current_branch(config.repo) or "a detached HEAD"
    if config.branch is None:
        return f"none (--no-branch) — commits would land on {here}"
    if gitwork.head_sha(config.repo) is None:
        return f"none — this repo has no commits yet, so the run would work on {here}"
    if gitwork.branch_exists(config.repo, config.branch):
        return f"{config.branch} — ALREADY EXISTS, so the run would refuse to start"
    return f"{config.branch} — would be created from {here} at run start"


def stage_table(config: FactoryConfig, request: str) -> str:
    lines = [
        f"run {config.run_id} — dry run, no agent will be called",
        f"repo:     {config.repo}",
        f"request:  {request or '(none given)'}",
        f"workflow: {config.workflow_name} — {config.workflow_text}",
        "",
    ]
    rows = [("STAGE", "MODEL", "BOUNDARY", "DISALLOWED TOOLS")]
    for name in config.workflow:
        stage = config.stages[name]
        rows.append(
            (
                name,
                stage.model or "— (runner-executed)",
                stage.boundary_text if name != workflows.CHECKS else "(no agent)",
                ", ".join(stage.disallowed_tools) or "—",
            )
        )
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    lines += [
        "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip() for row in rows
    ]

    lines.append("")
    lines.append("checks (executed by the runner, never claimed by an agent):")
    if not config.runs_checks:
        lines.append(
            f"  (this workflow has no {workflows.CHECKS} stage — "
            f"the run would be {UNVERIFIED_VERDICT})"
        )
    else:
        lines += [f"  {i}. {cmd}" for i, cmd in enumerate(config.checks, 1)] or [
            f"  (none — this run would be {UNVERIFIED_VERDICT})"
        ]
    lines.append("")
    lines.append("roles (first hit wins: repo override → global library → built-in):")
    lines.append(f"  repo:   {config.project_roles_dir}")
    lines.append(f"  global: {config.roles_dir}")
    for name in config.workflow:
        role = config.roles.get(name)
        if role is None:
            continue  # `checks` has no role files: it is the runner's own stage
        lines.append(f"  {name} [{role.layer_mix}]: {role.config.purpose or '(no purpose set)'}")
        for filename in ROLE_FILES:
            lines.append(f"    {filename:<10} {role.layers[filename]:<8} {role.sources[filename]}")

    lines.append("")
    sources = ", ".join(str(p) for p in config.conventions_sources) or "(none)"
    lines.append(f"conventions (shown to every role): {sources}")
    lines.append(f"branch:                {branch_plan(config)}")
    lines.append(f"budget:                {config.budget_text}")
    lines.append(f"max corrections/stage: {config.max_corrections}")
    lines.append(f"max review rounds:     {config.max_review_rounds}")
    lines.append(f"telemetry:             {config.run_dir / 'telemetry.jsonl'}")
    lines.append(f"telemetry POST:        {config.telemetry_url or '(disabled)'}")
    for warning in config.warnings:
        lines.append(f"warning: {warning}")
    return "\n".join(lines)


def _seed_roles(repo: Path, roles_dir: Path | None, *, explicit: bool) -> list[Path]:
    """First use plants a real, editable copy of the defaults; --seed-roles fills gaps."""
    store = RoleStore(repo, roles_dir=roles_dir)
    return store.seed() if explicit else store.seed_if_new()


_STATE_NOTE = {
    "current": "identical to the built-in — nothing to do",
    "pristine": "never edited here — safe to update",
    "edited": "your edit — kept unless you say otherwise",
    "missing": "not in the library — the built-in is being used instead",
}


def _refresh_table(entries: list[LibraryFile]) -> list[str]:
    rows = [("STATE", "FILE", "")] + [(e.state, e.key, _STATE_NOTE[e.state]) for e in entries]
    widths = [max(len(row[i]) for row in rows) for i in range(2)]
    return [
        f"  {row[0].ljust(widths[0])}  {row[1].ljust(widths[1])}  {row[2]}".rstrip() for row in rows
    ]


def refresh_roles(store: RoleStore, *, apply: bool, overwrite_edited: bool) -> str:
    """Report the library against the built-ins, and update only what nobody edited."""
    entries = store.audit()
    lines = [f"role library: {store.global_dir}", ""]
    lines += _refresh_table(entries)

    edited = [e for e in entries if e.state == EDITED]
    stale = [e for e in entries if e.state not in ("current", EDITED)]
    if not apply:
        lines.append("")
        lines.append(
            f"{len(stale)} file(s) can be updated without touching your work"
            + (" — factory --refresh-roles --apply" if stale else "")
        )
        lines += _diff_section(edited, "would be left alone")
        return "\n".join(lines)

    updated, skipped, backed_up = store.refresh(overwrite_edited=overwrite_edited)
    lines.append("")
    lines.append(f"updated {len(updated)} file(s) in {store.global_dir}")
    lines += [f"  {entry.key}" for entry in updated]
    for entry in backed_up:
        lines.append(f"  kept your previous {entry.key} as {entry.backup.name}")
    lines += _diff_section(skipped, "were NOT touched")
    return "\n".join(lines)


def _diff_section(edited: list[LibraryFile], verb: str) -> list[str]:
    """An edited file is never replaced on a guess — here is exactly what differs."""
    if not edited:
        return []
    lines = ["", f"{len(edited)} edited file(s) {verb}; replace them with "]
    lines[-1] += "--refresh-roles --apply --overwrite-edited (a .bak is kept)"
    for entry in edited:
        lines.append("")
        lines.append(entry.diff.rstrip() or f"(no textual diff for {entry.key})")
    return lines


def format_runs(root: Path, records: list[runs.RunRecord]) -> str:
    """State is what the operating system says, not what the record last managed to
    write: a run whose process is gone is stopped, however it died."""
    if not records:
        return f"no runs recorded under {root}"
    rows = [("RUN", "STATE", "PID", "TRY", "STARTED", "BRANCH", "REQUEST")]
    for record in records:
        state = runs.live_state(record)
        live = state == runs.RUNNING and record.pid
        rows.append(
            (
                record.run_id,
                state,
                str(record.pid) if live else "-",
                str(record.attempt),
                record.started[:19],
                record.branch or "-",
                (record.reason or record.request)[:48],
            )
        )
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    lines = [f"runs in {root}", ""]
    lines += [
        "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip() for row in rows
    ]
    return "\n".join(lines)


def list_runs(root: Path) -> int:
    print(format_runs(root, runs.list_runs(root)))
    return 0


def kill_run(root: Path, run_id: str) -> int:
    """Verify first, signal second — a stale pid file must never cost a bystander."""
    record = runs.read(root / run_id)
    if record is None:
        print(f"error: unknown run '{run_id}' — no record under {root / run_id}", file=sys.stderr)
        return 2
    refusal = runs.refusal_to_signal(record)
    if refusal:
        print(f"error: not signalling anything — {refusal}", file=sys.stderr)
        return 1
    pid = int(record.pid or 0)
    print(f"run {run_id}: pid {pid} verified as this run — {record.cmdline}")
    try:
        # Verified a second time inside terminate(): the process could have ended
        # between the check above and the signal, and a race must not cost a stranger.
        runs.terminate(record)
    except runs.RunError as exc:
        print(f"error: not signalling anything — {exc}", file=sys.stderr)
        return 1
    print(f"run {run_id}: SIGTERM sent to pid {pid}")

    deadline = time.monotonic() + KILL_WAIT_SECONDS
    while runs.alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    if runs.alive(pid):
        print(f"run {run_id}: pid {pid} is still alive {KILL_WAIT_SECONDS:g}s later", file=sys.stderr)
        return 1
    # The run's own handler normally writes this; do it here too for a run that died
    # before it could, so the record never keeps claiming a pid that is gone.
    if (runs.read(root / run_id) or record).state == runs.RUNNING:
        runs.close_record(
            root / run_id, state=runs.STOPPED, accepted=False, reason=f"killed (pid {pid})"
        )
    print(f"run {run_id}: process {pid} is gone, state is now stopped")
    return 0


def install_stop_handler(run_dir: Path) -> None:
    """SIGTERM stops the agent subprocess too, then records the stop and exits.

    Without this, terminating the runner leaves the `claude` it was blocked on
    running, and the record keeps claiming a pid that no longer exists.
    """

    def stop(signum: int, _frame: object) -> None:
        stopped = agent.terminate_live()
        runs.close_record(
            run_dir, state=runs.STOPPED, accepted=False, reason=f"terminated by signal {signum}"
        )
        print(
            f"\nfactory: terminated by signal {signum} "
            f"({stopped} agent process(es) stopped) — the run is resumable",
            file=sys.stderr,
            flush=True,
        )
        os._exit(EXIT_TERMINATED)

    signal.signal(signal.SIGTERM, stop)


def resume_workflow(record: runs.RunRecord) -> str | list[str]:
    """The shape the interrupted run had, by name when the name still means that."""
    stages = tuple(record.workflow)
    if workflows.PRESETS.get(record.workflow_name) == stages:
        return record.workflow_name
    return list(stages)


def resume_conflicts(args: argparse.Namespace) -> str:
    """Flags that would contradict the record --resume is reading from."""
    if args.request:
        return (
            "--resume takes the request from the recorded run — drop the request argument "
            "(start a new run if you want to ask for something else)"
        )
    if args.branch or args.no_branch:
        return "--resume lands on the branch the original run created; --branch/--no-branch cannot"
    if args.workflow:
        return "--resume replays the workflow the original run recorded; --workflow cannot change it"
    return ""


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = args.repo.resolve()

    # A flag that would have written something must never be silently ignored.
    if (args.apply or args.overwrite_edited) and not args.refresh_roles:
        print("error: --apply and --overwrite-edited only apply to --refresh-roles", file=sys.stderr)
        return 2
    if not repo.is_dir():
        print(f"error: no such directory: {repo}", file=sys.stderr)
        return 2
    if not gitwork.is_repo(repo):
        print(f"error: not a git repository: {repo}", file=sys.stderr)
        return 2

    # The registry commands answer from the run dirs alone: they must keep working
    # for a repo whose config or role library would refuse to start a run at all.
    try:
        root = runs_root_for(repo, args.config, args.runs_dir)
    except STARTUP_ERRORS as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.list_runs:
        return list_runs(root)
    if args.kill:
        return kill_run(root, args.kill)

    resume = None
    if args.resume:
        conflict = resume_conflicts(args)
        if conflict:
            print(f"error: {conflict}", file=sys.stderr)
            return 2
        try:
            resume = runs.plan_resume(repo, root / args.resume, args.resume)
        except (runs.RunError, gitwork.GitError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    def resolve() -> FactoryConfig:
        record = resume.record if resume else None
        return load_config(
            repo,
            config_path=args.config,
            model_override=args.model,
            run_id=record.run_id if record else None,
            max_corrections=args.max_corrections,
            max_review_rounds=args.max_review_rounds,
            no_checks=args.no_checks,
            runs_dir=args.runs_dir,
            roles_dir=args.roles_dir,
            workflow=resume_workflow(record) if record else args.workflow,
            branch=record.branch if record else args.branch,
            no_branch=(record.branch is None) if record else args.no_branch,
            max_cost_usd=args.max_cost_usd,
            max_tokens=args.max_tokens,
        )

    try:
        config = resolve()
        # Seeding needs the resolved library path, so it happens after the first
        # load and the config is re-resolved to pick the new files up this run.
        seeded = _seed_roles(repo, config.roles_dir, explicit=args.seed_roles)
        if seeded:
            config = resolve()
    except STARTUP_ERRORS as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if seeded:
        print(f"seeded {len(seeded)} default role file(s) into {config.roles_dir} — edit them")
    elif args.seed_roles:
        print(f"role library already complete at {config.roles_dir} — nothing written")

    if args.refresh_roles:
        try:
            print(
                refresh_roles(
                    RoleStore(repo, roles_dir=config.roles_dir),
                    apply=args.apply,
                    overwrite_edited=args.overwrite_edited,
                )
            )
        except STARTUP_ERRORS as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return 0

    request = resume.record.request if resume else (args.request or "")
    if not args.dry_run and not request:
        if args.seed_roles:
            return 0
        print("error: a request is required (or use --dry-run)", file=sys.stderr)
        return 2

    # Refuse before any agent is called: a run that verifies nothing must not
    # be able to print ACCEPTED, and --dry-run must not paint it as fine either.
    if config.undetectable_checks:
        print(f"error: {NO_CHECKS_REFUSAL}", file=sys.stderr)
        return 2

    if args.dry_run:
        print(stage_table(config, request))
        return 0

    telemetry = Telemetry(
        run_id=config.run_id,
        repo=config.repo,
        run_dir=config.run_dir,
        url=config.telemetry_url,
        context_window=config.context_window,
        echo=not args.quiet,
        # Same run id means the same masterwork session, so this attempt's phases
        # must be numbered after the ones the interrupted attempt already reported.
        seq_start=runs.next_seq(config.run_dir) if resume else 1,
    )
    if resume:
        print(f"resuming run {config.run_id} (attempt {resume.attempt}) on {resume.ref}")
        for line in resume.evidence:
            print(f"  {line}")
    print(f"factory run {config.run_id} → {telemetry.path}")
    if not config.verified:
        print(f"warning: {UNVERIFIED_VERDICT}; nothing it produces will be verified")
    install_stop_handler(config.run_dir)
    try:
        result = Pipeline(config, request, telemetry, resume=resume).run()
    finally:
        telemetry.close()
    print("\n" + format_summary(result))
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
