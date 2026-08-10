"""Provider abstraction over session-observability wiring.

An *integration* teaches one coding agent to post its lifecycle events to this
backend. Claude Code does it with hooks in `settings.json`; Codex, Cursor and
the rest will each have their own mechanism. What they share is the shape the
UI needs — can I connect it, is it connected, what would I be writing — so a new
agent is a new `Integration` implementation and a line in the registry.

Everything an integration writes lives in two places and no others: a forwarder
script under masterwork's own home, and the agent's own config file.
"""

from __future__ import annotations

import json
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

# connected  — wired up and pointing at a script that exists
# outdated   — wired up, but to a path that has moved or an older event set;
#              `connect()` repairs it in place
# disconnected — nothing of ours in the agent's config
# unavailable  — the agent (or a python to run the hook with) isn't on this
#                machine, so there is nothing to connect to
IntegrationState = Literal["connected", "outdated", "disconnected", "unavailable"]


@dataclass(frozen=True)
class IntegrationStatus:
    """What the Sessions screen shows for one agent, and everything it needs to
    explain what connecting would do before the user clicks."""

    id: str
    label: str
    state: IntegrationState
    detail: str
    ingest_url: str
    events: list[str]
    config_path: str | None = None
    script_path: str | None = None
    backup_path: str | None = None


@runtime_checkable
class Integration(Protocol):
    """One coding agent's observability wiring."""

    id: str
    label: str

    def status(self) -> IntegrationStatus:
        """Read the agent's config and report where things stand. Never writes."""
        ...

    def connect(self) -> IntegrationStatus:
        """Wire the agent up, or repair an outdated wiring. Idempotent, and it
        backs the agent's config up before touching it."""
        ...

    def disconnect(self) -> IntegrationStatus:
        """Remove every entry we own, leaving the rest of the config alone."""
        ...


def resolve_interpreter() -> str | None:
    """An absolute python3 for the hook command.

    Prefers a system interpreter over `sys.executable`: under `npx masterwork`
    the backend runs from a virtualenv inside npm's cache, and that path is
    swept away on the next prune while the agent's config would keep pointing
    at it. The forwarders are stdlib-only, so any python3 will do.
    """
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found:
            return str(Path(found).resolve())
    return sys.executable or None


def install_forwarder(source: Path, target_dir: Path, ingest_url: str) -> Path:
    """Copy a forwarder to its stable home and write the sidecar it reads.

    Overwrites on every connect, so upgrading masterwork upgrades the script an
    already-connected agent runs.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / source.name
    shutil.copyfile(source, target)
    target.chmod(target.stat().st_mode | stat.S_IXUSR)
    config = target_dir / "config.json"
    config.write_text(json.dumps({"ingest_url": ingest_url}, indent=2) + "\n", encoding="utf-8")
    return target


def forwarder_is_current(source: Path, target: Path) -> bool:
    """Is the installed copy the script this install of masterwork ships?

    Byte equality, not a version constant. `install_forwarder` copies the file
    verbatim and nothing else ever writes it, so a difference means the hook is
    running another version's code — which is exactly what an upgrade has to
    notice. The alternative, a version field bumped by hand, is one edit away
    from an upgrade that ships a new forwarder and reports itself already
    current: the hook keeps forwarding the old body while the backend waits for
    a field that will never arrive, and nothing anywhere says so.
    """
    try:
        return target.read_bytes() == source.read_bytes()
    except OSError:
        return False
