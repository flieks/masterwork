"""Resolved run configuration: built-in defaults overlaid with the repo's factory.config.json."""

from __future__ import annotations

import json
import secrets
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from adw.telemetry import DEFAULT_CONTEXT_WINDOW

CONFIG_FILENAME = "factory.config.json"

# `checks` sits between build and review because a reviewer should never be the
# first thing to learn the suite is red.
STAGE_ORDER = ("plan", "build", "checks", "review", "document")
AGENT_STAGES = ("plan", "build", "review", "document")

DEFAULT_MODELS = {"plan": "opus", "build": "sonnet", "review": "opus", "document": "sonnet"}
DEFAULT_BOUNDARIES: dict[str, list[str] | None] = {
    "plan": ["plan.md", "docs/specs/**"],
    "build": None,  # unrestricted within the repo
    "review": [],  # read-only
    "document": ["docs/**", "README.md", "CHANGELOG.md"],
}

# No agent shells out: `checks` is the runner's job, and Task/web tools would
# smuggle unbounded context past the envelope contract.
BASE_DISALLOWED = ("Bash", "Task", "WebFetch", "WebSearch")
WRITE_TOOLS = ("Edit", "MultiEdit", "Write", "NotebookEdit")

PY_CHECKS = ["uv run pytest -q", "uv run ruff check ."]
NODE_CHECKS = ["npm run typecheck --if-present", "npm test --if-present"]

DEFAULT_TELEMETRY_URL = "http://localhost:8008/api/v1/hooks/events"
DEFAULT_ARTIFACT_MAX_BYTES = 20_000
DEFAULT_TIMEOUT_SECONDS = 1800


class ConfigError(Exception):
    """factory.config.json is missing, unreadable, or malformed."""


@dataclass(frozen=True)
class Stage:
    name: str
    model: str | None  # None for `checks`, which runs no agent
    boundary: list[str] | None  # None = unrestricted, [] = read-only
    disallowed_tools: tuple[str, ...] = ()

    @property
    def boundary_text(self) -> str:
        if self.boundary is None:
            return "(unrestricted)"
        if not self.boundary:
            return "(read-only)"
        return ", ".join(self.boundary)


@dataclass(frozen=True)
class FactoryConfig:
    repo: Path
    run_id: str
    stages: dict[str, Stage]
    checks: list[str]
    max_corrections: int
    max_review_rounds: int
    telemetry_url: str | None
    artifact_max_bytes: int
    timeout_seconds: int
    context_window: int
    claude_bin: str
    warnings: tuple[str, ...] = field(default=())

    @property
    def run_dir(self) -> Path:
        return self.repo / "factory" / "runs" / self.run_id


def new_run_id() -> str:
    return secrets.token_hex(4)


def detect_checks(repo: Path) -> tuple[list[str], list[str]]:
    """Auto-detected quality commands plus any warnings, by repo shape."""
    commands: list[str] = []
    if (repo / "pyproject.toml").is_file():
        commands += PY_CHECKS
    if (repo / "package.json").is_file():
        commands += NODE_CHECKS
    if not commands:
        return [], [
            "no pyproject.toml or package.json found — running with NO executed checks; "
            f'set "checks" in {CONFIG_FILENAME} to gate this run on real commands'
        ]
    return commands, []


def _overlay(repo: Path, config_path: Path | None) -> dict:
    path = config_path or (repo / CONFIG_FILENAME)
    if not path.is_file():
        if config_path is not None:
            raise ConfigError(f"config file not found: {path}")
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"could not read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a JSON object")
    return data


def _as_commands(raw: object, source: str) -> list[str]:
    """Checks may be given as strings or as pre-split argv lists."""
    if not isinstance(raw, list):
        raise ConfigError(f'"{source}" must be a list')
    out: list[str] = []
    for item in raw:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, list) and all(isinstance(p, str) for p in item):
            out.append(shlex.join(item))
        else:
            raise ConfigError(f'"{source}" entries must be strings or lists of strings')
    return out


