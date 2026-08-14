"""The on-disk contract between a paused run and whoever answers it.

`questions.json` — written by the factory when a `--interview` run pauses,
read-only for the backend:

    {
      "run_id": "a1b2c3d4",
      "stage": "plan",
      "asked_at": "2026-08-14T10:00:00+00:00",
      "questions": [{"id": "q1", "question": "<assumption text, verbatim>"}]
    }

`answers.json` — written by the backend once the user answers, read by
`--resume` on the factory side:

    {
      "answered_at": "2026-08-14T10:05:00+00:00",
      "answers": [{"id": "q1", "question": "<verbatim>", "answer": "<user text>"}]
    }

Ids are `q1..qN` by position. Both files are written atomically (tmp + `os.replace`).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

QUESTIONS_FILENAME = "questions.json"
ANSWERS_FILENAME = "answers.json"


class InterviewError(Exception):
    """answers.json is missing, malformed, or does not answer the recorded questions."""


@dataclass(frozen=True)
class Question:
    id: str
    question: str


@dataclass(frozen=True)
class Answer:
    id: str
    question: str
    answer: str


def _now() -> str:
    return datetime.now(UTC).isoformat()


def questions_from_assumptions(assumptions: list[str]) -> list[Question]:
    """One question per non-blank assumption, verbatim, numbered q1..qN in order."""
    out: list[Question] = []
    for text in assumptions:
        stripped = text.strip()
        if stripped:
            out.append(Question(id=f"q{len(out) + 1}", question=stripped))
    return out


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, path)


def write_questions(
    run_dir: Path, run_id: str, questions: list[Question], *, stage: str = "plan"
) -> Path:
    path = run_dir / QUESTIONS_FILENAME
    _atomic_write(
        path,
        {
            "run_id": run_id,
            "stage": stage,
            "asked_at": _now(),
            "questions": [{"id": q.id, "question": q.question} for q in questions],
        },
    )
    return path


def read_questions(run_dir: Path) -> list[Question]:
    """Empty when the file is absent or unreadable — a reader here never blocks
    on a run that has not (yet, or ever) paused."""
    path = run_dir / QUESTIONS_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    raw = data.get("questions") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if isinstance(item, dict) and isinstance(item.get("id"), str) and isinstance(
            item.get("question"), str
        ):
            out.append(Question(id=item["id"], question=item["question"]))
    return out


def read_answers(run_dir: Path) -> list[Answer]:
    """Raises InterviewError when the file is missing, malformed, or does not
    answer exactly the questions `questions.json` recorded — one each."""
    path = run_dir / ANSWERS_FILENAME
    if not path.is_file():
        raise InterviewError(
            f"no {ANSWERS_FILENAME} at {path} — answer the questions first "
            "(through the UI, or by writing the file by hand) before resuming."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InterviewError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("answers"), list):
        raise InterviewError(f'{path} must be an object with an "answers" list')

    questions = {q.id: q.question for q in read_questions(run_dir)}
    answers: list[Answer] = []
    seen: set[str] = set()
    for item in data["answers"]:
        if not isinstance(item, dict):
            raise InterviewError(f"{path}: every answer must be an object")
        qid, text = item.get("id"), item.get("answer")
        if not isinstance(qid, str) or not isinstance(text, str) or not text.strip():
            raise InterviewError(f"{path}: every answer needs a non-blank 'id' and 'answer'")
        if qid not in questions:
            raise InterviewError(f'{path}: "{qid}" does not match any recorded question')
        if qid in seen:
            raise InterviewError(f'{path}: question "{qid}" is answered more than once')
        seen.add(qid)
        answers.append(Answer(id=qid, question=questions[qid], answer=text))

    missing = sorted(set(questions) - seen)
    if missing:
        raise InterviewError(f"{path}: missing answer(s) for {', '.join(missing)}")
    return answers


def prompt_block(answers: list[Answer]) -> str:
    """Folded into the build stage's user prompt, after the plan envelope — empty
    when there are no answers, so a non-interview build prompt is unchanged."""
    if not answers:
        return ""
    lines = [
        "ANSWERS TO THE PLAN STAGE'S QUESTIONS — honour these over the planner's own "
        "guesses; each answer replaces the assumption it was asked about:"
    ]
    for a in answers:
        lines.append(f"\nQ: {a.question}\nA: {a.answer}")
    return "\n".join(lines)
