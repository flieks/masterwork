"""Session launcher business logic: project listing/creation under
projects_root, spawning a detached factory run, and the interview state
machine (read a launch's questions, submit answers, spawn its resume).

Every path that reaches the filesystem goes through `resolve_within_roots`
(app/providers/base.py) — the same helper the asset write path uses — so a
traversal name or an out-of-root `project_path` can never escape.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.coding.service import FACTORY_SESSION_PREFIX
from app.api.v1.launcher import schemas
from app.api.v1.settings.service import read_settings
from app.config import settings as app_settings
from app.core.exceptions import (
    InterviewAnswerMismatchError,
    InterviewNotWaitingError,
    InvalidBrowsePathError,
    InvalidProjectNameError,
    LaunchFailedError,
    LaunchNotFoundError,
    ProjectCreationError,
    ProjectExistsError,
    ProjectPathOutsideRootError,
    RunNotFoundError,
    RunNotResumableError,
)
from app.db.models.launcher import MODE_INTERVIEW, SessionLaunch
from app.providers.base import resolve_within_roots
from app.repositories import launcher as launcher_repo
from app.services import factory_runs

_MAX_NAME_LEN = 100


def _validate_project_name(name: str) -> str:
    trimmed = name.strip()
    if not trimmed:
        raise InvalidProjectNameError("project name must not be empty")
    if len(trimmed) > _MAX_NAME_LEN:
        raise InvalidProjectNameError(f"project name must be at most {_MAX_NAME_LEN} characters")
    if "/" in trimmed or "\\" in trimmed or "\x00" in trimmed:
        raise InvalidProjectNameError("project name must not contain a path separator")
    if trimmed in (".", ".."):
        raise InvalidProjectNameError(f"project name must not be '{trimmed}'")
    if trimmed.startswith("."):
        raise InvalidProjectNameError("project name must not start with '.'")
    return trimmed


async def _projects_root(db: AsyncSession) -> Path:
    return Path((await read_settings(db)).projects_root)


async def list_projects(db: AsyncSession) -> list[schemas.LauncherProject]:
    root = await _projects_root(db)
    if not root.is_dir():
        return []
    return [
        schemas.LauncherProject(
            name=child.name, path=str(child), is_git_repo=(child / ".git").exists()
        )
        for child in sorted(root.iterdir(), key=lambda p: p.name)
        if child.is_dir() and not child.name.startswith(".")
    ]


def _validate_browse_path(value: str) -> Path:
    expanded = Path(value).expanduser()
    if not expanded.is_absolute():
        raise InvalidBrowsePathError(f"path must be absolute, got: {value}")
    try:
        resolved = expanded.resolve()
    except OSError as exc:  # e.g. a symlink loop
        raise InvalidBrowsePathError(f"path could not be resolved: {value}") from exc
    if not resolved.exists():
        raise InvalidBrowsePathError(f"path does not exist: {resolved}")
    if not resolved.is_dir():
        raise InvalidBrowsePathError(f"path is not a directory: {resolved}")
    return resolved


async def browse(db: AsyncSession, path: str | None) -> schemas.DirectoryListing:
    """Lists a directory's non-hidden subdirectories. Deliberately not confined
    to projects_root — this is how the picker leaves the current root, and
    PATCH /api/v1/settings already accepts any absolute path."""
    target = _validate_browse_path(path) if path is not None else await _projects_root(db)
    parent = target.parent if target.parent != target else None

    entries: list[schemas.DirectoryEntry] = []
    if target.is_dir():  # false only via the default branch, a vanished projects_root
        try:
            children = sorted(target.iterdir(), key=lambda p: p.name)
        except PermissionError as exc:
            raise InvalidBrowsePathError(f"path is not readable: {target}") from exc
        for child in children:
            try:
                if child.is_dir() and not child.name.startswith("."):
                    entries.append(schemas.DirectoryEntry(name=child.name, path=str(child)))
            except PermissionError:
                continue  # unreadable entry — skip it, don't fail the whole listing

    return schemas.DirectoryListing(
        path=str(target),
        parent=str(parent) if parent is not None else None,
        entries=entries,
        home=str(Path.home()),
    )


async def _git_init(path: Path) -> bool:
    """`git init -q` in `path`, async so it never blocks the event loop —
    mirrors app/services/asset_history.py's git pattern. Returns success."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "init",
            "-q",
            cwd=str(path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        return await proc.wait() == 0
    except OSError:
        return False


async def create_project(db: AsyncSession, name: str) -> schemas.LauncherProject:
    validated_name = _validate_project_name(name)
    root = await _projects_root(db)
    candidate = root / validated_name
    resolved = resolve_within_roots(candidate, [root])
    if resolved is None:
        raise InvalidProjectNameError(f"project name escapes projects_root: {name}")
    if resolved.exists():
        raise ProjectExistsError(f"a project named '{validated_name}' already exists")
    resolved.mkdir(parents=False)
    if not await _git_init(resolved):
        resolved.rmdir()  # leave projects_root clean for a retry
        raise ProjectCreationError(f"git init failed for {resolved}")
    return schemas.LauncherProject(name=resolved.name, path=str(resolved), is_git_repo=True)


def _log_path(launch_id: int) -> Path:
    return app_settings.masterwork_home / "launches" / f"{launch_id}.log"


async def launch(
    db: AsyncSession,
    body: schemas.LaunchRequest,
    spawner: Callable[..., int],
) -> schemas.SessionLaunchRead:
    root = await _projects_root(db)
    resolved = resolve_within_roots(Path(body.project_path), [root])
    if resolved is None or not resolved.is_dir():
        raise ProjectPathOutsideRootError(
            f"project_path must be a directory under {root}, got: {body.project_path}"
        )
    if not (resolved / ".git").exists():
        # factory/run.py exits 2 on a non-repo, and a detached process has
        # nowhere to surface that — caught here, synchronously, instead.
        raise ProjectPathOutsideRootError(f"project_path is not a git repository: {resolved}")

    # Only an interview launch gets a run id — an autonomous launch keeps
    # run_id=None and its argv byte-for-byte identical to before.
    is_interview = body.mode == schemas.LaunchMode.INTERVIEW
    run_id = factory_runs.new_run_id() if is_interview else None

    launch_row = await launcher_repo.create_launch(
        db,
        project_path=str(resolved),
        request_text=body.request_text,
        mode=body.mode.value,
        run_id=run_id,
    )
    log_path = _log_path(launch_row.id)
    try:
        pid = spawner(
            project_path=resolved,
            request_text=body.request_text,
            log_path=log_path,
            run_id=run_id,
            interview=is_interview,
        )
    except OSError as exc:
        await db.rollback()
        raise LaunchFailedError(f"could not start the factory run: {exc}") from exc

    await launcher_repo.set_pid(db, launch_row, pid)
    await db.commit()
    return schemas.SessionLaunchRead(
        id=launch_row.id,
        project_path=launch_row.project_path,
        request_text=launch_row.request_text,
        mode=schemas.LaunchMode(launch_row.mode),
        launched_at=launch_row.launched_at,
        pid=pid,
        run_id=launch_row.run_id,
        launched=True,
    )


# --- factory runs ---------------------------------------------------------


def _outcome(state: str, *, live: bool, accepted: bool) -> schemas.RunOutcome:
    """`state` is the process's, not the run's: a rejected run and an approved
    one both end `finished`, and a crashed one is left claiming `running`."""
    if live:
        return schemas.RunOutcome.RUNNING
    if state == "waiting_input":
        return schemas.RunOutcome.WAITING
    if accepted:
        return schemas.RunOutcome.DONE
    if state == "stopped":
        return schemas.RunOutcome.STOPPED
    return schemas.RunOutcome.FAILED


def _resume_hint(
    *, live: bool, accepted: bool, ref: str | None, branches: set[str] | None
) -> str | None:
    """Why a resume is refused, in the user's words. None means it is offered.
    Mirrors factory plan_resume's own gate (factory/adw/runs.py), including its
    refusals — a detached-HEAD run and a run whose branch was deleted since."""
    if live:
        return "still running"
    if accepted:
        return "completed and approved — nothing to resume"
    if ref is None:
        return "ran on a detached HEAD — no branch to resume onto"
    if branches is not None and ref not in branches:
        return f"the branch it worked on ('{ref}') is gone"
    return None


async def _branch_names(project: Path) -> set[str] | None:
    """Local branch names, or None when git cannot answer — an unverifiable
    ref is trusted rather than reported as gone."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(project),
            "for-each-ref",
            "--format=%(refname:short)",
            "refs/heads",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return None
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return None
    return {line for line in stdout.decode(errors="replace").splitlines() if line}


def _run_to_schema(
    project: Path,
    record: dict[str, object],
    *,
    run_dir: Path | None = None,
    branches: set[str] | None = None,
) -> schemas.FactoryRun | None:
    """None for a record without a usable run_id — nothing to act on."""
    run_id = record.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        return None
    state = record.get("state")
    state_str = state if isinstance(state, str) else "unknown"
    accepted = record.get("accepted") is True
    live = state_str == "running" and factory_runs.pid_alive(record.get("pid"))
    raw_branch = record.get("branch")
    branch = raw_branch if isinstance(raw_branch, str) else None
    hint = _resume_hint(
        live=live,
        accepted=accepted,
        ref=factory_runs.resume_ref(record),
        branches=branches,
    )
    return schemas.FactoryRun(
        run_id=run_id,
        project_path=str(project),
        project_name=project.name,
        outcome=_outcome(state_str, live=live, accepted=accepted),
        state=state_str,
        request_text=str(record.get("request") or ""),
        branch=branch,
        reason=str(record["reason"]) if isinstance(record.get("reason"), str) else None,
        interview=record.get("interview") is True,
        accepted=accepted,
        started_at=str(record["started"]) if isinstance(record.get("started"), str) else None,
        ended_at=str(record["ended"]) if isinstance(record.get("ended"), str) else None,
        resumable=hint is None,
        resume_hint=hint,
        session_ids=factory_runs.read_session_ids(run_dir) if run_dir else [],
    )


def _runs_roots(projects_root: Path) -> list[Path]:
    """Every directory that can hold run dirs: the global factory runs root's
    children, plus any per-project configured runs_dir for projects_root's
    immediate children (factory.config.json can point anywhere)."""
    roots: dict[Path, None] = {}
    global_root = app_settings.factory_runs_root
    if global_root.is_dir():
        for entry in sorted(global_root.iterdir()):
            if entry.is_dir():
                roots[entry] = None
    if projects_root.is_dir():
        for project in sorted(projects_root.iterdir()):
            if project.is_dir():
                configured = factory_runs.runs_root_for(project)
                if configured.is_dir():
                    roots[configured] = None
    return list(roots)


async def list_factory_runs(db: AsyncSession) -> list[schemas.FactoryRun]:
    """Every run recorded under the factory's runs roots whose repo resolves
    under projects_root — terminal launches show up too, not just rows this
    backend wrote. Each run.json's own "repo" names the project, so a project
    nested deeper than projects_root's first level is still found."""
    root = await _projects_root(db)
    runs: list[schemas.FactoryRun] = []
    seen: set[tuple[str, str]] = set()
    branches: dict[Path, set[str] | None] = {}
    for runs_root in _runs_roots(root):
        for run_dir in runs_root.iterdir():
            if not run_dir.is_dir():
                continue
            record = factory_runs.read_run_record(run_dir)
            if record is None or not isinstance(record.get("repo"), str):
                continue
            project = resolve_within_roots(Path(str(record["repo"])), [root])
            if project is None or not project.is_dir():
                continue
            if project not in branches:  # one git call per project, not per run
                branches[project] = await _branch_names(project)
            run = _run_to_schema(project, record, run_dir=run_dir, branches=branches[project])
            if run is not None and (run.project_path, run.run_id) not in seen:
                seen.add((run.project_path, run.run_id))
                runs.append(run)
    runs.sort(key=lambda r: r.started_at or "", reverse=True)
    return runs


async def find_run_for_session(db: AsyncSession, session_id: str) -> schemas.FactoryRun | None:
    """The run this coding session belongs to, or None. Two exact links, no
    guessing: the runner's own session is `factory-<run_id>` (telemetry.py
    builds it, coding/service.py reads it), and each stage's session id is
    recorded in the run's telemetry."""
    for run in await list_factory_runs(db):
        if session_id == f"{FACTORY_SESSION_PREFIX}{run.run_id}" or session_id in run.session_ids:
            return run
    return None


async def resume_run(
    db: AsyncSession,
    body: schemas.FactoryRunResumeRequest,
    resume_spawner: Callable[..., int],
) -> schemas.FactoryRunResumeRead:
    root = await _projects_root(db)
    resolved = resolve_within_roots(Path(body.project_path), [root])
    if resolved is None or not resolved.is_dir():
        raise ProjectPathOutsideRootError(
            f"project_path must be a directory under {root}, got: {body.project_path}"
        )

    run_dir = factory_runs.run_dir_for(resolved, body.run_id)
    record = factory_runs.read_run_record(run_dir)
    if record is None:
        raise RunNotFoundError(f"no run '{body.run_id}' recorded for {resolved.name}")
    # Checked here too, synchronously: the resume is detached, so a refusal it
    # discovers for itself would only ever reach a log file nobody is reading.
    run = _run_to_schema(resolved, record, branches=await _branch_names(resolved))
    if run is None:
        raise RunNotFoundError(f"run '{body.run_id}' has an unreadable record")
    if not run.resumable:
        raise RunNotResumableError(f"run '{body.run_id}' cannot be resumed: {run.resume_hint}")

    log_path = app_settings.masterwork_home / "launches" / f"run-{body.run_id}.log"
    try:
        pid = resume_spawner(
            project_path=resolved,
            run_id=body.run_id,
            log_path=log_path,
        )
    except OSError as exc:
        raise LaunchFailedError(f"could not resume the factory run: {exc}") from exc
    return schemas.FactoryRunResumeRead(run_id=body.run_id, resumed=True, pid=pid)


# --- interview state -----------------------------------------------------


def _interview_read(launch: SessionLaunch) -> schemas.InterviewRead:
    """Derives the interview state from the run's own files — nothing about
    this is stored on the launch row beyond the run id."""
    if launch.mode != MODE_INTERVIEW or not launch.run_id:
        return schemas.InterviewRead(
            launch_id=launch.id,
            run_id=launch.run_id,
            state=schemas.InterviewState.NOT_INTERVIEW,
            run_state=None,
        )

    run_dir = factory_runs.run_dir_for(Path(launch.project_path), launch.run_id)
    run_state = factory_runs.read_run_state(run_dir)
    questions = factory_runs.read_questions(run_dir)

    if run_state is None:
        state = schemas.InterviewState.STARTING
    elif factory_runs.answers_exist(run_dir):
        state = schemas.InterviewState.ANSWERED
    elif questions and run_state == "waiting_input":
        state = schemas.InterviewState.WAITING
    elif run_state in ("finished", "stopped"):
        state = schemas.InterviewState.FINISHED
    else:
        state = schemas.InterviewState.RUNNING

    return schemas.InterviewRead(
        launch_id=launch.id,
        run_id=launch.run_id,
        state=state,
        run_state=run_state,
        questions=(
            [schemas.InterviewQuestion(**q) for q in questions]
            if state == schemas.InterviewState.WAITING
            else []
        ),
    )


async def list_launches(
    db: AsyncSession, limit: int = launcher_repo.DEFAULT_LIST_LIMIT
) -> list[schemas.SessionLaunchListItem]:
    rows = await launcher_repo.list_launches(db, limit)
    return [
        schemas.SessionLaunchListItem(
            id=row.id,
            project_path=row.project_path,
            request_text=row.request_text,
            mode=schemas.LaunchMode(row.mode),
            launched_at=row.launched_at,
            pid=row.pid,
            run_id=row.run_id,
            launched=True,
            interview=(
                _interview_read(row) if row.mode == MODE_INTERVIEW and row.run_id else None
            ),
        )
        for row in rows
    ]


async def read_interview(db: AsyncSession, launch_id: int) -> schemas.InterviewRead:
    launch = await launcher_repo.get_launch(db, launch_id)
    if launch is None:
        raise LaunchNotFoundError(f"no launch with id {launch_id}")
    return _interview_read(launch)


async def submit_answers(
    db: AsyncSession,
    launch_id: int,
    body: schemas.InterviewAnswersRequest,
    resume_spawner: Callable[..., int],
) -> schemas.InterviewResumeRead:
    launch = await launcher_repo.get_launch(db, launch_id)
    if launch is None:
        raise LaunchNotFoundError(f"no launch with id {launch_id}")

    interview = _interview_read(launch)
    if interview.state != schemas.InterviewState.WAITING:
        raise InterviewNotWaitingError(
            f"launch {launch_id} is not waiting for answers (state: {interview.state.value})"
        )

    questions_by_id = {q.id: q.question for q in interview.questions}
    submitted_ids = [a.id for a in body.answers]
    no_duplicates = len(submitted_ids) == len(set(submitted_ids))
    exact_match = set(submitted_ids) == set(questions_by_id)
    if not (no_duplicates and exact_match):
        raise InterviewAnswerMismatchError(
            "answers must cover exactly the recorded questions, one each"
        )
    pairs: list[dict[str, str]] = []
    for answer in body.answers:
        text = answer.answer.strip()
        if not text:
            raise InterviewAnswerMismatchError(f'answer for "{answer.id}" must not be blank')
        pairs.append({"id": answer.id, "question": questions_by_id[answer.id], "answer": text})

    assert launch.run_id is not None  # guaranteed by state == WAITING
    run_dir = factory_runs.run_dir_for(Path(launch.project_path), launch.run_id)
    # Written before the spawn: a resume that started before its answers were
    # on disk would refuse itself.
    factory_runs.write_answers(run_dir, pairs)

    try:
        pid = resume_spawner(
            project_path=Path(launch.project_path),
            run_id=launch.run_id,
            log_path=_log_path(launch.id),
        )
    except OSError as exc:
        await db.rollback()
        raise LaunchFailedError(f"could not resume the factory run: {exc}") from exc

    await launcher_repo.set_pid(db, launch, pid)
    await db.commit()
    return schemas.InterviewResumeRead(
        launch_id=launch.id, run_id=launch.run_id, resumed=True, pid=pid
    )
