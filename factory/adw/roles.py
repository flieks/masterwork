"""The role store: stage agents as editable files, resolved repo → home → built-in.

A role is a directory of three files — `system.md` (static identity), `user.md`
(the per-turn task, with `{{placeholders}}`) and `role.json` (model, tools, write
boundary, purpose). Nothing here knows about Claude: a role is just text plus a
small settings object, so the same store can drive another CLI later.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

ROLES = ("plan", "build", "review", "document", "scout")

# Roles that write nothing: their boundary is `[]` and their write tools are gone.
READ_ONLY_ROLES = ("review", "scout")

SYSTEM_FILE = "system.md"
USER_FILE = "user.md"
CONFIG_FILE = "role.json"
ROLE_FILES = (SYSTEM_FILE, USER_FILE, CONFIG_FILE)
TEMPLATE_FILES = (SYSTEM_FILE, USER_FILE)

PROJECT_SUBDIR = Path(".masterwork")
PROJECT_ROLES_SUBDIR = PROJECT_SUBDIR / "agents"

# The house rules every role is held to, appended library-then-repo. Lives beside
# the roles rather than inside one, because a rule only one role can see is how a
# builder and a reviewer end up judging the same change by different standards.
CONVENTIONS_FILE = "conventions.md"
DEFAULT_ROLES_ROOT = Path.home() / ".masterwork" / "agents"
ROLES_DIR_ENV = "MASTERWORK_ROLES_DIR"

BUILT_IN = "(built-in)"
NONE_MARKER = "(none)"

# Which of the three layers answered for a file. The prompt masterwork displays is
# the library copy; a repo override wins over it silently, so the layer travels
# with the resolution into the dry-run table and into telemetry.
LAYER_REPO = "repo"
LAYER_LIBRARY = "library"
LAYER_BUILTIN = "builtin"

# masterwork indexes every role file as an asset named `masterwork:agent:<role>:<part>`
# and skips anything outside this charset — a role it cannot name is a role nobody
# can see or edit there. Same regex as backend/app/providers/masterwork_roles.py.
ROLE_NAME_PATTERN = r"[a-z0-9][a-z0-9_-]{0,63}"
_ROLE_NAME = re.compile(ROLE_NAME_PATTERN)


def is_valid_role_name(name: str) -> bool:
    return _ROLE_NAME.fullmatch(name) is not None


class RoleError(Exception):
    """A role file is unreadable, malformed, or uses a placeholder nobody supplies."""


# --- the template contract -------------------------------------------------

# The one place every template variable is documented. Both system.md and
# user.md may use any of them; anything else is a config error.
VARIABLES: dict[str, str] = {
    "role": f"this role's own name — one of {', '.join(ROLES)}",
    "repo": "absolute path of the target repository",
    "request": "the original request, verbatim",
    "previous_stage": f"name of the upstream stage, or {NONE_MARKER} for the first stage",
    "previous_envelope": f"pretty-printed JSON of the upstream envelope, or {NONE_MARKER}",
    "artifacts": "contents of the artifact files the upstream envelope named, size-capped",
    "boundary": "this role's write boundary, stated in words",
    "conventions": (
        f"the shared conventions every role is shown — `<library>/{CONVENTIONS_FILE}` then "
        f"`<repo>/{(PROJECT_SUBDIR / CONVENTIONS_FILE).as_posix()}`, empty when neither exists"
    ),
    "envelope_contract": (
        "the required-field envelope contract for this role, worded for its write "
        "boundary — a read-only role is shown an empty `changed_files`"
    ),
    "output_contract": (
        "the same contract in three lines, for a role's `system.md` — the only text "
        "re-sent on every correction; put it there when a role keeps failing the "
        "envelope gate, and nowhere else, because it costs tokens on every turn"
    ),
}

_PLACEHOLDER = re.compile(r"\{\{([^}\n]*)\}\}")
_BLANK_LINE = re.compile(r"\n[ \t]*\n")


def placeholder_names(template: str) -> list[str]:
    return [match.group(1).strip() for match in _PLACEHOLDER.finditer(template)]


def _unknown(source: str, name: str) -> RoleError:
    known = ", ".join(sorted(VARIABLES))
    return RoleError(
        f"{source}: unknown placeholder {{{{{name}}}}} — known variables are: {known}"
    )


def check_placeholders(template: str, *, source: str) -> None:
    """An unknown `{{placeholder}}` is a config error, never literal prompt text."""
    for name in placeholder_names(template):
        if name not in VARIABLES:
            raise _unknown(source, name)


# The floor of variables a resolved template must still carry, per role and per
# file. It lives in code, not only in role.json: role.json is as rewritable as the
# template it guards, so a requirement that can be deleted alongside it guards
# nothing. `requires` in role.json widens this; nothing shrinks it.
#
# system.md is a static identity and needs no per-turn value. user.md is the task:
# drop {{request}} and the prompt renders perfectly and is sent with no request in
# it — the one degradation no downstream gate can see.
_TASK = ("request",)
_DEFAULT_REQUIRED: dict[str, tuple[str, ...]] = {SYSTEM_FILE: (), USER_FILE: _TASK}
# `conventions` is deliberately NOT required: a library seeded before it existed
# still runs, and the missing-conventions warning tells the user what it is losing.
_REQUIRED: dict[str, dict[str, tuple[str, ...]]] = {
    role: {SYSTEM_FILE: (), USER_FILE: _TASK} for role in ROLES
}


def builtin_requirements(role: str) -> dict[str, tuple[str, ...]]:
    """What this role's templates must use even when no `role.json` says so."""
    return _REQUIRED.get(role, _DEFAULT_REQUIRED)


