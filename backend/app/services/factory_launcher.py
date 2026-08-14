"""Spawns `factory/run.py` as a detached, fire-and-forget subprocess.

The factory is always the vehicle for a launched session — never a bare
`claude` invocation — so a session started from the launcher self-attributes
the same way any other factory run does, via MASTERWORK_FACTORY_RUN_ID
(factory/adw/agent.py).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# Live child handles, reaped opportunistically on each new launch so a
# long-lived backend never accumulates zombies — nothing ever wait()s them.
_children: list[subprocess.Popen[bytes]] = []


def _reap() -> None:
    _children[:] = [p for p in _children if p.poll() is None]


def spawn_factory_run(
    *,
    repo_root: Path,
    python_bin: str,
    project_path: Path,
    request_text: str,
    log_path: Path,
) -> int:
    """Launch `python_bin factory/run.py --repo project_path request_text`,
    detached from this process and never awaited. Returns the child pid.

    argv is a list, never a shell string — request_text is untrusted and must
    never be interpreted. No mode flag: both launch modes run the same
    unattended pipeline this iteration (see plan.md).
    """
    _reap()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        python_bin,
        str(repo_root / "factory" / "run.py"),
        "--repo",
        str(project_path),
        request_text,
    ]
    with log_path.open("ab") as log:
        proc = subprocess.Popen(
            argv,
            cwd=str(project_path),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # detaches from this process's group
        )
    _children.append(proc)
    return proc.pid
