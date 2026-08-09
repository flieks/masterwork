#!/usr/bin/env python3
"""CLI entry point for the agent-factory pipeline runner.

python3 factory/run.py [--repo PATH] [--config PATH] [--model X] [--dry-run] "request"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from adw import gitwork  # noqa: E402
from adw.config import STAGE_ORDER, ConfigError, FactoryConfig, load_config  # noqa: E402
from adw.pipeline import Pipeline, format_summary  # noqa: E402
from adw.telemetry import Telemetry  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="factory", description="Run a development request as a deterministic pipeline."
    )
    parser.add_argument("request", nargs="?", help="What to build, in one paragraph.")
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Target repo (default: cwd).")
    parser.add_argument("--config", type=Path, help="Path to factory.config.json.")
    parser.add_argument("--model", help="Override the model for every stage.")
    parser.add_argument("--max-corrections", type=int, help="Gate corrections per stage.")
    parser.add_argument("--max-review-rounds", type=int, help="Review→build rounds.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Print the resolved stages and exit."
    )
    parser.add_argument("--quiet", action="store_true", help="Do not echo telemetry events.")
    return parser


def stage_table(config: FactoryConfig, request: str) -> str:
    lines = [
        f"run {config.run_id} — dry run, no agent will be called",
        f"repo:    {config.repo}",
        f"request: {request or '(none given)'}",
        "",
    ]
    rows = [("STAGE", "MODEL", "BOUNDARY", "DISALLOWED TOOLS")]
    for name in STAGE_ORDER:
        stage = config.stages[name]
        rows.append(
            (
                name,
                stage.model or "— (runner-executed)",
                stage.boundary_text if name != "checks" else "(no agent)",
                ", ".join(stage.disallowed_tools) or "—",
            )
        )
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    lines += [
        "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip() for row in rows
    ]

    lines.append("")
    lines.append("checks (executed by the runner, never claimed by an agent):")
    lines += [f"  {i}. {cmd}" for i, cmd in enumerate(config.checks, 1)] or ["  (none)"]
    lines.append("")
    lines.append(f"max corrections/stage: {config.max_corrections}")
    lines.append(f"max review rounds:     {config.max_review_rounds}")
    lines.append(f"telemetry:             {config.run_dir / 'telemetry.jsonl'}")
    lines.append(f"telemetry POST:        {config.telemetry_url or '(disabled)'}")
    for warning in config.warnings:
        lines.append(f"warning: {warning}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = args.repo.resolve()

    if not repo.is_dir():
        print(f"error: no such directory: {repo}", file=sys.stderr)
        return 2
    if not gitwork.is_repo(repo):
        print(f"error: not a git repository: {repo}", file=sys.stderr)
        return 2

    try:
        config = load_config(
            repo,
            config_path=args.config,
            model_override=args.model,
            max_corrections=args.max_corrections,
            max_review_rounds=args.max_review_rounds,
        )
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        print(stage_table(config, args.request or ""))
        return 0

    if not args.request:
        print("error: a request is required (or use --dry-run)", file=sys.stderr)
        return 2

    telemetry = Telemetry(
        run_id=config.run_id,
        repo=config.repo,
        run_dir=config.run_dir,
        url=config.telemetry_url,
        context_window=config.context_window,
        echo=not args.quiet,
    )
    print(f"factory run {config.run_id} → {telemetry.path}")
    try:
        result = Pipeline(config, args.request, telemetry).run()
    finally:
        telemetry.close()
    print("\n" + format_summary(result))
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