def check_required(
    template: str, *, role: str, filename: str, required: tuple[str, ...], source: str
) -> None:
    """The reverse of `check_placeholders`: a variable the template forgot."""
    used = set(placeholder_names(template))
    for name in required:
        if name not in used:
            raise RoleError(
                f"{source}: the {role} role's {filename} must use {{{{{name}}}}} "
                f"({VARIABLES[name]}) — a template that drops it still renders, and the "
                f"turn is sent without it. Required in {filename} for {role}: "
                f"{', '.join(required)}."
            )


def render(template: str, values: Mapping[str, str], *, source: str) -> str:
    """Plain `{{name}}` substitution, block by block. No engine, no dependency.

    Blocks are the blank-line-separated chunks of the *template* (never of the
    substituted values, which may contain blank lines of their own). A block that
    renders empty — or to a bare `##` heading — is dropped: that is how an
    optional section such as `## Artifacts …` expresses itself without a
    conditional syntax.
    """
    blocks: list[str] = []
    for raw in _BLANK_LINE.split(template):
        rendered = _substitute(raw, values, source).strip()
        if not rendered or _heading_only(rendered):
            continue
        blocks.append(rendered)
    return "\n\n".join(blocks)


def _substitute(block: str, values: Mapping[str, str], source: str) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1).strip()
        if name not in values:
            raise _unknown(source, name)
        return values[name]

    return _PLACEHOLDER.sub(replace, block)


def _heading_only(block: str) -> bool:
    lines = block.splitlines()
    return bool(lines) and lines[0].lstrip().startswith("#") and not "\n".join(lines[1:]).strip()


# --- built-in defaults -----------------------------------------------------

UNATTENDED_RULE = """UNATTENDED RUN — you cannot ask anyone anything.
Every question you would have asked becomes an entry in `assumptions`. If an
assumption is too dangerous to make silently (destructive migration, credential
rotation, data deletion), do not make it: return `status: "blocked"` with the
reason in `summary`, and the run stops cleanly."""

_REQUEST_BLOCK = "## Original request\n{{request}}"

_UPSTREAM_BLOCKS = """## Envelope from the {{previous_stage}} stage
```json
{{previous_envelope}}
```

## Artifacts named by the {{previous_stage}} stage
{{artifacts}}"""

_TAIL = "{{conventions}}\n\n{{boundary}}\n\n" + UNATTENDED_RULE + "\n\n{{envelope_contract}}"

_FIRST_STAGE_USER = f"{_REQUEST_BLOCK}\n\n{_TAIL}\n"
_DOWNSTREAM_USER = f"{_REQUEST_BLOCK}\n\n{_UPSTREAM_BLOCKS}\n\n{_TAIL}\n"

