"""Best-effort git snapshots of the shared asset tree (~/.claude).

The tree is version-controlled (skills/ + agents/ only, via .gitignore) so every
applied suggestion or proposal is diffable and revertible. No repo → silent
no-op (tests use tmp trees); a git failure must never fail the apply itself.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


async def commit_snapshot(root: Path, message: str) -> None:
    """`git add -A && git commit` in `root` if it is a git repo."""
    if not (root / ".git").is_dir():
        return
    try:
        for args in (
            ["git", "-C", str(root), "add", "-A"],
            # Exits non-zero when there is nothing to commit — that's fine.
            ["git", "-C", str(root), "commit", "-q", "-m", message],
        ):
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
    except OSError:
        logger.warning("git snapshot of %s failed", root, exc_info=True)
