"""Terminal equivalent of the Sessions screen's Connect button.

    uv run python -m app.observability.cli status
    uv run python -m app.observability.cli connect [agent]

For headless installs and for anyone who would rather not click. The UI is the
main path; this shares its code, so the two can never drift.
"""

from __future__ import annotations

import argparse
import sys

from app.config import settings
from app.core.exceptions import DomainError
from app.observability.base import Integration, IntegrationStatus
from app.observability.registry import build_integrations


def _report(status: IntegrationStatus) -> None:
    print(f"{status.label}: {status.state}\n  {status.detail}")
    if status.state == "connected":
        print(f"  hooks: {status.config_path}\n  script: {status.script_path}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="masterwork-observability", description=__doc__)
    parser.add_argument("action", choices=["status", "connect", "disconnect"])
    parser.add_argument(
        "agent",
        nargs="?",
        help="Integration id (default: every agent masterwork knows about).",
    )
    args = parser.parse_args(argv)

    integrations: list[Integration] = build_integrations(settings)
    if args.agent:
        integrations = [i for i in integrations if i.id == args.agent]
        if not integrations:
            print(f"unknown agent: {args.agent}", file=sys.stderr)
            return 2

    failed = False
    for integration in integrations:
        try:
            if args.action == "connect":
                _report(integration.connect())
            elif args.action == "disconnect":
                _report(integration.disconnect())
            else:
                _report(integration.status())
        except DomainError as exc:
            print(f"{integration.label}: {exc.detail}", file=sys.stderr)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