def _disallowed_for(stage_name: str) -> tuple[str, ...]:
    if stage_name == "review":
        return BASE_DISALLOWED + WRITE_TOOLS
    return BASE_DISALLOWED


def load_config(
    repo: Path,
    *,
    config_path: Path | None = None,
    model_override: str | None = None,
    run_id: str | None = None,
    max_corrections: int | None = None,
    max_review_rounds: int | None = None,
) -> FactoryConfig:
    """Defaults, then factory.config.json, then CLI overrides (last wins)."""
    repo = repo.resolve()
    data = _overlay(repo, config_path)
    warnings: list[str] = []

    models = dict(DEFAULT_MODELS)
    raw_models = data.get("models", {})
    if not isinstance(raw_models, dict):
        raise ConfigError('"models" must be an object')
    for name, value in raw_models.items():
        if name not in DEFAULT_MODELS:
            warnings.append(f'{CONFIG_FILENAME}: unknown stage "{name}" in "models" — ignored')
            continue
        models[name] = str(value)

    boundaries = dict(DEFAULT_BOUNDARIES)
    raw_bounds = data.get("boundaries", {})
    if not isinstance(raw_bounds, dict):
        raise ConfigError('"boundaries" must be an object')
    for name, value in raw_bounds.items():
        if name not in DEFAULT_BOUNDARIES:
            warnings.append(f'{CONFIG_FILENAME}: unknown stage "{name}" in "boundaries" — ignored')
            continue
        if value is not None and not (
            isinstance(value, list) and all(isinstance(p, str) for p in value)
        ):
            raise ConfigError(f'boundary for "{name}" must be null or a list of glob strings')
        boundaries[name] = value

    if "checks" in data and data["checks"] is not None:
        checks = _as_commands(data["checks"], "checks")
        if not checks:
            warnings.append(f'{CONFIG_FILENAME}: "checks" is empty — this run executes no checks')
    else:
        checks, detect_warnings = detect_checks(repo)
        warnings += detect_warnings

    stages = {
        name: Stage(
            name=name,
            model=None if name == "checks" else (model_override or models[name]),
            boundary=None if name == "checks" else boundaries[name],
            disallowed_tools=() if name == "checks" else _disallowed_for(name),
        )
        for name in STAGE_ORDER
    }

    telemetry_url = data.get("telemetry_url", DEFAULT_TELEMETRY_URL)
    if telemetry_url is not None and not isinstance(telemetry_url, str):
        raise ConfigError('"telemetry_url" must be a string or null')

    return FactoryConfig(
        repo=repo,
        run_id=run_id or new_run_id(),
        stages=stages,
        checks=checks,
        max_corrections=_positive(
            max_corrections, data.get("max_corrections"), 2, "max_corrections"
        ),
        max_review_rounds=_positive(
            max_review_rounds, data.get("max_review_rounds"), 2, "max_review_rounds"
        ),
        telemetry_url=telemetry_url or None,
        artifact_max_bytes=_positive(
            None, data.get("artifact_max_bytes"), DEFAULT_ARTIFACT_MAX_BYTES, "artifact_max_bytes"
        ),
        timeout_seconds=_positive(
            None, data.get("timeout_seconds"), DEFAULT_TIMEOUT_SECONDS, "timeout_seconds"
        ),
        context_window=_positive(
            None, data.get("context_window"), DEFAULT_CONTEXT_WINDOW, "context_window"
        ),
        claude_bin=str(data.get("claude_bin") or "claude"),
        warnings=tuple(warnings),
    )


def _positive(override: int | None, configured: object, default: int, name: str) -> int:
    for candidate in (override, configured):
        if candidate is None:
            continue
        if not isinstance(candidate, int) or isinstance(candidate, bool) or candidate < 0:
            raise ConfigError(f'"{name}" must be a non-negative integer')
        return candidate
    return default
