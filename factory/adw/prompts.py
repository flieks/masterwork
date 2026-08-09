"""Stage prompts: request + previous envelope + named artifacts. Never a transcript."""

from __future__ import annotations

from pathlib import Path

from adw.config import Stage
from adw.envelopes import REQUIRED_FIELDS, Envelope

ROLE_HEADERS = {
    "plan": """You are the PLAN stage of a deterministic, unattended pipeline.
Read the repo, then write an implementation plan to `plan.md`: the change in one
paragraph, the files to add or change with why, the data/contract impact, the
test strategy, and the risks. Be concrete about paths. Do not implement anything.""",
    "build": """You are the BUILD stage of a deterministic, unattended pipeline.
Implement the plan in the repo, tests included. You have no shell: the runner
executes the repo's own test and lint commands after you finish, so make the code
correct rather than claiming it is. Keep the change minimal and in the repo's style.""",
    "review": """You are the REVIEW stage of a deterministic, unattended pipeline.
Review the work on two axes: (1) Standards — does it follow this repo's documented
conventions and layering? (2) Spec — does it do what the original request and the
plan asked, no more and no less? You are READ-ONLY: you write no files. Report
findings that must be fixed in `blocking` (one clear, actionable sentence each,
naming the file); set `approved: true` only when `blocking` is empty.""",
    "document": """You are the DOCUMENT stage of a deterministic, unattended pipeline.
Update the user-facing documentation to match what was built — README, docs pages,
and the changelog if the repo keeps one. Change no source code. If nothing needs
documenting, say so in the summary and change nothing.""",
}

UNATTENDED_RULE = """UNATTENDED RUN — you cannot ask anyone anything.
Every question you would have asked becomes an entry in `assumptions`. If an
assumption is too dangerous to make silently (destructive migration, credential
rotation, data deletion), do not make it: return `status: "blocked"` with the
reason in `summary`, and the run stops cleanly."""

_CONTRACT_TEMPLATE = """END YOUR REPLY WITH EXACTLY ONE FENCED ```json BLOCK AND NOTHING AFTER IT.
The runner parses the LAST fenced json block; any prose after it fails the gate.

```json
{{
  "status": "ok | blocked | failed",
  "summary": "one paragraph — the first line becomes the commit message",
  "artifacts": ["paths you wrote that the next stage should read"],
  "notes_for_next_agent": "what the next stage needs to know",
  "changed_files": ["every file you created or modified, repo-relative"],
  "approved": false,
  "blocking": [],
  "assumptions": ["anything you would have asked the user"]
}}
```

Required for the {stage} role: {required}.
`changed_files` is verified against `git diff` — a file you claim but did not touch,
or touched but did not claim, fails the gate."""


def envelope_contract(stage: str) -> str:
    required = ", ".join(REQUIRED_FIELDS.get(stage, ("status", "summary")))
    return _CONTRACT_TEMPLATE.format(stage=stage, required=required)


def boundary_clause(stage: Stage) -> str:
    if stage.boundary is None:
        return "WRITE BOUNDARY: any path inside this repository."
    if not stage.boundary:
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


def stage_prompt(
    *,
    stage: Stage,
    request: str,
    repo: Path,
    previous_stage: str | None = None,
    previous: Envelope | None = None,
    artifact_max_bytes: int = 20_000,
    extra: str = "",
) -> str:
    parts = [ROLE_HEADERS.get(stage.name, f"You are the {stage.name.upper()} stage.")]
    parts.append(f"## Original request\n{request.strip()}")
    if previous is not None and previous_stage:
        parts.append(
            f"## Envelope from the {previous_stage} stage\n```json\n{previous.to_json()}\n```"
        )
        artifacts = read_artifacts(repo, previous.artifacts, artifact_max_bytes)
        if artifacts:
            parts.append(f"## Artifacts named by the {previous_stage} stage\n{artifacts}")
    if extra:
        parts.append(extra)
    parts.append(boundary_clause(stage))
    parts.append(UNATTENDED_RULE)
    parts.append(envelope_contract(stage.name))
    return "\n\n".join(parts)


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
