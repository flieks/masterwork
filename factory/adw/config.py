"""Resolved run configuration: built-in defaults overlaid with the repo's factory.config.json."""

from __future__ import annotations

import json
import secrets
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from adw.roles import ResolvedRole, RoleError, RoleStore
from adw.telemetry import DEFAULT_CONTEXT_WINDOW

CONFIG_FILENAME = "factory.config.json"

# `checks` sits between build and review because a reviewer should never be the
# first thing to learn the suite is red.
STAGE_ORDER = ("plan", "build", "checks", "review", "document")
AGENT_STAGES = ("plan", "build", "review", "document")

# Last-resort fallbacks. In practice the built-in role.json layer carries the same
# values, so these only matter if a hand-written role.json omits a key.
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

# Run logs are the runner's own output — they belong to the runner, not to the
# repo it is operating on.
DEFAULT_RUNS_ROOT = Path.home() / ".masterwork" / "runs"

DETECTED = "detected"  # auto-detected from the repo's shape
CONFIGURED = "configured"  # an explicit "checks" array in factory.config.json
OPTED_OUT = "opted-out"  # --no-checks

NO_CHECKS_REFUSAL = (
    "nothing to verify: this repo has no pyproject.toml and no package.json, so no "
    "quality commands could be auto-detected. A run with zero checks proves nothing, "
    "so the factory will not start one by accident.\n"
    f'Add a "checks" array to {CONFIG_FILENAME} (e.g. {{"checks": ["make test"]}}), '
    "or pass --no-checks to run explicitly unverified."
)
UNVERIFIED_WARNING = (
    "this run executes NO checks — nothing it produces will be verified by the runner"
)


class ConfigError(Exception):
    """factory.config.json is missing, unreadable, or malformed."""


# A broken role file is a configuration problem too, so callers catch both as one.
STARTUP_ERRORS = (ConfigError, RoleError)


@dataclass(frozen=True)
class Stage:
    name: str
    model: str | None  # None for `checks`, which runs no agent
    boundary: list[str] | None  # None = unrestricted, [] = read-only
    disallowed_tools: tuple[str, ...] = ()

    @property
    def read_only(self) -> bool:
        """`[]` permits nothing; `None` is unrestricted, not empty. Decoded once, here."""
        return self.boundary is not None and not self.boundary

    @property
    def boundary_text(self) -> str:
        if self.boundary is None:
            return "(unrestricted)"
        if self.read_only:
            return "(read-only)"
        return ", ".join(self.boundary)


@dataclass(frozen=True)
class FactoryConfig:
    repo: Path
    run_id: str
    run_dir: Path
    stages: dict[str, Stage]
    checks: list[str]
    checks_source: str
    max_corrections: int
    max_review_rounds: int
    telemetry_url: str | None
    artifact_max_bytes: int
    timeout_seconds: int
    context_window: int
    claude_bin: str
    warnings: tuple[str, ...] = field(default=())
    # The resolved role store: one entry per agent stage, `checks` has none.
    roles: dict[str, ResolvedRole] = field(default_factory=dict)
    roles_dir: Path | None = None
    project_roles_dir: Path | None = None

    @property
    def verified(self) -> bool:
        """False when the run executes no checks — it can prove nothing."""
        return bool(self.checks)

    @property
    def undetectable_checks(self) -> bool:
        """Auto-detection came up empty and nobody opted out: refuse to start."""
        return self.checks_source == DETECTED and not self.checks

    @property
    def run_dir_exclusions(self) -> tuple[str, ...]:
        """Repo-relative prefix of the run dir, only when `runs_dir` points back
        inside the target repo — otherwise the logs cannot pollute a gate diff."""
        try:
            relative = self.run_dir.relative_to(self.repo)
        except ValueError:
            return ()
        return (relative.as_posix().rstrip("/") + "/",)


def new_run_id() -> str:
    return secrets.token_hex(4)


def detect_checks(repo: Path) -> list[str]:
    """Auto-detected quality commands, by repo shape. Empty means undetectable."""
    commands: list[str] = []
    if (repo / "pyproject.toml").is_file():
        commands += PY_CHECKS
    if (repo / "package.json").is_file():
        commands += NODE_CHECKS
    return commands


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