_SYSTEM = {
    "plan": """You are the PLAN stage of a deterministic, unattended pipeline.
Read the repo, then write an implementation plan to `plan.md`: the change in one
paragraph, the files to add or change with why, the data/contract impact, the
test strategy, and the risks. Be concrete about paths. Do not implement anything.

Before you write the plan, load the house skill for the stack you are touching, with
the Skill tool — a Python/FastAPI change → `backend-dev`, a React change →
`frontend-dev`, an Expo/React Native change → `mobile-dev`. Only a skill listed as
available to you, and only one that matches: a mobile skill on a Python backend is a
wasted turn, and none matching means you load none. Plan to what the skill says.

{{output_contract}}
""",
    "build": """You are the BUILD stage of a deterministic, unattended pipeline.
Implement the plan in the repo, tests included. You have no shell: the runner
executes the repo's own test and lint commands after you finish, so make the code
correct rather than claiming it is. Keep the change minimal and in the repo's style.

Before you edit, load the house skill for the stack you are changing, with the Skill
tool — `backend-dev` for Python/FastAPI, `frontend-dev` for React, `mobile-dev` for
Expo/React Native, plus `tdd` when you write the tests and `concise-comments` when
you write comments. Only skills listed as available to you, and only ones that match:
a mobile skill on a Python backend is a wasted turn. Build to what the skill says.
""",
    "review": """You are the REVIEW stage of a deterministic, unattended pipeline.
You are READ-ONLY: you write no files, so your own `changed_files` is always `[]` —
never repeat the files the stage under review changed. You have no shell, so read the
changed files rather than diffing them.

Judge the work on two axes, answered as two separate questions — they fail
differently, and a reviewer that blurs them reports only whichever it noticed first.

STANDARDS — does the code follow the standards this repo documents, its conventions
and its layering? Its authority is the shared conventions you are shown, whatever the
repo itself documents, the house skill you load below, and the style of the code
already around the change — never your own taste. Where none of those speak, fall
back to the classic smells: duplicated logic, mysterious names, speculative
generality, primitive obsession, feature envy, shotgun surgery. A documented repo
rule always beats that fallback, and anything the repo's own linter, formatter or
type checker enforces is never your finding — the runner runs those itself.

SPEC — does the change do what was asked, no more and no less? Its authority is the
original request, plus `plan.md` if a planner wrote one; where those two disagree the
request wins, and there is nothing else to appeal to. Look for three failures:
something asked for that is missing or half-done, behaviour nobody asked for, and a
requirement that looks implemented but is implemented wrongly. Quote the words you
are holding the code to.

Work can pass one axis and fail the other, so `summary` gives a verdict for EACH,
including the one that passed — neither axis stands in for the other.

Findings that must be fixed go in `blocking`: one clear, actionable sentence each,
naming the file, and opening with the axis it came from — `Standards:` or `Spec:` is
all the builder is told about which question it failed. Anything you would not send
the work back for — a judgement call, a nit, a preference — goes in `summary` and
never in `blocking`, because every blocking entry costs the run another build round.
Set `approved: true` only when `blocking` is empty. Disapproval is a verdict, not a
status: however wrong the work is, say so with `approved: false` and the reasons in
`blocking`, and let the runner loop it back to the builder. `status: "blocked"` says
only that you could not review at all.

Before you judge, load the house skill for the stack under review, with the Skill
tool — `backend-dev` for Python/FastAPI, `frontend-dev` for React, `mobile-dev` for
Expo/React Native, plus `architecture-review` when the change crosses the
frontend/backend boundary. Only skills listed as available to you, and only ones that
match: a mobile skill on a Python backend is a wasted turn. Not `code-review` — its
method is the two axes above and you already have it; the skill itself only adds the
sub-agents and the `git diff` you have no tools for. Judge against what the skill says.
""",
    "document": """You are the DOCUMENT stage of a deterministic, unattended pipeline.
Update the user-facing documentation to match what was built — README, docs pages,
and the changelog if the repo keeps one. Change no source code. If nothing needs
documenting, say so in the summary and change nothing.
""",
    "scout": """You are the SCOUT stage of a deterministic, unattended pipeline.
Answer the question you were given about this repository by reading it, and report
what you found: the files and symbols that matter, how they fit together, what
already exists, and anything that would surprise whoever acts on this next. You are
READ-ONLY: you write no files, you propose no plan, and you implement nothing — so
your `artifacts` and `changed_files` are always `[]`. Put each finding in `findings`
as one self-contained sentence naming the file or symbol it is about; put the answer
to the question, in a paragraph, in `summary`. A question you cannot answer from the
repo is a finding that says so, not a guess.
""",
}

# Kept in step with config.DEFAULT_MODELS / DEFAULT_BOUNDARIES / _disallowed_for
# by test_roles.py — role.json is the lower-precedence layer, not a second truth.
_BASE_TOOLS = ["Bash", "Task", "WebFetch", "WebSearch"]
_WRITE_TOOLS = ["Edit", "MultiEdit", "Write", "NotebookEdit"]

