"""Spawns `factory/run.py` as a detached, fire-and-forget subprocess.

The factory is always the vehicle for a launched session — never a bare
agent CLI invocation — so a session started from the launcher self-attributes
the same way any other factory run does, via MASTERWORK_FACTORY_RUN_ID
(factory/adw/agent.py).
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

from app.services.agent_cli import search_path


def _child_path(agent_bins: Sequence[str]) -> str:
    """A backend started from a desktop app or a launchd plist inherits a minimal
    PATH, while the agent CLIs are installed per-user or inside an app bundle.
    The factory resolves its CLI by name, so those dirs go on the child's PATH."""
    return search_path(extra=[Path(bin_path).parent for bin_path in agent_bins])


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
    run_id: str | None = None,
    interview: bool = False,
    workflow: str | None = None,
    agent: str | None = None,
    agent_bins: Sequence[str] = (),
) -> int:
    """Launch `python_bin factory/run.py --repo project_path request_text`,
    detached from this process and never awaited. Returns the child pid.

    argv is a list, never a shell string — request_text is untrusted and must
    never be interpreted. `--run-id`/`--interview`/`--workflow`/`--agent` are
    appended only when set, so a plain launch's argv is unchanged to the byte.
    """
    argv = [
        python_bin,
        str(repo_root / "factory" / "run.py"),
        "--repo",
        str(project_path),
    ]
    if run_id is not None:
        argv += ["--run-id", run_id]
    if interview:
        argv.append("--interview")
    if workflow is not None:
        argv += ["--workflow", workflow]
    if agent is not None:
        argv += ["--agent", agent]
    argv.append(request_text)
    return _spawn(argv, project_path, log_path, agent_bins)


def spawn_factory_resume(
    *,
    repo_root: Path,
    python_bin: str,
    project_path: Path,
    run_id: str,
    log_path: Path,
    agent_bins: Sequence[str] = (),
) -> int:
    """Launch `python_bin factory/run.py --repo project_path --resume run_id`,
    detached the same way spawn_factory_run is — the answers.json the caller
    just wrote is what this resumed run reads its questions' answers from. No
    `--agent`: the run record remembers the agent it started with."""
    argv = [
        python_bin,
        str(repo_root / "factory" / "run.py"),
        "--repo",
        str(project_path),
        "--resume",
        run_id,
    ]
    return _spawn(argv, project_path, log_path, agent_bins)


def _spawn(
    argv: list[str], project_path: Path, log_path: Path, agent_bins: Sequence[str] = ()
) -> int:
    _reap()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PATH": _child_path(agent_bins)}
    with log_path.open("ab") as log:
        proc = subprocess.Popen(
            argv,
            cwd=str(project_path),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # detaches from this process's group
        )
    _children.append(proc)
    return proc.pid
