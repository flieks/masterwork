"""The backend half of the factory's interview file contract
(factory/adw/interview.py): read-only for questions.json and run.json,
write-only for answers.json. No FastAPI here — this is filesystem plumbing.

Mirrors the factory's own runs-root rule (factory/adw/config.py:
`runs_root()`) so an interview run's files are found exactly where
`factory/run.py` itself would look for them: a repo's own
`factory.config.json` "runs_dir" when set, else
`<factory_runs_root>/<project dir name>`.
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings

CONFIG_FILENAME = "factory.config.json"
QUESTIONS_FILENAME = "questions.json"
ANSWERS_FILENAME = "answers.json"
RECORD_FILENAME = "run.json"


def new_run_id() -> str:
    """Same shape as factory/adw/config.py's new_run_id() — both are just an
    unpredictable, path-safe token; nothing requires the two to match bit for bit."""
    return secrets.token_hex(4)


def _configured_runs_dir(project_path: Path) -> str | None:
    config_path = project_path / CONFIG_FILENAME
    if not config_path.is_file():
        return None
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = data.get("runs_dir") if isinstance(data, dict) else None
    return value if isinstance(value, str) and value.strip() else None


def runs_root_for(project_path: Path) -> Path:
    """`factory.config.json`'s "runs_dir" (expanded, relative resolved against
    the project) when set, else `factory_runs_root / project dir name`."""
    configured = _configured_runs_dir(project_path)
    if configured is None:
        return settings.factory_runs_root / project_path.name
    root = Path(configured).expanduser()
    return root if root.is_absolute() else project_path / root


def run_dir_for(project_path: Path, run_id: str) -> Path:
    return runs_root_for(project_path) / run_id


def read_questions(run_dir: Path) -> list[dict[str, str]]:
    """`[]` when the file is absent or unreadable — a run that has not (yet, or
    ever) paused reads the same as one with nothing to ask."""
    path = run_dir / QUESTIONS_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    raw = data.get("questions") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    return [
        {"id": item["id"], "question": item["question"]}
        for item in raw
        if isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and isinstance(item.get("question"), str)
    ]


def answers_exist(run_dir: Path) -> bool:
    return (run_dir / ANSWERS_FILENAME).is_file()


def read_run_state(run_dir: Path) -> str | None:
    """`run.json`'s "state" field, or None when it is absent or unreadable."""
    path = run_dir / RECORD_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    state = data.get("state") if isinstance(data, dict) else None
    return state if isinstance(state, str) else None


def read_run_record(run_dir: Path) -> dict[str, object] | None:
    """The whole run.json dict, or None when absent/unreadable — the factory
    owns the file; this only ever reads it."""
    path = run_dir / RECORD_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def pid_alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, PermissionError):
        return False
    return True


def write_answers(run_dir: Path, pairs: list[dict[str, str]]) -> None:
    """Atomic tmp + os.replace, matching the factory's own writes. Refuses a
    missing run dir — a run dir that does not exist means the run never
    started, and inventing one would strand the answers nobody can read."""
    if not run_dir.is_dir():
        raise FileNotFoundError(f"no run directory at {run_dir}")
    payload = {"answered_at": datetime.now(UTC).isoformat(), "answers": pairs}
    path = run_dir / ANSWERS_FILENAME
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)
