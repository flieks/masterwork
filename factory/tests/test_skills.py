"""The skill guidance: which role is told to load what, and what it must never name.

Measured before this existed: 39 stage children across 11 pipeline runs made only
Read/Edit/Glob/Write/Grep calls — zero `Skill` calls — while the user's skill library
sat unread. The guidance lives in each role's `system.md` rather than in the shared
`conventions.md`, because the choice of skill is the one thing that differs per role.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from adw import prompts
from adw.config import load_config
from adw.roles import ROLES, SYSTEM_FILE, builtin_text

REQUEST = "Add a /health endpoint"

# Every skill the built-in prompts are allowed to name. A skill that is not installed
# is a wasted turn and a confusing failure, so this list is the promise that each name
# was checked — against `~/.claude/skills/` and against the tools the role actually has.
NAMEABLE = (
    "backend-dev",
    "frontend-dev",
    "mobile-dev",
    "tdd",
    "concise-comments",
    "architecture-review",
)

# `code-review` is installed and is the obvious name for the review stage — and it
# opens by spawning two parallel sub-agents over `git diff`. Every role disallows
# Bash and Task, so it can only fail. The review identity names it to refuse it, and
# says why the refusal costs nothing: its method is carried in the prompt already
# (see test_review_method.py), so only the machinery is missing.
UNUSABLE = "code-review"

STACK_SKILLS = {"backend-dev": "Python/FastAPI", "frontend-dev": "React", "mobile-dev": "Expo"}


def compiled(repo: Path, role: str) -> prompts.CompiledPrompt:
    cfg = load_config(repo, workflow="scout" if role == "scout" else None)
    return prompts.compile_prompt(
        role=cfg.roles[role], stage=cfg.stages[role], request=REQUEST, repo=repo
    )


def flat(text: str) -> str:
    """Prompt prose is hand-wrapped; where a sentence breaks is not the contract."""
    return " ".join(text.split())


@pytest.mark.parametrize("role", ["plan", "build", "review"])
def test_the_working_roles_are_told_to_load_the_skill_for_the_stack(tmp_path: Path, role: str):
    system = flat(compiled(tmp_path, role).system)
    assert "Skill tool" in system
    for skill in STACK_SKILLS:
        assert f"`{skill}`" in system, f"{role} does not name {skill}"


@pytest.mark.parametrize("role", ["plan", "build", "review"])
def test_the_guidance_is_conditional_never_load_everything(tmp_path: Path, role: str):
    """Loading a skill costs tokens on every run, so the rule is "when the work touches
    X, load Y" — with the mismatch named, since a mobile skill on a Python backend is
    the exact waste this is meant to avoid."""
    system = flat(compiled(tmp_path, role).system)
    assert "only ones that match" in system or "only one that matches" in system
    assert "a mobile skill on a Python backend is a" in system


def test_the_builder_is_told_about_tests_and_comments(tmp_path: Path):
    system = flat(compiled(tmp_path, "build").system)
    assert "`tdd` when you write the tests" in system
    assert "`concise-comments` when you write comments" in system


def test_the_reviewer_is_told_to_refuse_the_skill_it_cannot_run(tmp_path: Path):
    """An unexplained prohibition invites the model to work around it. The clause says
    what is missing (the sub-agents and the shell) and what is not (the method)."""
    system = flat(compiled(tmp_path, "review").system)
    assert f"Not `{UNUSABLE}` —" in system
    assert "its method is the two axes above and you already have it" in system
    assert "`architecture-review`" in system


def test_the_documenting_role_is_given_no_skill_to_load(tmp_path: Path):
    """Nothing in the library documents documentation: naming one would be a wasted
    turn, and its prompt stays byte-identical to the pre-skill golden."""
    assert "Skill tool" not in flat(compiled(tmp_path, "document").system)


def skill_guidance(role: str) -> str:
    """The paragraph of a role's identity that talks about loading skills, or ""."""
    blocks = builtin_text(role, SYSTEM_FILE).split("\n\n")
    return next((block for block in blocks if "Skill tool" in block), "")


@pytest.mark.parametrize("role", ROLES)
def test_no_builtin_prompt_names_a_skill_nobody_vetted(role: str):
    """Every skill named in the guidance is one that was checked to exist in
    `~/.claude/skills/` AND to be runnable without Bash or Task. A typo or an invented
    name does not fail loudly — it costs the stage one silent, wasted turn."""
    named = set(re.findall(r"`([a-z0-9][a-z0-9-]*)`", flat(skill_guidance(role))))
    assert named <= {*NAMEABLE, UNUSABLE}, f"{role}/{SYSTEM_FILE} names {named - set(NAMEABLE)}"


def test_the_guidance_rides_the_system_prompt_not_the_task(tmp_path: Path):
    """`system.md` is re-sent on every `--resume`; the user message is not. A stage
    that is corrected twice must not lose the skill it was told to work from."""
    for role in ("plan", "build", "review"):
        prompt = compiled(tmp_path, role)
        assert "Skill tool" in flat(prompt.system)
        assert "Skill tool" not in flat(prompt.user)
