"""Resolved run configuration: built-in defaults overlaid with the repo's factory.config.json.

Which CLI runs the agent stages is `"agent"`: `claude` (default) or `codex`, and
`--agent` wins over the file. Models are per agent, each keyed by stage: `"models"`
names Claude models (role.json `model` is their fallback), `"codex_models"` names
Codex ones, and a Codex stage with none omits `-m` so ~/.codex/config.toml decides.
`"codex_reasoning_effort"` is one string or a stage → string map. `"claude_bin"` and
`"codex_bin"` pin the binaries. `--model` overrides every stage on either agent.
"""

from __future__ import annotations

import json
import re
import secrets
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from adw import workflows
from adw.agent import AGENTS, CODEX, DEFAULT_AGENT
from adw.roles import READ_ONLY_ROLES, ROLES, ResolvedRole, RoleError, RoleStore
from adw.telemetry import DEFAULT_CONTEXT_WINDOW
from adw.workflows import CHECKS, WorkflowError

CONFIG_FILENAME = "factory.config.json"

# The default shape. Every shape lives in adw/workflows.py now, including this one.
STAGE_ORDER = workflows.FULL
AGENT_STAGES = ROLES

# Last-resort fallbacks. In practice the built-in role.json layer carries the same
# values, so these only matter if a hand-written role.json omits a key.
DEFAULT_MODELS = {
    "plan": "opus",
    "build": "sonnet",
    "review": "opus",
    "document": "sonnet",
    "scout": "sonnet",
}
DEFAULT_BOUNDARIES: dict[str, list[str] | None] = {
    "plan": ["plan.md", "docs/specs/**"],
    "build": None,  # unrestricted within the repo
    "review": [],  # read-only
    "document": ["docs/**", "README.md", "CHANGELOG.md"],
    "scout": [],  # read-only
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

# Each run gets its own branch: committing onto whatever the user had checked out
# is the more surprising of the two behaviours, so it costs an explicit opt-out.
DEFAULT_BRANCH_PREFIX = "factory/"

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


# A broken role file and an impossible workflow are configuration problems too, so
# callers catch all three as one and exit 2 before any agent runs.
STARTUP_ERRORS = (ConfigError, RoleError, WorkflowError)


@dataclass(frozen=True)
class Stage:
    name: str
    model: str | None  # None for `checks`, which runs no agent
    boundary: list[str] | None  # None = unrestricted, [] = read-only
    disallowed_tools: tuple[str, ...] = ()
    reasoning_effort: str | None = None  # Codex only

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
    # The branch this run creates and works on; None means "stay where we are".
    branch: str | None
    # Budget caps. None = uncapped, which is the default: a cap nobody asked for
    # would stop runs that are behaving exactly as they always have.
    max_cost_usd: float | None
    max_tokens: int | None
    telemetry_url: str | None
    artifact_max_bytes: int
    timeout_seconds: int
    # None when nothing is known about the window (a Codex model nobody configured).
    context_window: int | None
    claude_bin: str
    warnings: tuple[str, ...] = field(default=())
    agent: str = DEFAULT_AGENT
    codex_bin: str = "codex"
    # The resolved role store: one entry per agent stage, `checks` has none.
    roles: dict[str, ResolvedRole] = field(default_factory=dict)
    roles_dir: Path | None = None
    project_roles_dir: Path | None = None
    # The shape of this run: which stages, in which order, and what to call it.
    workflow: tuple[str, ...] = STAGE_ORDER
    workflow_name: str = workflows.DEFAULT_PRESET
    # The shared conventions block every role is shown; empty when no file exists.
    conventions: str = ""
    conventions_sources: tuple[Path, ...] = ()

    @property
    def runs_checks(self) -> bool:
        """Whether this workflow contains the one stage the runner executes itself."""
        return CHECKS in self.workflow

    @property
    def runs_review(self) -> bool:
        return "review" in self.workflow

    @property
    def verified(self) -> bool:
        """False when the run executes no checks — it can prove nothing. A workflow
        without a `checks` stage is exactly as unverified as `--no-checks`, and says
        so through this same flag rather than a second concept."""
        return self.runs_checks and bool(self.checks)

    @property
    def undetectable_checks(self) -> bool:
        """Auto-detection came up empty and nobody opted out: refuse to start.
        Only a workflow that would actually run the checks has anything to refuse."""
        return self.runs_checks and self.checks_source == DETECTED and not self.checks

    @property
    def workflow_text(self) -> str:
        return workflows.describe(self.workflow)

    @property
    def budget_text(self) -> str:
        """What the caps are, in the words the breach message will use."""
        parts = []
        if self.max_cost_usd is not None:
            parts.append(f"${self.max_cost_usd:g}")
        if self.max_tokens is not None:
            parts.append(f"{self.max_tokens:,} tokens")
        return " and ".join(parts) or "(uncapped)"

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


_RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def validate_run_id(run_id: str, runs_root: Path) -> None:
    """A caller-supplied run id becomes a path segment (`<runs_root>/<run_id>`),
    so it is checked before it is ever joined to one: non-empty, <=64 chars,
    `[A-Za-z0-9._-]` only, not `.`/`..`, and not already claimed."""
    if (
        not run_id
        or len(run_id) > 64
        or run_id in (".", "..")
        or not _RUN_ID_RE.fullmatch(run_id)
    ):
        raise ConfigError(
            f"--run-id {run_id!r} must be 1-64 characters of letters, digits, '.', "
            "'_', '-' and not '.' or '..'"
        )
    if (runs_root / run_id / "run.json").exists():  # adw.runs.RECORD_FILENAME
        raise ConfigError(f"--run-id {run_id!r} is already in use under {runs_root}")


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
    if stage_name in READ_ONLY_ROLES:
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


def _codex_stage(
    name: str,
    role: ResolvedRole,
    models: dict[str, str],
    boundaries: dict[str, list[str] | None],
    model_override: str | None,
    efforts: dict[str, str],
) -> Stage:
    """Claude's model aliases and tool names mean nothing to Codex: the model comes from
    the flag or `codex_models` (else the CLI's own default), the policy from the boundary."""
    return Stage(
        name=name,
        model=model_override or models.get(name),
        boundary=(
            boundaries[name]
            if name in boundaries
            else (role.config.writes if role.config.writes_set else DEFAULT_BOUNDARIES[name])
        ),
        reasoning_effort=efforts.get(name),
    )


def _agent(override: object, configured: object) -> str:
    for candidate, source in ((override, "--agent"), (configured, f'{CONFIG_FILENAME} "agent"')):
        if candidate is None:
            continue
        if candidate not in AGENTS:
            raise ConfigError(f"{source} must be one of {', '.join(AGENTS)}, not {candidate!r}")
        return str(candidate)
    return DEFAULT_AGENT


def _stage_models(raw: object, key: str, warnings: list[str]) -> dict[str, str]:
    """Only what the file actually said: absent is not the same as "set to the default"."""
    if not isinstance(raw, dict):
        raise ConfigError(f'"{key}" must be an object')
    models: dict[str, str] = {}
    for name, value in raw.items():
        if name not in DEFAULT_MODELS:
            warnings.append(f'{CONFIG_FILENAME}: unknown stage "{name}" in "{key}" — ignored')
            continue
        models[name] = str(value)
    return models


def _efforts(raw: object, warnings: list[str]) -> dict[str, str]:
    """One effort for every stage, or a stage → effort map."""
    key = "codex_reasoning_effort"
    if raw is None:
        return {}
    if isinstance(raw, str) and raw.strip():
        return dict.fromkeys(DEFAULT_MODELS, raw.strip())
    if isinstance(raw, dict) and all(isinstance(v, str) and v.strip() for v in raw.values()):
        return _stage_models(raw, key, warnings)
    raise ConfigError(f'"{key}" must be a string or an object of stage → string')


def runs_root(repo: Path, configured: object, override: object) -> Path:
    """The directory whose children are run dirs — what --list-runs and --resume read."""
    for candidate in (override, configured):
        if candidate is None:
            continue
        if not isinstance(candidate, str | Path) or not str(candidate).strip():
            raise ConfigError('"runs_dir" must be a non-empty path string')
        root = Path(str(candidate)).expanduser()
        # A relative override is relative to the repo, so `factory/runs` still works.
        return root if root.is_absolute() else repo / root
    return DEFAULT_RUNS_ROOT / repo.name


def resolve_runs_dir(repo: Path, run_id: str, configured: object, override: object) -> Path:
    """`<runs root>/<run_id>`; the root defaults outside the target repo entirely."""
    return runs_root(repo, configured, override) / run_id


def runs_root_for(repo: Path, config_path: Path | None, override: object) -> Path:
    """The runs root without resolving roles or a workflow: --list-runs and --kill
    must work for a repo whose role library or config would refuse a real run."""
    return runs_root(repo, _overlay(repo, config_path).get("runs_dir"), override)


def _branch(run_id: str, configured: object, override: str | None, no_branch: bool) -> str | None:
    """`factory/<run_id>` unless told otherwise; None means "stay on this branch"."""
    if no_branch:
        return None
    for candidate in (override, configured):
        if candidate is None:
            continue
        if isinstance(candidate, bool):
            if not candidate:
                return None
            break  # `true` — branch, with the generated name
        if not isinstance(candidate, str) or not candidate.strip():
            raise ConfigError('"branch" must be a branch name, or true/false')
        return candidate.strip()
    return f"{DEFAULT_BRANCH_PREFIX}{run_id}"


def _cap(override: object, configured: object, name: str, *, whole: bool) -> float | None:
    """An optional budget cap. Absent stays absent — 0 would be a cap nobody can meet."""
    for candidate in (override, configured):
        if candidate is None:
            continue
        ok = isinstance(candidate, int) if whole else isinstance(candidate, int | float)
        if isinstance(candidate, bool) or not ok:
            raise ConfigError(f'"{name}" must be a {"whole number" if whole else "number"}')
        if candidate <= 0:
            raise ConfigError(f'"{name}" must be greater than 0 (omit it to run uncapped)')
        return int(candidate) if whole else float(candidate)
    return None


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
    workflow: str | list[str] | None = None,
    branch: str | None = None,
    no_branch: bool = False,
    max_cost_usd: float | None = None,
    max_tokens: int | None = None,
    agent: str | None = None,
) -> FactoryConfig:
    """Role store, then factory.config.json, then CLI overrides (last wins)."""
    repo = repo.resolve()
    data = _overlay(repo, config_path)
    warnings: list[str] = []

    # Before anything else: a shape that cannot run is refused with nothing spent.
    workflow_name, workflow_stages = workflows.resolve(
        workflow if workflow is not None else data.get("workflow"),
        source="--workflow" if workflow is not None else f'{CONFIG_FILENAME} "workflow"',
    )

    store = RoleStore(repo, roles_dir=_roles_dir(repo, data.get("roles_dir"), roles_dir))
    roles = store.resolve_all()
    # Only the roles this run will actually use: a warning about a role the workflow
    # never reaches is noise the real ones then hide behind.
    warnings += store.startup_warnings(
        {name: roles[name] for name in workflow_stages if name in roles}
    )

    resolved_agent = _agent(agent, data.get("agent"))
    models = _stage_models(data.get("models", {}), "models", warnings)
    codex_models = _stage_models(data.get("codex_models", {}), "codex_models", warnings)
    efforts = _efforts(data.get("codex_reasoning_effort"), warnings)

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

    if CHECKS not in workflow_stages:
        warnings.append(
            f'workflow "{workflow_name}" ({workflows.describe(workflow_stages)}) has no '
            f"{CHECKS} stage — {UNVERIFIED_WARNING}"
        )

    def agent_stage(name: str) -> Stage:
        if resolved_agent == CODEX:
            return _codex_stage(
                name, roles[name], codex_models, boundaries, model_override, efforts
            )
        return _agent_stage(name, roles[name], models, boundaries, model_override)

    stages = {
        name: (
            Stage(name=name, model=None, boundary=None, disallowed_tools=())
            if name == CHECKS
            else agent_stage(name)
        )
        for name in workflow_stages
    }

    telemetry_url = data.get("telemetry_url", DEFAULT_TELEMETRY_URL)
    if telemetry_url is not None and not isinstance(telemetry_url, str):
        raise ConfigError('"telemetry_url" must be a string or null')

    resolved_run_id = run_id or new_run_id()
    token_cap = _cap(max_tokens, data.get("max_tokens"), "max_tokens", whole=True)
    cost_cap = _cap(max_cost_usd, data.get("max_cost_usd"), "max_cost_usd", whole=False)
    if resolved_agent == CODEX and cost_cap is not None:
        warnings.append(
            f"max_cost_usd ${cost_cap:g} cannot be enforced on codex — it reports tokens, "
            "never a price — so "
            + ("only max_tokens caps this run" if token_cap is not None else "this run is uncapped")
        )
    # Claude's window is a known display default; a Codex model's is not, so say nothing.
    context_window = (
        _positive(None, data.get("context_window"), DEFAULT_CONTEXT_WINDOW, "context_window")
        if resolved_agent != CODEX or data.get("context_window") is not None
        else None
    )
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
        branch=_branch(resolved_run_id, data.get("branch"), branch, no_branch),
        max_cost_usd=cost_cap,
        max_tokens=None if token_cap is None else int(token_cap),
        telemetry_url=telemetry_url or None,
        artifact_max_bytes=_positive(
            None, data.get("artifact_max_bytes"), DEFAULT_ARTIFACT_MAX_BYTES, "artifact_max_bytes"
        ),
        timeout_seconds=_positive(
            None, data.get("timeout_seconds"), DEFAULT_TIMEOUT_SECONDS, "timeout_seconds"
        ),
        context_window=context_window,
        claude_bin=str(data.get("claude_bin") or "claude"),
        warnings=tuple(warnings),
        agent=resolved_agent,
        codex_bin=str(data.get("codex_bin") or "codex"),
        roles=roles,
        roles_dir=store.global_dir,
        project_roles_dir=store.project_dir,
        workflow=workflow_stages,
        workflow_name=workflow_name,
        conventions=store.conventions(),
        conventions_sources=store.conventions_sources,
    )


def _positive(override: int | None, configured: object, default: int, name: str) -> int:
    for candidate in (override, configured):
        if candidate is None:
            continue
        if not isinstance(candidate, int) or isinstance(candidate, bool) or candidate < 0:
            raise ConfigError(f'"{name}" must be a non-negative integer')
        return candidate
    return default
