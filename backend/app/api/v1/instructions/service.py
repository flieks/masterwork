"""Read/write the single global CLAUDE.md.

The path comes from settings, never from the request — this file lives outside
the provider roots, so there is nothing to path-validate and nothing else here
can ever be written.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.api.v1.instructions.schemas import InstructionsDoc
from app.core.exceptions import InstructionsIOError


def read_instructions(path: Path) -> InstructionsDoc:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        updated_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except FileNotFoundError:
        return InstructionsDoc(path=str(path), content="", exists=False, updated_at=None)
    except OSError as exc:
        raise InstructionsIOError(f"could not read {path}: {exc.strerror or exc}") from exc
    return InstructionsDoc(path=str(path), content=content, exists=True, updated_at=updated_at)


def write_instructions(path: Path, content: str) -> InstructionsDoc:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    except OSError as exc:
        raise InstructionsIOError(f"could not write {path}: {exc.strerror or exc}") from exc
    return read_instructions(path)