_ROLE_JSON: dict[str, dict[str, object]] = {
    "plan": {
        "purpose": "Read the repo and write the implementation plan.",
        "model": "opus",
        "disallowed_tools": list(_BASE_TOOLS),
        "writes": ["plan.md", "docs/specs/**"],
    },
    "build": {
        "purpose": "Implement the plan, tests included.",
        "model": "sonnet",
        "disallowed_tools": list(_BASE_TOOLS),
        "writes": None,
    },
    "review": {
        "purpose": "Judge the work on standards and spec, and write nothing.",
        "model": "opus",
        "disallowed_tools": _BASE_TOOLS + _WRITE_TOOLS,
        "writes": [],
    },
    "document": {
        "purpose": "Bring the user-facing docs back in step with the code.",
        "model": "sonnet",
        "disallowed_tools": list(_BASE_TOOLS),
        "writes": ["docs/**", "README.md", "CHANGELOG.md"],
    },
    "scout": {
        "purpose": "Answer a question about the repo from the repo, and write nothing.",
        "model": "sonnet",
        "disallowed_tools": _BASE_TOOLS + _WRITE_TOOLS,
        "writes": [],
    },
}


# --- shared conventions ----------------------------------------------------

_CONVENTIONS_HEADING = "## Shared conventions"
_CONVENTIONS_LEAD = (
    "House rules every role in this pipeline is held to. The builder writes to them "
    "and the reviewer judges against them, so neither can be surprised by the other."
)
# Repo-level APPENDS to library-level rather than replacing it: conventions are a
# list of rules, not a coherent identity like a system prompt, so two lists compose
# where two prompts would not. Replacing would mean a project adding one rule
# silently drops every global one — the same shadowing bug the refresh path exists
# to fix. Ordering carries the override: the repo's section is read last and wins.
_CONVENTIONS_PRECEDENCE = "Where the two disagree, the repository's own file wins."


def compose_conventions(sources: list[tuple[Path, str]]) -> str:
    """The `{{conventions}}` value: one block, or empty when nobody wrote any."""
    sections = [(path, text.strip()) for path, text in sources if text.strip()]
    if not sections:
        return ""
    lead = _CONVENTIONS_LEAD
    if len(sections) > 1:
        lead += " " + _CONVENTIONS_PRECEDENCE
    body = "\n\n".join(f"### From {path}\n{text}" for path, text in sections)
    return f"{_CONVENTIONS_HEADING}\n{lead}\n\n{body}"


LIBRARY_README = "README.md"


def library_readme() -> str:
    """The only on-disk documentation of the template contract — generated, never stale."""
    variables = "\n".join(f"- `{{{{{name}}}}}` — {text}" for name, text in VARIABLES.items())
    required = "\n".join(
        f"- `{role}` → `{USER_FILE}`: "
        + (", ".join(f"`{{{{{n}}}}}`" for n in builtin_requirements(role)[USER_FILE]) or "none")
        for role in ROLES
    )
    return f"""# masterwork role library

One directory per role: {", ".join(ROLES)}. Each holds three files.

- `{SYSTEM_FILE}` — the agent's static identity, sent as the system prompt.
- `{USER_FILE}` — the per-turn task, sent as the user prompt.
- `{CONFIG_FILE}` — `model`, `disallowed_tools`, `writes` (the boundary globs;
  `null` unrestricted, `[]` read-only), a one-line `purpose`, and an optional
  `requires` (see below).

A role directory's name must match `{ROLE_NAME_PATTERN}`: masterwork indexes each
file as `masterwork:agent:<role>:<part>`, and a name it cannot represent is a role
nobody can see or edit.

## Resolution

First hit wins, per role and per file — `{LAYER_REPO}`, then `{LAYER_LIBRARY}`, then `{LAYER_BUILTIN}`:

1. `<target repo>/{PROJECT_ROLES_SUBDIR.as_posix()}/<role>/…`
2. this library
3. the defaults built into the package

`--dry-run` prints which layer answered for each file, and every `agent_turn`
telemetry event carries the same three-way answer.

## Keeping this library up to date

These files are yours: `--seed-roles` only ever fills gaps, so a file that exists
here shadows the built-in default forever — including every later improvement to it.
`--refresh-roles` reports which files are still byte-identical to the default they
were seeded from and which ones you have edited; `--refresh-roles --apply` updates
only the un-edited ones. An edited file is never rewritten without
`--overwrite-edited`, and even then a `.bak` is kept next to it.

## Shared conventions

`{CONVENTIONS_FILE}` in this directory is shown to EVERY role, appended to by
`<target repo>/{(PROJECT_SUBDIR / CONVENTIONS_FILE).as_posix()}` — the repo's rules are
read last and win where the two disagree. Both are optional; with neither present the
`{{{{conventions}}}}` block disappears from the prompt entirely. Put house rules here
rather than in one role's `{SYSTEM_FILE}`: a rule only the builder can see is a rule the
reviewer will block the run over.

## Template variables

Both `{SYSTEM_FILE}` and `{USER_FILE}` may use these, and nothing else — an unknown
`{{{{placeholder}}}}` is a startup error naming the file:

{variables}

A blank-line-separated block that renders empty, or to a bare `##` heading, is
dropped. That is how `## Artifacts …` disappears when there are none.

## Required variables

A template that DROPS a variable renders perfectly and sends a prompt with a hole
in it, so each role also declares what its templates must still use. Missing one is
a startup error naming the file, the role and the variable:

{required}

`{SYSTEM_FILE}` requires nothing — it is a static identity. Add stricter rules per
role with `"requires": {{"{USER_FILE}": ["request", "boundary"]}}` in `{CONFIG_FILE}`;
that can only widen the list above, never shrink it.

## Precedence for settings

CLI flag > the target repo's `factory.config.json` > `{CONFIG_FILE}` > built-in default.

Every prompt actually sent is saved under
`~/.masterwork/runs/<repo>/<run_id>/prompts/<role>/<turn>.{{system,user}}.md`.
"""


