"""Read/write each agent's global instructions file.

Paths come from settings, never from the request — these files live outside the
provider roots, so there is nothing to path-validate and nothing else here can
ever be written.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.api.v1.instructions.schemas import InstructionsDoc
from app.core.exceptions import InstructionsIOError
from app.services.agent_cli import AgentId

_FILE_NAMES = {AgentId.CLAUDE: "CLAUDE.md", AgentId.CODEX: "AGENTS.md"}


@dataclass(frozen=True)
class InstructionsPaths:
    claude: Path
    codex: Path
    # Codex reads this instead of `codex` whenever it is non-empty.
    codex_override: Path

    def for_agent(self, agent: AgentId) -> Path:
        return self.codex if agent is AgentId.CODEX else self.claude


def _non_empty(path: Path) -> bool:
    try:
        return bool(path.read_text(encoding="utf-8", errors="replace").strip())
    except OSError:
        return False


def _same_file(a: Path, b: Path) -> bool:
    try:
        if a.exists() and b.exists():
            return os.path.samefile(a, b)
        return a.resolve() == b.resolve()
    except OSError:
        return False


def read_instructions(paths: InstructionsPaths, agent: AgentId) -> InstructionsDoc:
    path = paths.for_agent(agent)
    other = paths.for_agent(AgentId.CLAUDE if agent is AgentId.CODEX else AgentId.CODEX)
    shadowed = agent is AgentId.CODEX and _non_empty(paths.codex_override)
    common = {
        "agent": agent,
        "file_name": _FILE_NAMES[agent],
        "path": str(path),
        "shadowed_by": str(paths.codex_override) if shadowed else None,
        "same_file_as": str(other) if _same_file(path, other) else None,
    }
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        updated_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except FileNotFoundError:
        return InstructionsDoc(**common, content="", exists=False, updated_at=None)
    except OSError as exc:
        raise InstructionsIOError(f"could not read {path}: {exc.strerror or exc}") from exc
    return InstructionsDoc(**common, content=content, exists=True, updated_at=updated_at)


def write_instructions(paths: InstructionsPaths, agent: AgentId, content: str) -> InstructionsDoc:
    path = paths.for_agent(agent)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise InstructionsIOError(f"could not write {path}: {exc.strerror or exc}") from exc
    return read_instructions(paths, agent)
