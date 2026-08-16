"""Spawns `factory/run.py` as a detached, fire-and-forget subprocess.

The factory is always the vehicle for a launched session — never a bare
`claude` invocation — so a session started from the launcher self-attributes
the same way any other factory run does, via MASTERWORK_FACTORY_RUN_ID
(factory/adw/agent.py).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

AGENT_CLI = "claude"

# A backend started from a desktop app or a launchd plist inherits a minimal
# PATH, while the agent CLI is installed per-user. The factory resolves it by
# name, so these are added to the child's PATH rather than guessed at here.
_EXTRA_PATH_DIRS = (
    Path.home() / ".local" / "bin",
    Path.home() / ".claude" / "local",
    Path("/opt/homebrew/bin"),
    Path("/usr/local/bin"),
)


def _child_path() -> str:
    parts = os.environ.get("PATH", "").split(os.pathsep)
    for extra in _EXTRA_PATH_DIRS:
        if extra.is_dir() and str(extra) not in parts:
            parts.append(str(extra))
    return os.pathsep.join(parts)


def find_agent_cli() -> str | None:
    """Where the child will find `claude`, or None when it would not — the
    factory dies in under a second without it, and a detached run has nowhere
    to say so."""
    return shutil.which(AGENT_CLI, path=_child_path())

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
) -> int:
    """Launch `python_bin factory/run.py --repo project_path request_text`,
    detached from this process and never awaited. Returns the child pid.

    argv is a list, never a shell string — request_text is untrusted and must
    never be interpreted. `--run-id`/`--interview` are appended only when set,
    so an autonomous launch's argv is unchanged to the byte.
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
    argv.append(request_text)
    return _spawn(argv, project_path, log_path)


def spawn_factory_resume(
    *,
    repo_root: Path,
    python_bin: str,
    project_path: Path,
    run_id: str,
    log_path: Path,
) -> int:
    """Launch `python_bin factory/run.py --repo project_path --resume run_id`,
    detached the same way spawn_factory_run is — the answers.json the caller
    just wrote is what this resumed run reads its questions' answers from."""
    argv = [
        python_bin,
        str(repo_root / "factory" / "run.py"),
        "--repo",
        str(project_path),
        "--resume",
        run_id,
    ]
    return _spawn(argv, project_path, log_path)


def _spawn(argv: list[str], project_path: Path, log_path: Path) -> int:
    _reap()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PATH": _child_path()}
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
