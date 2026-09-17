"""Which coding-agent CLIs are installed on this machine, and where.

A backend started by launchd or a desktop app inherits a minimal PATH, and Codex
usually ships inside an app bundle that is never on PATH at all, so a binary is
looked up on an extended PATH first and then at its known install locations.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from pathlib import Path

from app.config import Settings


class AgentId(StrEnum):
    CLAUDE = "claude"
    CODEX = "codex"


AGENT_LABELS: dict[AgentId, str] = {AgentId.CLAUDE: "Claude Code", AgentId.CODEX: "Codex"}

# Preference order when the user has not picked one.
AGENT_ORDER: tuple[AgentId, ...] = (AgentId.CLAUDE, AgentId.CODEX)

# Resolved binary per agent; None when it is not installed.
AgentBins = Mapping[AgentId, str | None]


def extra_path_dirs(home: Path) -> tuple[Path, ...]:
    """Per-user install dirs a launchd-started process never has on PATH."""
    return (
        home / ".local" / "bin",
        home / ".claude" / "local",
        Path("/opt/homebrew/bin"),
        Path("/usr/local/bin"),
    )


def search_path(
    base: str | None = None, *, home: Path | None = None, extra: Iterable[Path] = ()
) -> str:
    """`base` (default: this process's PATH) plus the per-user dirs and `extra`
    that exist, without duplicates."""
    parts = [
        p for p in (os.environ.get("PATH", "") if base is None else base).split(os.pathsep) if p
    ]
    for directory in (*extra_path_dirs(home or Path.home()), *extra):
        if directory.is_dir() and str(directory) not in parts:
            parts.append(str(directory))
    return os.pathsep.join(parts)


def fallback_bins(agent: AgentId, home: Path) -> tuple[Path, ...]:
    """Install locations that are not on any PATH."""
    if agent is AgentId.CODEX:
        return (
            Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
            Path("/Applications/Codex.app/Contents/Resources/codex"),
            home / ".local" / "bin" / "codex",
        )
    return (home / ".claude" / "local" / "claude",)


def resolve_bin(name: str, fallbacks: Sequence[Path], *, path: str | None = None) -> str | None:
    """`name` on `path` (default: the extended PATH), else the first executable
    fallback, else None."""
    found = shutil.which(name, path=search_path() if path is None else path)
    if found:
        return found
    for candidate in fallbacks:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def detect_agent_bins(settings: Settings, *, home: Path | None = None) -> dict[AgentId, str | None]:
    """Every known agent's resolved binary. Tests patch this so none ever looks
    at the real machine."""
    home = home or Path.home()
    return {
        AgentId.CLAUDE: resolve_bin(
            settings.claude_bin,
            fallback_bins(AgentId.CLAUDE, home),
            path=search_path(home=home),
        ),
        AgentId.CODEX: resolve_bin(
            settings.codex_bin,
            fallback_bins(AgentId.CODEX, home),
            path=search_path(home=home),
        ),
    }


def configured_bin(agent: AgentId, settings: Settings) -> str:
    return settings.codex_bin if agent is AgentId.CODEX else settings.claude_bin


def effective_agent(stored: str | None, bins: AgentBins) -> AgentId:
    """The stored choice when it names a known agent (even one since uninstalled —
    silently switching agents would surprise more than a launch error); otherwise
    the first installed agent, and Claude when none is."""
    if stored is not None and stored in {agent.value for agent in AgentId}:
        return AgentId(stored)
    for agent in AGENT_ORDER:
        if bins.get(agent):
            return agent
    return AgentId.CLAUDE
