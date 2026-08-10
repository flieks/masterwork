"""The role store: resolution order, seeding, templating, role.json precedence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from adw import prompts, roles
from adw.config import (
    BASE_DISALLOWED,
    DEFAULT_BOUNDARIES,
    DEFAULT_MODELS,
    WRITE_TOOLS,
    ConfigError,
    load_config,
)
from adw.envelopes import parse_envelope
from adw.roles import (
    CONFIG_FILE,
    ROLE_FILES,
    ROLES,
    SYSTEM_FILE,
    USER_FILE,
    RoleError,
    RoleStore,
)

GOLDEN = Path(__file__).resolve().parent / "golden" / "legacy_prompts.json"

# The exact fixture the golden file was captured against, before the refactor.
REQUEST = "Add a /health endpoint that returns {'status': 'ok'}"
ARTIFACT_MAX_BYTES = 200
PLAN_MD = "# Plan\nAdd GET /health returning {'status': 'ok'}.\n"
BIG_MD = "spec line\n" * 60

PLAN_ENVELOPE = {
    "status": "ok",
    "summary": "Plan the health endpoint.\nSecond line is dropped from the commit subject.",
    "artifacts": ["plan.md", "docs/specs/big.md", "docs/specs/gone.md"],
    "notes_for_next_agent": "Put the route in app.py",
    "changed_files": ["plan.md"],
    "approved": False,
    "blocking": [],
    "assumptions": ["No auth on /health"],
}
BUILD_ENVELOPE = {
    "status": "ok",
    "summary": "Added the health route.",
    "artifacts": [],
    "notes_for_next_agent": "app.py now exposes health().",
    "changed_files": ["app.py"],
    "approved": False,
    "blocking": [],
    "assumptions": [],
}


def as_envelope(data: dict):
    return parse_envelope("```json\n" + json.dumps(data) + "\n```", "build").envelope


def golden_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "golden-repo"
    (repo / "docs" / "specs").mkdir(parents=True)
    (repo / "plan.md").write_text(PLAN_MD, encoding="utf-8")
    (repo / "docs" / "specs" / "big.md").write_text(BIG_MD, encoding="utf-8")
    return repo


def write_role_file(base: Path, role: str, filename: str, text: str) -> Path:
    path = base / role / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --- behavioural equivalence with the pre-refactor prompts.py ---------------


# The roles that existed when the golden file was captured (`_note` is its header).
# A role added later has no pre-refactor prompt to be equivalent to, so it is pinned
# by its own tests instead.
GOLDEN_ROLES = tuple(
    sorted(key for key in json.loads(GOLDEN.read_text(encoding="utf-8")) if key != "_note")
)


@pytest.mark.parametrize("role", GOLDEN_ROLES)
def test_the_builtin_defaults_reproduce_the_hardcoded_prompts(tmp_path: Path, role: str):
    """system + user must be byte-identical to what prompts.py produced before.

    `review` is the one entry deliberately re-captured (2026-08-10), and must NOT be
    restored to the legacy text. The pre-refactor contract was role-generic: it asked
    every role for `"changed_files": ["every file you created or modified"]`, including
    a role whose write boundary is `[]`, whose write tools are disallowed, and whose
    prompt inlines the builder's envelope directly above. Reviewers copied that file
    list, gate 3 correctly reported "claimed but not changed on disk", and every run
    spent a correction on review — run ad94b30c failed the whole pipeline when the
    reviewer read its own gate correction as a finding against the builder. The
    contract is now derived from the role's boundary (`Stage.read_only`), so this
    divergence recurs for any future read-only role. plan/build/document are
    untouched and stay pinned against the pre-refactor implementation.

    The same entry diverged a SECOND time (2026-08-10, same day) for the other axis:
    nothing in the legacy text said what `status` means next to `approved`, so a
    reviewer could express disapproval as `status: "blocked"` — which the runner read
    as a clean stop and which therefore skipped the review→build correction loop
    entirely. Both the role identity and the compiled contract now state the rule:
    `status` says whether the review could be carried out, `approved` + `blocking`
    are the verdict. It is in the contract (compiled from code every turn) and not
    only in `system.md`, because a role library seeded before the rule existed keeps
    its own copy of that file forever. Only a role that owes an `approved` field is
    shown it (`envelopes.owes_a_verdict`), so plan/build/document stay byte-identical.
    """
    repo = golden_repo(tmp_path)
    cfg = load_config(repo)
    upstream = {
        "plan": (None, None),
        "build": ("plan", PLAN_ENVELOPE),
        "review": ("build", BUILD_ENVELOPE),
        "document": ("build", BUILD_ENVELOPE),
    }[role]

    compiled = prompts.compile_prompt(
        role=cfg.roles[role],
        stage=cfg.stages[role],
        request=REQUEST,
        repo=repo,
        previous_stage=upstream[0],
        previous=as_envelope(upstream[1]) if upstream[1] else None,
        artifact_max_bytes=ARTIFACT_MAX_BYTES,
    )
    assert compiled.combined == json.loads(GOLDEN.read_text(encoding="utf-8"))[role]


def test_the_split_puts_identity_in_system_and_the_task_in_user(tmp_path: Path):
    repo = golden_repo(tmp_path)
    cfg = load_config(repo)
    compiled = prompts.compile_prompt(
        role=cfg.roles["review"], stage=cfg.stages["review"], request=REQUEST, repo=repo
    )
    assert compiled.system.startswith("You are the REVIEW stage")
    assert "READ-ONLY" in compiled.system
    assert REQUEST not in compiled.system  # identity is static, the task is not
    assert compiled.user.startswith("## Original request")
    assert "WRITE BOUNDARY: NOTHING" in compiled.user


# --- the envelope contract follows the write boundary -----------------------

WRITER_CLAIM = '"changed_files": ["every file you created or modified, repo-relative"]'


def test_a_read_only_roles_contract_does_not_invite_it_to_list_changed_files(tmp_path: Path):
    """The reviewer writes nothing and is handed the builder's envelope; a contract
    asking for "every file you created or modified" made it echo that list and fail
    gate 3. Its own `changed_files` is always `[]` — see the golden-file docstring."""
    cfg = load_config(golden_repo(tmp_path))
    contract = prompts.envelope_contract(cfg.stages["review"])
    assert '"changed_files": []' in contract
    assert WRITER_CLAIM not in contract
    assert "You write nothing" in contract
    # …and the reminder reaches the prompt the reviewer is actually sent.
    assert "You write nothing" in prompts.compile_prompt(
        role=cfg.roles["review"], stage=cfg.stages["review"], request=REQUEST, repo=tmp_path
    ).combined


@pytest.mark.parametrize("role", ["plan", "build", "document"])
def test_a_writing_roles_contract_still_asks_for_every_file_it_touched(tmp_path: Path, role: str):
    contract = prompts.envelope_contract(load_config(golden_repo(tmp_path)).stages[role])
    assert WRITER_CLAIM in contract
    assert "changed_files` is verified against `git diff`" in contract


def test_only_a_role_that_owes_a_verdict_is_told_which_axis_carries_disapproval(tmp_path: Path):
    """The rule rides the compiled contract, not just `system.md`: a role library
    seeded before it existed keeps its own copy of that file forever."""
    cfg = load_config(golden_repo(tmp_path))
    review = prompts.envelope_contract(cfg.stages["review"])
    assert "`status` and the verdict are independent axes." in review
    assert "Any disapproval" in review and "`approved: false`" in review
    assert 'Reserve `status: "blocked"`\nfor being unable to review at all' in review
    for role in ("plan", "build", "document"):
        assert "independent axes" not in prompts.envelope_contract(cfg.stages[role])


def test_read_only_is_read_from_the_boundary_not_from_the_role_name(tmp_path: Path):
    """Any role pinned to `writes: []` inherits the read-only contract — nothing here
    knows that `review` is the one that happens to be read-only today."""
    repo = golden_repo(tmp_path)
    config = json.dumps({"boundaries": {"document": []}})
    (repo / "factory.config.json").write_text(config, encoding="utf-8")
    cfg = load_config(repo)
    assert cfg.stages["document"].read_only
    assert '"changed_files": []' in prompts.envelope_contract(cfg.stages["document"])


# --- artifact inlining + truncation ----------------------------------------


def test_artifacts_are_inlined_capped_and_the_truncation_is_stated(tmp_path: Path):
    repo = golden_repo(tmp_path)
    cfg = load_config(repo)
    compiled = prompts.compile_prompt(
        role=cfg.roles["build"],
        stage=cfg.stages["build"],
        request=REQUEST,
        repo=repo,
        previous_stage="plan",
        previous=as_envelope(PLAN_ENVELOPE),
        artifact_max_bytes=ARTIFACT_MAX_BYTES,
    )
    assert "### plan.md" in compiled.user
    assert PLAN_MD.strip() in compiled.user
    assert "… [truncated: 600 bytes total, first 200 shown]" in compiled.user
    assert compiled.user.count("spec line") == 20  # exactly the capped prefix
    assert "### docs/specs/gone.md\n(missing on disk)" in compiled.user


def test_an_artifactless_envelope_drops_the_whole_artifacts_section(tmp_path: Path):
    repo = golden_repo(tmp_path)
    cfg = load_config(repo)
    compiled = prompts.compile_prompt(
        role=cfg.roles["review"],
        stage=cfg.stages["review"],
        request=REQUEST,
        repo=repo,
        previous_stage="build",
        previous=as_envelope(BUILD_ENVELOPE),
    )
    assert "## Envelope from the build stage" in compiled.user
    assert "Artifacts named by" not in compiled.user  # no empty heading left behind


# --- resolution order ------------------------------------------------------


def test_the_global_library_beats_the_builtin(tmp_path: Path, isolated_roles: Path):
    repo = golden_repo(tmp_path)
    write_role_file(isolated_roles, "plan", SYSTEM_FILE, "GLOBAL PLANNER IDENTITY\n")
    role = RoleStore(repo).resolve("plan")

    assert role.system.strip() == "GLOBAL PLANNER IDENTITY"
    assert role.sources[SYSTEM_FILE] == str(isolated_roles / "plan" / SYSTEM_FILE)
    assert role.sources[USER_FILE] == roles.BUILT_IN  # per file, not per role


def test_a_repo_override_beats_the_global_library(tmp_path: Path, isolated_roles: Path):
    repo = golden_repo(tmp_path)
    write_role_file(isolated_roles, "build", SYSTEM_FILE, "GLOBAL BUILDER\n")
    write_role_file(isolated_roles, "build", USER_FILE, "GLOBAL TASK {{request}}\n")
    project = repo / roles.PROJECT_ROLES_SUBDIR
    write_role_file(project, "build", SYSTEM_FILE, "PROJECT BUILDER\n")

    role = RoleStore(repo).resolve("build")
    assert role.system.strip() == "PROJECT BUILDER"
    assert role.user.strip() == "GLOBAL TASK {{request}}"
    assert role.sources[SYSTEM_FILE] == str(project / "build" / SYSTEM_FILE)
    assert role.sources[USER_FILE] == str(isolated_roles / "build" / USER_FILE)
    assert role.sources[CONFIG_FILE] == roles.BUILT_IN


def test_an_untouched_machine_resolves_entirely_to_the_builtins(tmp_path: Path):
    role = RoleStore(golden_repo(tmp_path)).resolve("document")
    assert set(role.sources.values()) == {roles.BUILT_IN}
    assert role.config.model == "sonnet"


def test_the_roles_dir_argument_overrides_the_environment(tmp_path: Path):
    repo = golden_repo(tmp_path)
    elsewhere = tmp_path / "other-library"
    write_role_file(elsewhere, "plan", SYSTEM_FILE, "ELSEWHERE\n")
    assert RoleStore(repo, roles_dir=elsewhere).resolve("plan").system.strip() == "ELSEWHERE"
    assert RoleStore(repo).resolve("plan").system.strip() != "ELSEWHERE"


# --- seeding ---------------------------------------------------------------


def test_seeding_writes_every_default_file_once(tmp_path: Path, isolated_roles: Path):
    store = RoleStore(golden_repo(tmp_path))
    written = store.seed_if_new()

    assert len(written) == len(ROLES) * len(ROLE_FILES) + 1  # + the library README
    assert (isolated_roles / roles.LIBRARY_README).is_file()
    for role in ROLES:
        for filename in ROLE_FILES:
            assert (isolated_roles / role / filename).is_file()
    assert store.seed_if_new() == []  # an existing library is never re-seeded


def test_seeding_never_overwrites_an_edited_file(tmp_path: Path, isolated_roles: Path):
    store = RoleStore(golden_repo(tmp_path))
    store.seed()
    edited = isolated_roles / "plan" / SYSTEM_FILE
    edited.write_text("MY OWN PLANNER\n", encoding="utf-8")
    (isolated_roles / "plan" / USER_FILE).unlink()

    written = store.seed()
    assert written == [isolated_roles / "plan" / USER_FILE]  # only the gap was filled
    assert edited.read_text() == "MY OWN PLANNER\n"


def test_seeded_files_are_exactly_the_builtins(tmp_path: Path, isolated_roles: Path):
    store = RoleStore(golden_repo(tmp_path))
    store.seed()
    for role in ROLES:
        for filename in ROLE_FILES:
            on_disk = (isolated_roles / role / filename).read_text(encoding="utf-8")
            assert on_disk == roles.builtin_text(role, filename)


# --- templating ------------------------------------------------------------


def test_an_unknown_placeholder_is_a_config_error_naming_file_and_placeholder(
    tmp_path: Path, isolated_roles: Path
):
    repo = golden_repo(tmp_path)
    path = write_role_file(isolated_roles, "build", USER_FILE, "Do {{request}} with {{secrets}}.\n")
    with pytest.raises(RoleError) as excinfo:
        RoleStore(repo).resolve("build")

    message = str(excinfo.value)
    assert str(path) in message
    assert "{{secrets}}" in message
    assert "request" in message  # and it lists what IS available


def test_the_run_refuses_to_start_on_a_broken_template(tmp_path: Path, isolated_roles: Path):
    repo = golden_repo(tmp_path)
    write_role_file(isolated_roles, "review", SYSTEM_FILE, "You judge {{everything}}.\n")
    with pytest.raises(RoleError):
        load_config(repo)


def test_every_documented_variable_is_supplied_and_no_more(tmp_path: Path):
    repo = golden_repo(tmp_path)
    cfg = load_config(repo)
    values = prompts.template_values(stage=cfg.stages["plan"], request=REQUEST, repo=repo)
    assert set(values) == set(roles.VARIABLES)


def test_the_builtin_templates_only_use_documented_variables():
    for role in ROLES:
        for filename in (SYSTEM_FILE, USER_FILE):
            for name in roles.placeholder_names(roles.builtin_text(role, filename)):
                assert name in roles.VARIABLES, f"{role}/{filename} uses {{{{{name}}}}}"


def test_a_placeholder_may_be_written_with_spaces(tmp_path: Path, isolated_roles: Path):
    repo = golden_repo(tmp_path)
    write_role_file(isolated_roles, "plan", USER_FILE, "Task: {{ request }}\n")
    cfg = load_config(repo)
    compiled = prompts.compile_prompt(
        role=cfg.roles["plan"], stage=cfg.stages["plan"], request=REQUEST, repo=repo
    )
    assert compiled.user == f"Task: {REQUEST}"


def test_artifact_content_that_looks_like_a_placeholder_is_left_alone(tmp_path: Path):
    repo = golden_repo(tmp_path)
    (repo / "plan.md").write_text("Use {{request}} literally.\n", encoding="utf-8")
    cfg = load_config(repo)
    compiled = prompts.compile_prompt(
        role=cfg.roles["build"],
        stage=cfg.stages["build"],
        request=REQUEST,
        repo=repo,
        previous_stage="plan",
        previous=as_envelope({**PLAN_ENVELOPE, "artifacts": ["plan.md"]}),
    )
    assert "Use {{request}} literally." in compiled.user  # substitution never recurses


# --- role.json -------------------------------------------------------------


def test_the_builtin_role_json_agrees_with_the_config_defaults():
    """role.json is a lower layer of the same truth, never a second one."""
    for role in ROLES:
        data = json.loads(roles.builtin_text(role, CONFIG_FILE))
        assert data["model"] == DEFAULT_MODELS[role]
        assert data["writes"] == DEFAULT_BOUNDARIES[role]
        read_only = role in roles.READ_ONLY_ROLES
        expected = list(BASE_DISALLOWED) + (list(WRITE_TOOLS) if read_only else [])
        assert data["disallowed_tools"] == expected
        assert data["purpose"]


def test_role_json_sets_the_model_and_the_boundary(tmp_path: Path, isolated_roles: Path):
    repo = golden_repo(tmp_path)
    write_role_file(
        isolated_roles,
        "plan",
        CONFIG_FILE,
        json.dumps({"model": "haiku", "writes": ["notes/**"], "purpose": "cheap planning"}),
    )
    cfg = load_config(repo)
    assert cfg.stages["plan"].model == "haiku"
    assert cfg.stages["plan"].boundary == ["notes/**"]
    assert cfg.roles["plan"].config.purpose == "cheap planning"
    assert cfg.stages["plan"].disallowed_tools == BASE_DISALLOWED  # unset key falls through


def test_factory_config_json_beats_role_json(tmp_path: Path, isolated_roles: Path):
    repo = golden_repo(tmp_path)
    write_role_file(
        isolated_roles, "plan", CONFIG_FILE, json.dumps({"model": "haiku", "writes": ["notes/**"]})
    )
    (repo / "factory.config.json").write_text(
        json.dumps({"models": {"plan": "opus"}, "boundaries": {"plan": ["spec.md"]}}),
        encoding="utf-8",
    )
    cfg = load_config(repo)
    assert cfg.stages["plan"].model == "opus"
    assert cfg.stages["plan"].boundary == ["spec.md"]


def test_the_cli_model_flag_beats_everything(tmp_path: Path, isolated_roles: Path):
    repo = golden_repo(tmp_path)
    write_role_file(isolated_roles, "plan", CONFIG_FILE, json.dumps({"model": "haiku"}))
    (repo / "factory.config.json").write_text(
        json.dumps({"models": {"plan": "opus"}}), encoding="utf-8"
    )
    assert load_config(repo, model_override="sonnet").stages["plan"].model == "sonnet"


def test_a_null_writes_is_unrestricted_and_an_empty_one_is_read_only(
    tmp_path: Path, isolated_roles: Path
):
    repo = golden_repo(tmp_path)
    write_role_file(isolated_roles, "plan", CONFIG_FILE, json.dumps({"writes": None}))
    write_role_file(isolated_roles, "document", CONFIG_FILE, json.dumps({"writes": []}))
    cfg = load_config(repo)
    assert cfg.stages["plan"].boundary is None
    assert cfg.stages["document"].boundary == []


def test_role_json_can_change_the_disallowed_tools(tmp_path: Path, isolated_roles: Path):
    repo = golden_repo(tmp_path)
    write_role_file(
        isolated_roles, "build", CONFIG_FILE, json.dumps({"disallowed_tools": ["WebFetch"]})
    )
    assert load_config(repo).stages["build"].disallowed_tools == ("WebFetch",)


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        ("{not json", "not valid JSON"),
        ("[]", "must contain a JSON object"),
        (json.dumps({"modl": "haiku"}), "unknown key(s) modl"),
        (json.dumps({"model": 3}), '"model" must be'),
        (json.dumps({"writes": "docs/**"}), '"writes" must be'),
        (json.dumps({"disallowed_tools": "Bash"}), '"disallowed_tools" must be'),
    ],
)
def test_a_malformed_role_json_is_an_error_naming_the_file(
    tmp_path: Path, isolated_roles: Path, body: str, fragment: str
):
    repo = golden_repo(tmp_path)
    path = write_role_file(isolated_roles, "review", CONFIG_FILE, body)
    with pytest.raises(RoleError) as excinfo:
        load_config(repo)
    assert str(path) in str(excinfo.value)
    assert fragment in str(excinfo.value)


def test_a_bad_roles_dir_setting_is_refused(tmp_path: Path):
    repo = golden_repo(tmp_path)
    (repo / "factory.config.json").write_text(json.dumps({"roles_dir": ""}), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(repo)


def test_a_relative_roles_dir_is_resolved_against_the_repo(tmp_path: Path):
    repo = golden_repo(tmp_path)
    write_role_file(repo / "library", "plan", SYSTEM_FILE, "REPO LIBRARY\n")
    (repo / "factory.config.json").write_text(
        json.dumps({"roles_dir": "library"}), encoding="utf-8"
    )
    cfg = load_config(repo)
    assert cfg.roles["plan"].system.strip() == "REPO LIBRARY"
    assert cfg.roles_dir == repo / "library"


# --- required variables ----------------------------------------------------


def test_a_user_template_that_drops_the_request_is_refused_at_startup(
    tmp_path: Path, isolated_roles: Path
):
    """The silent degradation this whole store exists to catch: it renders fine."""
    repo = golden_repo(tmp_path)
    path = write_role_file(
        isolated_roles, "build", USER_FILE, "Implement the plan.\n\n{{envelope_contract}}\n"
    )
    with pytest.raises(RoleError) as excinfo:
        load_config(repo)

    message = str(excinfo.value)
    assert str(path) in message  # the file
    assert "build" in message  # the role
    assert "{{request}}" in message  # the variable


def test_a_role_with_no_role_json_is_validated_too(tmp_path: Path, isolated_roles: Path):
    repo = golden_repo(tmp_path)
    write_role_file(isolated_roles, "plan", USER_FILE, "Plan something.\n")
    assert not (isolated_roles / "plan" / CONFIG_FILE).exists()
    with pytest.raises(RoleError, match=r"\{\{request\}\}"):
        RoleStore(repo).resolve("plan")


def test_a_repo_override_is_held_to_the_same_requirement(tmp_path: Path, isolated_roles: Path):
    repo = golden_repo(tmp_path)
    write_role_file(isolated_roles, "review", USER_FILE, "Review {{request}}.\n")
    path = write_role_file(repo / roles.PROJECT_ROLES_SUBDIR, "review", USER_FILE, "Review it.\n")
    with pytest.raises(RoleError) as excinfo:
        load_config(repo)
    assert str(path) in str(excinfo.value)


def test_the_system_template_requires_nothing(tmp_path: Path, isolated_roles: Path):
    """Identity is static — forcing {{request}} into it would be a different rule."""
    repo = golden_repo(tmp_path)
    write_role_file(isolated_roles, "document", SYSTEM_FILE, "You write docs.\n")
    assert roles.builtin_requirements("document")[SYSTEM_FILE] == ()
    assert RoleStore(repo).resolve("document").system.strip() == "You write docs."


def test_the_builtin_templates_satisfy_their_own_requirements():
    for role in ROLES:
        required = roles.required_variables(role, roles.RoleConfig())
        for filename in (SYSTEM_FILE, USER_FILE):
            roles.check_required(
                roles.builtin_text(role, filename),
                role=role,
                filename=filename,
                required=required[filename],
                source=filename,
            )


def test_role_json_can_require_more_than_the_floor(tmp_path: Path, isolated_roles: Path):
    repo = golden_repo(tmp_path)
    write_role_file(
        isolated_roles, "plan", CONFIG_FILE, json.dumps({"requires": {USER_FILE: ["boundary"]}})
    )
    write_role_file(isolated_roles, "plan", USER_FILE, "Plan {{request}}.\n")
    with pytest.raises(RoleError, match=r"\{\{boundary\}\}"):
        load_config(repo)

    write_role_file(isolated_roles, "plan", USER_FILE, "Plan {{request}}.\n\n{{boundary}}\n")
    assert load_config(repo).roles["plan"].config.requires == {USER_FILE: ("boundary",)}


def test_role_json_cannot_require_less_than_the_floor(tmp_path: Path, isolated_roles: Path):
    """The floor is in code because role.json is as editable as the template it guards."""
    repo = golden_repo(tmp_path)
    write_role_file(isolated_roles, "plan", CONFIG_FILE, json.dumps({"requires": {USER_FILE: []}}))
    write_role_file(isolated_roles, "plan", USER_FILE, "Plan whatever you like.\n")
    with pytest.raises(RoleError, match=r"\{\{request\}\}"):
        load_config(repo)


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        (json.dumps({"requires": ["request"]}), '"requires" must be an object'),
        (json.dumps({"requires": {"prompt.md": ["request"]}}), "is not a template file"),
        (json.dumps({"requires": {USER_FILE: "request"}}), "must be a list of variable names"),
        (json.dumps({"requires": {USER_FILE: ["secrets"]}}), "unknown placeholder {{secrets}}"),
    ],
)
def test_a_malformed_requires_is_an_error_naming_the_file(
    tmp_path: Path, isolated_roles: Path, body: str, fragment: str
):
    repo = golden_repo(tmp_path)
    path = write_role_file(isolated_roles, "build", CONFIG_FILE, body)
    with pytest.raises(RoleError) as excinfo:
        load_config(repo)
    assert str(path) in str(excinfo.value)
    assert fragment in str(excinfo.value)


# --- resolution layers ------------------------------------------------------


def test_every_file_records_the_layer_that_resolved_it(tmp_path: Path, isolated_roles: Path):
    repo = golden_repo(tmp_path)
    write_role_file(isolated_roles, "build", SYSTEM_FILE, "GLOBAL BUILDER\n")
    write_role_file(isolated_roles, "build", USER_FILE, "Build {{request}}.\n")
    write_role_file(repo / roles.PROJECT_ROLES_SUBDIR, "build", SYSTEM_FILE, "PROJECT BUILDER\n")

    role = RoleStore(repo).resolve("build")
    assert role.layers == {
        SYSTEM_FILE: roles.LAYER_REPO,
        USER_FILE: roles.LAYER_LIBRARY,
        CONFIG_FILE: roles.LAYER_BUILTIN,
    }
    assert role.layer_mix == "repo+library+builtin"


def test_an_untouched_machine_reports_every_file_as_builtin(tmp_path: Path):
    role = RoleStore(golden_repo(tmp_path)).resolve("plan")
    assert set(role.layers.values()) == {roles.LAYER_BUILTIN}
    assert role.layer_mix == roles.LAYER_BUILTIN


def test_a_file_missing_from_an_existing_library_warns(tmp_path: Path, isolated_roles: Path):
    """The fingerprint of a deleted file: the role reverts to hardcoded text."""
    repo = golden_repo(tmp_path)
    RoleStore(repo).seed()
    (isolated_roles / "review" / USER_FILE).unlink()

    warnings = load_config(repo).warnings
    gap = [w for w in warnings if "is missing" in w]
    assert len(gap) == 1
    assert str(isolated_roles / "review" / USER_FILE) in gap[0]
    assert "review falls back to the built-in text" in gap[0]


def test_a_repo_override_never_warns(tmp_path: Path, isolated_roles: Path):
    repo = golden_repo(tmp_path)
    RoleStore(repo).seed()
    write_role_file(repo / roles.PROJECT_ROLES_SUBDIR, "plan", SYSTEM_FILE, "PROJECT PLANNER\n")

    cfg = load_config(repo)
    assert cfg.warnings == ()
    assert cfg.roles["plan"].layers[SYSTEM_FILE] == roles.LAYER_REPO


def test_an_unseeded_library_does_not_warn_about_every_builtin(tmp_path: Path):
    assert load_config(golden_repo(tmp_path)).warnings == ()


# --- role names -------------------------------------------------------------


def test_the_role_name_charset_is_the_one_masterwork_indexes():
    # backend/app/providers/masterwork_roles.py — the two must stay identical or a
    # role runs here and is invisible there.
    assert roles.ROLE_NAME_PATTERN == r"[a-z0-9][a-z0-9_-]{0,63}"
    assert all(roles.is_valid_role_name(role) for role in ROLES)


@pytest.mark.parametrize(
    "name", ["Plan", "plan:system", "plan/build", "", "-plan", "_plan", "pl an", "p" * 65]
)
def test_an_unrepresentable_role_name_is_rejected(name: str):
    assert not roles.is_valid_role_name(name)


@pytest.mark.parametrize("name", ["plan", "p", "code-review", "code_review_2", "p" * 64])
def test_a_representable_role_name_is_accepted(name: str):
    assert roles.is_valid_role_name(name)


def test_resolving_an_unrepresentable_role_says_why(tmp_path: Path):
    with pytest.raises(RoleError) as excinfo:
        RoleStore(golden_repo(tmp_path)).resolve("Plan")
    assert "masterwork:agent:<role>:<part>" in str(excinfo.value)


def test_a_role_directory_masterwork_cannot_index_is_reported(
    tmp_path: Path, isolated_roles: Path
):
    repo = golden_repo(tmp_path)
    RoleStore(repo).seed()
    (isolated_roles / "Refactor").mkdir()
    (isolated_roles / ".git").mkdir()  # not a role, never reported
    (repo / roles.PROJECT_ROLES_SUBDIR / "code:review").mkdir(parents=True)

    warnings = [w for w in load_config(repo).warnings if "cannot be represented" in w]
    assert len(warnings) == 2
    assert any(str(isolated_roles / "Refactor") in w for w in warnings)
    assert any(str(repo / roles.PROJECT_ROLES_SUBDIR / "code:review") in w for w in warnings)
