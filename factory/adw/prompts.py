"""Compile a role's templates into the two prompts a turn actually sends.

The text itself lives in the role store (`adw/roles.py` + `~/.masterwork/agents`);
this module only computes the variables, renders, and keeps the audit copy. What
a stage sees is still request + previous envelope + named artifacts, never a
transcript.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from adw.config import Stage
from adw.envelopes import REQUIRED_FIELDS, Envelope, owes_a_verdict, owes_findings
from adw.roles import NONE_MARKER, ResolvedRole, render

PROMPTS_SUBDIR = "prompts"

_CONTRACT_TEMPLATE = """END YOUR REPLY WITH EXACTLY ONE FENCED ```json BLOCK AND NOTHING AFTER IT.
The runner parses the LAST fenced json block; any prose after it fails the gate.

```json
{{
  "status": "ok | blocked | failed",
  "summary": "one paragraph — the first line becomes the commit message",
  "artifacts": {artifacts},
  "notes_for_next_agent": "what the next stage needs to know",
  "changed_files": {changed_files},
  "approved": false,
  "blocking": [],{extra}
  "assumptions": ["anything you would have asked the user"]
}}
```

Required for the {stage} role: {required}.
{rule}{verdict}"""

# One extra skeleton line, for the one role whose output IS a list of findings.
# Empty for every other role, so their compiled contract is unchanged to the byte.
_FINDINGS_FIELD = '\n  "findings": ["one self-contained sentence each, naming the file"],'

_WRITER_FIELDS = {
    "artifacts": '["paths you wrote that the next stage should read"]',
    "changed_files": '["every file you created or modified, repo-relative"]',
    "rule": (
        "`changed_files` is verified against `git diff` — a file you claim but did not touch,\n"
        "or touched but did not claim, fails the gate."
    ),
}

# A role whose boundary is `[]` writes nothing, so the generic wording asked it to
# report files it cannot have. Handed the upstream envelope, reviewers copied the
# builder's list, gate 3 failed it as "claimed but not changed on disk", and the run
# spent a correction — sometimes reading that correction as a finding against the
# builder and failing the pipeline. The contract now matches the boundary.
_READ_ONLY_FIELDS = {
    "artifacts": "[]",
    "changed_files": "[]",
    "rule": (
        "You write nothing, so `artifacts` and `changed_files` are ALWAYS empty for this\n"
        "role. `changed_files` is verified against `git diff`: repeating files another\n"
        "stage changed — including any named in the envelope you were given — fails the\n"
        "gate. Report what you find in `summary` and `blocking`, never as a file claim."
    ),
}


# `status` is the one field whose meaning is not self-evident, and getting it wrong
# is invisible: a review that disapproves as `status: "blocked"` instead of
# `approved: false` reads as a clean stop and skips the review→build loop it exists
# to drive (observed for real — the reviewer rejected marker comments and the run
# ended instead of asking the builder to remove them). The rule is compiled from
# code on every turn rather than living only in the role's `system.md`, because a
# role library seeded before this rule existed keeps its own copy of that file
# forever and would never see it.
_VERDICT_RULE = """

`status` and the verdict are independent axes. `status` reports whether the review
itself could be carried out; `approved` + `blocking` carry your judgement of the work.
Any disapproval — including of a change you consider dangerous — is `approved: false`
with the reasons in `blocking`, NEVER `status: "blocked"`. Reserve `status: "blocked"`
for being unable to review at all (the files or artifacts you were told to read are
missing or unreadable): it takes an EMPTY `blocking` list and the reason in `summary`.
Findings listed under a non-ok `status` are read as a rejection and loop back to the
builder anyway."""


def envelope_contract(stage: Stage) -> str:
    """The envelope skeleton, worded for this role's own write boundary."""
    fields = _READ_ONLY_FIELDS if stage.read_only else _WRITER_FIELDS
    required = ", ".join(REQUIRED_FIELDS.get(stage.name, ("status", "summary")))
    verdict = _VERDICT_RULE if owes_a_verdict(stage.name) else ""
    extra = _FINDINGS_FIELD if owes_findings(stage.name) else ""
    return _CONTRACT_TEMPLATE.format(
        stage=stage.name, required=required, verdict=verdict, extra=extra, **fields
    )


def boundary_clause(stage: Stage) -> str:
    if stage.boundary is None:
        return "WRITE BOUNDARY: any path inside this repository."
    if stage.read_only:
        return (
            "WRITE BOUNDARY: NOTHING. This role is read-only — do not create, edit, "
            "or delete any file. Any write is reverted by the runner and fails the gate."
        )
    listed = "\n".join(f"  - {p}" for p in stage.boundary)
    return (
        "WRITE BOUNDARY: you may only create or modify paths matching:\n"
        f"{listed}\n"
        "Anything else is reverted by the runner and fails the gate."
    )


def read_artifacts(repo: Path, paths: list[str], max_bytes: int) -> str:
    """Inline the previous stage's artifacts, size-capped, truncation noted."""
    if not paths:
        return ""
    sections = []
    for rel in paths:
        path = repo / rel
        if not path.is_file():
            sections.append(f"### {rel}\n(missing on disk)")
            continue
        try:
            raw = path.read_bytes()
        except OSError as exc:
            sections.append(f"### {rel}\n(unreadable: {exc})")
            continue
        text = raw[:max_bytes].decode("utf-8", errors="replace")
        if len(raw) > max_bytes:
            text += f"\n\n… [truncated: {len(raw)} bytes total, first {max_bytes} shown]"
        sections.append(f"### {rel}\n```\n{text}\n```")
    return "\n\n".join(sections)


@dataclass(frozen=True)
class CompiledPrompt:
    system: str
    user: str

    @property
    def combined(self) -> str:
        """What the agent reads, in order — the shape the prompts had before the split."""
        return f"{self.system}\n\n{self.user}"


def template_values(
    *,
    stage: Stage,
    request: str,
    repo: Path,
    previous_stage: str | None = None,
    previous: Envelope | None = None,
    artifact_max_bytes: int = 20_000,
    conventions: str = "",
) -> dict[str, str]:
    """Every variable a role template may use. Documented in `roles.VARIABLES`."""
    return {
        "role": stage.name,
        "repo": str(repo),
        "request": request.strip(),
        # Empty when no conventions file exists, so the block leaves no trace.
        "conventions": conventions,
        "previous_stage": previous_stage or NONE_MARKER,
        "previous_envelope": previous.to_json() if previous is not None else NONE_MARKER,
        "artifacts": (
            read_artifacts(repo, previous.artifacts, artifact_max_bytes)
            if previous is not None
            else ""
        ),
        "boundary": boundary_clause(stage),
        "envelope_contract": envelope_contract(stage),
    }


def compile_prompt(
    *,
    role: ResolvedRole,
    stage: Stage,
    request: str,
    repo: Path,
    previous_stage: str | None = None,
    previous: Envelope | None = None,
    artifact_max_bytes: int = 20_000,
    conventions: str = "",
) -> CompiledPrompt:
    values = template_values(
        stage=stage,
        request=request,
        repo=repo,
        previous_stage=previous_stage,
        previous=previous,
        artifact_max_bytes=artifact_max_bytes,
        conventions=conventions,
    )
    return CompiledPrompt(
        system=render(role.system, values, source=role.sources["system.md"]),
        user=render(role.user, values, source=role.sources["user.md"]),
    )


def save_prompt_copy(run_dir: Path, role: str, turn: int, system: str, user: str) -> dict[str, str]:
    """The prompt as actually sent — a bad run is only diagnosable if this exists."""
    directory = run_dir / PROMPTS_SUBDIR / role
    directory.mkdir(parents=True, exist_ok=True)
    saved: dict[str, str] = {}
    for kind, text in (("system", system), ("user", user)):
        path = directory / f"{turn}.{kind}.md"
        path.write_text(text, encoding="utf-8")
        saved[kind] = str(path)
    return saved


# The runner already committed the previous turn, and re-snapshots the tree before
# each correction — so `changed_files` is scoped to the turn, not to the stage.
THIS_TURN_ONLY = (
    "Then re-emit the full envelope. Your earlier work is already committed, so "
    "`changed_files` must list exactly the files you change in THIS turn — no more, no less."
)


def review_correction(blocking: list[str], round_number: int, max_rounds: int) -> str:
    """The only thing a rejected review sends back into the builder session."""
    findings = "\n".join(f"  {i}. {item}" for i, item in enumerate(blocking, 1))
    return (
        f"REVIEW REJECTED (round {round_number} of {max_rounds}). "
        "Fix exactly these blocking findings and nothing else:\n"
        f"{findings}\n\n" + THIS_TURN_ONLY
    )


def checks_correction(detail: str, attempt: int, max_attempts: int) -> str:
    """Executed checks are facts; hand the builder the exit codes and output."""
    return (
        f"EXECUTED CHECKS FAILED (attempt {attempt} of {max_attempts}). "
        "The runner ran the repo's own commands and they did not pass:\n\n"
        f"{detail}\n\n"
        "Fix the cause — do not disable, skip, or weaken the checks. " + THIS_TURN_ONLY
    )