def _agent_stage(
    name: str,
    role: ResolvedRole,
    models: dict[str, str],
    boundaries: dict[str, list[str] | None],
    model_override: str | None,
) -> Stage:
    """Precedence: CLI flag > factory.config.json > role.json > built-in default."""
    return Stage(
        name=name,
        model=model_override or models.get(name) or role.config.model or DEFAULT_MODELS[name],
        boundary=(
            boundaries[name]
            if name in boundaries
            else (role.config.writes if role.config.writes_set else DEFAULT_BOUNDARIES[name])
        ),
        disallowed_tools=(
            role.config.disallowed_tools
            if role.config.disallowed_tools is not None
            else _disallowed_for(name)
        ),
    )


def resolve_runs_dir(repo: Path, run_id: str, configured: object, override: object) -> Path:
    """`<runs root>/<run_id>`; the root defaults outside the target repo entirely."""
    for candidate in (override, configured):
        if candidate is None:
            continue
        if not isinstance(candidate, str | Path) or not str(candidate).strip():
            raise ConfigError('"runs_dir" must be a non-empty path string')
        root = Path(str(candidate)).expanduser()
        # A relative override is relative to the repo, so `factory/runs` still works.
        return (root if root.is_absolute() else repo / root) / run_id
    return DEFAULT_RUNS_ROOT / repo.name / run_id


def _roles_dir(repo: Path, configured: object, override: object) -> Path | None:
    """None means "wherever the store defaults to" — $MASTERWORK_ROLES_DIR or ~/.masterwork."""
    for candidate in (override, configured):
        if candidate is None:
            continue
        if not isinstance(candidate, str | Path) or not str(candidate).strip():
            raise ConfigError('"roles_dir" must be a non-empty path string')
        root = Path(str(candidate)).expanduser()
        return root if root.is_absolute() else repo / root
    return None


def load_config(
    repo: Path,
    *,
    config_path: Path | None = None,
    model_override: str | None = None,
    run_id: str | None = None,
    max_corrections: int | None = None,
    max_review_rounds: int | None = None,
    no_checks: bool = False,
    runs_dir: Path | str | None = None,
    roles_dir: Path | str | None = None,
) -> FactoryConfig:
    """Role store, then factory.config.json, then CLI overrides (last wins)."""
    repo = repo.resolve()
    data = _overlay(repo, config_path)
    warnings: list[str] = []

    store = RoleStore(repo, roles_dir=_roles_dir(repo, data.get("roles_dir"), roles_dir))
    roles = store.resolve_all()
    warnings += store.startup_warnings(roles)

    # Only what the file actually said: absent is not the same as "set to the default".
    models: dict[str, str] = {}
    raw_models = data.get("models", {})
    if not isinstance(raw_models, dict):
        raise ConfigError('"models" must be an object')
    for name, value in raw_models.items():
        if name not in DEFAULT_MODELS:
            warnings.append(f'{CONFIG_FILENAME}: unknown stage "{name}" in "models" — ignored')
            continue
        models[name] = str(value)

    boundaries: dict[str, list[str] | None] = {}
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

    if no_checks:
        checks, checks_source = [], OPTED_OUT
        warnings.append(f"--no-checks: {UNVERIFIED_WARNING}")
    elif "checks" in data and data["checks"] is not None:
        checks, checks_source = _as_commands(data["checks"], "checks"), CONFIGURED
        if not checks:
            warnings.append(f'{CONFIG_FILENAME} "checks" is explicitly empty — {UNVERIFIED_WARNING}')
    else:
        checks, checks_source = detect_checks(repo), DETECTED

    stages = {
        name: (
            Stage(name=name, model=None, boundary=None, disallowed_tools=())
            if name == "checks"
            else _agent_stage(name, roles[name], models, boundaries, model_override)
        )
        for name in STAGE_ORDER
    }

    telemetry_url = data.get("telemetry_url", DEFAULT_TELEMETRY_URL)
    if telemetry_url is not None and not isinstance(telemetry_url, str):
        raise ConfigError('"telemetry_url" must be a string or null')

    resolved_run_id = run_id or new_run_id()
    return FactoryConfig(
        repo=repo,
        run_id=resolved_run_id,
        run_dir=resolve_runs_dir(repo, resolved_run_id, data.get("runs_dir"), runs_dir),
        stages=stages,
        checks=checks,
        checks_source=checks_source,
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
        roles=roles,
        roles_dir=store.global_dir,
        project_roles_dir=store.project_dir,
    )


def _positive(override: int | None, configured: object, default: int, name: str) -> int:
    for candidate in (override, configured):
        if candidate is None:
            continue
        if not isinstance(candidate, int) or isinstance(candidate, bool) or candidate < 0:
            raise ConfigError(f'"{name}" must be a non-negative integer')
        return candidate
    return default