# Roles that normally open a workflow, and so are shown no upstream envelope.
_FIRST_STAGE_ROLES = ("plan", "scout")


def builtin_text(role: str, filename: str) -> str:
    if filename == SYSTEM_FILE:
        return _SYSTEM[role]
    if filename == USER_FILE:
        return _FIRST_STAGE_USER if role in _FIRST_STAGE_ROLES else _DOWNSTREAM_USER
    return json.dumps(_ROLE_JSON[role], indent=2, ensure_ascii=False) + "\n"


# --- role.json -------------------------------------------------------------

_CONFIG_KEYS = ("purpose", "model", "disallowed_tools", "writes", "requires")


@dataclass(frozen=True)
class RoleConfig:
    purpose: str = ""
    model: str | None = None
    disallowed_tools: tuple[str, ...] | None = None
    writes: list[str] | None = None
    # `writes` is three-valued: absent (fall through), null (unrestricted), [] (read-only).
    writes_set: bool = False
    # Extra required variables per template file — added to the built-in floor.
    requires: dict[str, tuple[str, ...]] = field(default_factory=dict)


def parse_role_config(text: str, *, source: str) -> RoleConfig:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        raise RoleError(f"{source}: not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RoleError(f"{source}: must contain a JSON object")

    unknown = sorted(set(data) - set(_CONFIG_KEYS))
    if unknown:
        raise RoleError(
            f"{source}: unknown key(s) {', '.join(unknown)} — allowed: {', '.join(_CONFIG_KEYS)}"
        )

    purpose = data.get("purpose", "")
    if not isinstance(purpose, str):
        raise RoleError(f'{source}: "purpose" must be a string')

    model = data.get("model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise RoleError(f'{source}: "model" must be a non-empty string or null')

    tools = data.get("disallowed_tools")
    if tools is not None and not (
        isinstance(tools, list) and all(isinstance(t, str) for t in tools)
    ):
        raise RoleError(f'{source}: "disallowed_tools" must be a list of strings or null')

    writes = data.get("writes")
    if writes is not None and not (
        isinstance(writes, list) and all(isinstance(p, str) for p in writes)
    ):
        raise RoleError(f'{source}: "writes" must be null or a list of glob strings')

    return RoleConfig(
        purpose=purpose,
        model=model.strip() if isinstance(model, str) else None,
        disallowed_tools=tuple(tools) if tools is not None else None,
        writes=list(writes) if writes is not None else None,
        writes_set="writes" in data,
        requires=_parse_requires(data.get("requires"), source),
    )


def _parse_requires(raw: object, source: str) -> dict[str, tuple[str, ...]]:
    """`{"user.md": ["request", "boundary"]}` — a stricter contract than the floor."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise RoleError(
            f'{source}: "requires" must be an object mapping '
            f"{' or '.join(TEMPLATE_FILES)} to a list of variable names"
        )
    out: dict[str, tuple[str, ...]] = {}
    for filename, names in raw.items():
        if filename not in TEMPLATE_FILES:
            raise RoleError(
                f'{source}: "requires" key {filename!r} is not a template file — '
                f"expected {' or '.join(TEMPLATE_FILES)}"
            )
        if not (isinstance(names, list) and all(isinstance(n, str) for n in names)):
            raise RoleError(f'{source}: "requires.{filename}" must be a list of variable names')
        for name in names:
            if name not in VARIABLES:
                raise _unknown(f'{source} ("requires.{filename}")', name)
        out[filename] = tuple(names)
    return out


def required_variables(role: str, config: RoleConfig) -> dict[str, tuple[str, ...]]:
    """The built-in floor for this role, widened by whatever role.json adds."""
    base = builtin_requirements(role)
    return {
        filename: tuple(dict.fromkeys(base.get(filename, ()) + config.requires.get(filename, ())))
        for filename in TEMPLATE_FILES
    }


# --- resolution ------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedRole:
    name: str
    system: str  # template text, not yet rendered
    user: str  # template text, not yet rendered
    config: RoleConfig
    sources: dict[str, str]  # filename → the path it came from, or "(built-in)"
    layers: dict[str, str]  # filename → repo | library | builtin

    @property
    def layer_mix(self) -> str:
        """`repo+library` — the distinct layers this role was assembled from."""
        return "+".join(dict.fromkeys(self.layers[f] for f in ROLE_FILES))


def _label(role: str, filename: str, source: str) -> str:
    """What an error message calls the file — a real path, or the built-in it fell back to."""
    return f"{role}/{filename} {BUILT_IN}" if source == BUILT_IN else source


def _unusable_name(name: str) -> str:
    return (
        f"role name {name!r} cannot be represented — masterwork indexes roles as "
        f"`masterwork:agent:<role>:<part>` and only accepts {ROLE_NAME_PATTERN}, so this "
        "role would be invisible there, with nowhere to view or edit it"
    )


def default_roles_root() -> Path:
    override = os.environ.get(ROLES_DIR_ENV)
    return Path(override).expanduser() if override else DEFAULT_ROLES_ROOT


# --- the library, file by file ---------------------------------------------

# What seeding wrote, so `--refresh-roles` can tell OUR text from the user's. A
# library seeded before this file existed has no record, and every difference there
# is treated as an edit — the safe direction.
SEED_RECORD = ".seeded.json"

CURRENT = "current"  # byte-identical to today's built-in: nothing to do
PRISTINE = "pristine"  # exactly what we seeded, and we have since improved it
EDITED = "edited"  # differs from both — the user's, never overwritten silently
MISSING = "missing"  # not in the library at all


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LibraryFile:
    role: str  # "" for the library-level README
    filename: str
    path: Path
    builtin: str
    state: str = CURRENT
    on_disk: str | None = None

    @property
    def key(self) -> str:
        return f"{self.role}/{self.filename}" if self.role else self.filename

    @property
    def backup(self) -> Path:
        return self.path.with_name(self.path.name + ".bak")

    @property
    def diff(self) -> str:
        """Built-in → your copy, so `-` lines are the default and `+` lines are yours."""
        return "".join(
            difflib.unified_diff(
                self.builtin.splitlines(keepends=True),
                (self.on_disk or "").splitlines(keepends=True),
                fromfile=f"built-in {self.key}",
                tofile=str(self.path),
            )
        )

    def with_state(self, state: str, on_disk: str | None) -> LibraryFile:
        return LibraryFile(
            role=self.role,
            filename=self.filename,
            path=self.path,
            builtin=self.builtin,
            state=state,
            on_disk=on_disk,
        )


class RoleStore:
    """Resolution order, per role and per file: repo override → global library → built-in."""

    def __init__(self, repo: Path, roles_dir: Path | str | None = None) -> None:
        self.repo = Path(repo)
        self.global_dir = (
            Path(roles_dir).expanduser() if roles_dir is not None else default_roles_root()
        )
        self.project_dir = self.repo / PROJECT_ROLES_SUBDIR

    @property
    def search_path(self) -> tuple[tuple[str, Path], ...]:
        return ((LAYER_REPO, self.project_dir), (LAYER_LIBRARY, self.global_dir))

    def _read(self, role: str, filename: str) -> tuple[str, str, str]:
        for layer, base in self.search_path:
            path = base / role / filename
            if path.is_file():
                try:
                    return path.read_text(encoding="utf-8"), str(path), layer
                except OSError as exc:
                    raise RoleError(f"could not read {path}: {exc}") from exc
        return builtin_text(role, filename), BUILT_IN, LAYER_BUILTIN

    def resolve(self, role: str) -> ResolvedRole:
        if not is_valid_role_name(role):
            raise RoleError(_unusable_name(role))
        if role not in ROLES:
            raise RoleError(f"unknown role {role!r} — known roles: {', '.join(ROLES)}")
        system, system_src, system_layer = self._read(role, SYSTEM_FILE)
        user, user_src, user_layer = self._read(role, USER_FILE)
        raw_config, config_src, config_layer = self._read(role, CONFIG_FILE)
        config = parse_role_config(raw_config, source=_label(role, CONFIG_FILE, config_src))
        required = required_variables(role, config)
        for filename, template, source in (
            (SYSTEM_FILE, system, system_src),
            (USER_FILE, user, user_src),
        ):
            label = _label(role, filename, source)
            check_placeholders(template, source=label)
            check_required(
                template, role=role, filename=filename, required=required[filename], source=label
            )
        return ResolvedRole(
            name=role,
            system=system,
            user=user,
            config=config,
            sources={SYSTEM_FILE: system_src, USER_FILE: user_src, CONFIG_FILE: config_src},
            layers={
                SYSTEM_FILE: system_layer,
                USER_FILE: user_layer,
                CONFIG_FILE: config_layer,
            },
        )

    def resolve_all(self) -> dict[str, ResolvedRole]:
        return {role: self.resolve(role) for role in ROLES}

    # --- shared conventions -------------------------------------------------

    @property
    def conventions_paths(self) -> tuple[Path, ...]:
        """Library first, repo last — the order they are appended in, so the repo wins."""
        return (
            self.global_dir / CONVENTIONS_FILE,
            self.repo / PROJECT_SUBDIR / CONVENTIONS_FILE,
        )

    @property
    def conventions_sources(self) -> tuple[Path, ...]:
        return tuple(path for path in self.conventions_paths if path.is_file())

    def conventions(self) -> str:
        """The `{{conventions}}` value for this repo: both files, appended, or empty."""
        sources: list[tuple[Path, str]] = []
        for path in self.conventions_sources:
            try:
                sources.append((path, path.read_text(encoding="utf-8")))
            except OSError as exc:
                raise RoleError(f"could not read {path}: {exc}") from exc
        return compose_conventions(sources)

    # --- startup warnings ---------------------------------------------------

    def startup_warnings(self, resolved: Mapping[str, ResolvedRole]) -> list[str]:
        """Three things worth saying out loud before a run: a file that vanished from
        the library (the role silently reverts to hardcoded text — the fingerprint
        of a deleted file), a role whose templates cannot show the conventions file
        the user wrote, and a role directory masterwork can never index. A repo
        override is the normal way to override and says nothing."""
        return (
            self._library_gaps(resolved)
            + self._conventions_ignored(resolved)
            + self._unusable_dirs()
        )

    def _conventions_ignored(self, resolved: Mapping[str, ResolvedRole]) -> list[str]:
        """A conventions file nobody renders is worse than no conventions file: the
        user wrote house rules and the run silently ignores them."""
        written = self.conventions_sources
        if not written:
            return []
        return [
            f"conventions: {', '.join(str(p) for p in written)} exists, but the {role} role's "
            f"templates never use {{{{conventions}}}} — those rules are NOT reaching {role} "
            "(--refresh-roles shows which library files still predate it)"
            for role, entry in sorted(resolved.items())
            if not any(
                "conventions" in placeholder_names(text)
                for text in (entry.system, entry.user)
            )
        ]

    def _library_gaps(self, resolved: Mapping[str, ResolvedRole]) -> list[str]:
        if not self.global_dir.is_dir():
            return []  # nothing seeded yet: falling back to the built-ins is expected
        return [
            f"role library: {self.global_dir / role / filename} is missing — {role} falls back "
            "to the built-in text (--seed-roles writes it back)"
            for role, entry in resolved.items()
            for filename in ROLE_FILES
            if entry.layers.get(filename) == LAYER_BUILTIN
        ]

    def _unusable_dirs(self) -> list[str]:
        out: list[str] = []
        for _, base in self.search_path:
            if not base.is_dir():
                continue
            try:
                entries = sorted(base.iterdir())
            except OSError:
                continue
            out += [
                f"{entry}: {_unusable_name(entry.name)}"
                for entry in entries
                if entry.is_dir()
                and not entry.name.startswith(".")
                and not is_valid_role_name(entry.name)
            ]
        return out

    # --- seeding -----------------------------------------------------------

    def seed(self) -> list[Path]:
        """Write every missing default into the global library. Never overwrites."""
        written: list[Path] = []
        seeded: dict[str, str] = {}
        for entry in self._defaults():
            if entry.path.exists():
                continue
            self._write(entry.path, entry.builtin)
            written.append(entry.path)
            seeded[entry.key] = digest(entry.builtin)
        if seeded:
            self._record_seeded(seeded)
        return written

    def seed_if_new(self) -> list[Path]:
        """First use only: an existing library is left exactly as the user left it."""
        return [] if self.global_dir.exists() else self.seed()

    # --- refreshing ---------------------------------------------------------

    def _defaults(self) -> list[LibraryFile]:
        """Every file the library is supposed to hold, with the current built-in text."""
        wanted: list[tuple[str, str, str]] = [("", LIBRARY_README, library_readme())]
        wanted += [
            (role, filename, builtin_text(role, filename))
            for role in ROLES
            for filename in ROLE_FILES
        ]
        return [
            LibraryFile(role=role, filename=filename, path=self._path(role, filename), builtin=text)
            for role, filename, text in wanted
        ]

    def _path(self, role: str, filename: str) -> Path:
        return (self.global_dir / role / filename) if role else self.global_dir / filename

    def audit(self) -> list[LibraryFile]:
        """Per file: identical to the built-in, still pristine, edited here, or missing.

        `pristine` is only knowable against a record of what seeding actually wrote —
        without one, a file that differs from today's built-in is indistinguishable
        from a hand-edit, and is reported as EDITED so nobody's work is assumed away.
        """
        seeded = self._seed_record()
        out: list[LibraryFile] = []
        for entry in self._defaults():
            if not entry.path.is_file():
                out.append(entry.with_state(MISSING, None))
                continue
            try:
                text = entry.path.read_text(encoding="utf-8")
            except OSError as exc:
                raise RoleError(f"could not read {entry.path}: {exc}") from exc
            if text == entry.builtin:
                state = CURRENT
            elif seeded.get(entry.key) == digest(text):
                state = PRISTINE
            else:
                state = EDITED
            out.append(entry.with_state(state, text))
        return out

    def refresh(
        self, *, overwrite_edited: bool = False
    ) -> tuple[list[LibraryFile], list[LibraryFile], list[LibraryFile]]:
        """Bring the library up to date. Returns (updated, skipped, backed_up).

        Only files carrying no edit of yours are rewritten; an edited one needs
        `overwrite_edited`, and even then its old text is kept beside it as `.bak`.
        """
        updated: list[LibraryFile] = []
        skipped: list[LibraryFile] = []
        backed_up: list[LibraryFile] = []
        seeded: dict[str, str] = {}
        for entry in self.audit():
            if entry.state == CURRENT:
                # Nothing to write — but a file byte-identical to the built-in is
                # PROOF that it carries no edit, and a library seeded before the
                # record existed has no other way to say so. Remember it now, or the
                # next improvement to this default reads as the user's own work and
                # is skipped forever.
                seeded.setdefault(entry.key, digest(entry.builtin))
                continue
            if entry.state == EDITED and not overwrite_edited:
                skipped.append(entry)
                continue
            if entry.state == EDITED:
                self._write(entry.backup, entry.on_disk or "")
                backed_up.append(entry)
            self._write(entry.path, entry.builtin)
            seeded[entry.key] = digest(entry.builtin)
            updated.append(entry)
        if seeded:
            self._record_seeded(seeded)
        return updated, skipped, backed_up

    # --- the seed record ----------------------------------------------------

    @property
    def seed_record_path(self) -> Path:
        return self.global_dir / SEED_RECORD

    def _seed_record(self) -> dict[str, str]:
        try:
            data = json.loads(self.seed_record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return {}  # absent or unreadable: every difference counts as an edit
        files = data.get("files") if isinstance(data, dict) else None
        if not isinstance(files, dict):
            return {}
        return {k: v for k, v in files.items() if isinstance(k, str) and isinstance(v, str)}

    def _record_seeded(self, digests: Mapping[str, str]) -> None:
        """What we wrote, so a later refresh can tell our text from the user's."""
        merged = {**self._seed_record(), **digests}
        payload = {"version": 1, "files": dict(sorted(merged.items()))}
        self._write(self.seed_record_path, json.dumps(payload, indent=2) + "\n")

    def _write(self, path: Path, text: str) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except OSError as exc:
            raise RoleError(f"could not write {path}: {exc}") from exc
