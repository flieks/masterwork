"""Session launcher API schemas."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class LaunchMode(StrEnum):
    AUTONOMOUS = "autonomous"
    INTERVIEW = "interview"


class LauncherProject(BaseModel):
    name: str
    path: str = Field(..., description="Absolute path under projects_root.")
    is_git_repo: bool


class LauncherProjectCreateRequest(BaseModel):
    """Named apart from the unrelated `ProjectCreateRequest` (features/projects) —
    this creates a plain folder under projects_root, not a masterwork Project."""

    name: str = Field(..., description="Folder name — no path separators or traversal.")


class LaunchRequest(BaseModel):
    project_path: str = Field(..., description="Absolute path; must resolve under projects_root.")
    request_text: str = Field(
        ..., min_length=1, description="What to build — handed to the factory as-is."
    )
    mode: LaunchMode = Field(
        LaunchMode.AUTONOMOUS,
        description=(
            "'autonomous' never asks; 'interview' pauses after planning to ask "
            "about weak assumptions before building."
        ),
    )


class SessionLaunchRead(BaseModel):
    id: int
    project_path: str
    request_text: str
    mode: LaunchMode
    launched_at: datetime
    pid: int | None
    run_id: str | None = Field(None, description="Set for interview launches only.")
    launched: bool = Field(..., description="True once the subprocess was spawned.")


class InterviewState(StrEnum):
    NOT_INTERVIEW = "not_interview"
    STARTING = "starting"
    RUNNING = "running"
    WAITING = "waiting"
    ANSWERED = "answered"
    FINISHED = "finished"


class InterviewQuestion(BaseModel):
    id: str
    question: str


class InterviewRead(BaseModel):
    launch_id: int
    run_id: str | None
    state: InterviewState
    run_state: str | None = Field(None, description="run.json's raw state, for debugging.")
    questions: list[InterviewQuestion] = Field(default_factory=list)


class InterviewAnswer(BaseModel):
    id: str
    answer: str = Field(..., min_length=1)


class InterviewAnswersRequest(BaseModel):
    answers: list[InterviewAnswer] = Field(..., min_length=1)


class InterviewResumeRead(BaseModel):
    launch_id: int
    run_id: str
    resumed: bool
    pid: int | None


class SessionLaunchListItem(SessionLaunchRead):
    interview: InterviewRead | None = Field(
        None, description="Only for interview-mode launches; null for autonomous ones."
    )


class RunOutcome(StrEnum):
    """What actually happened, which run.json's `state` alone does not say: a
    rejected run and an approved one both end up `state="finished"`."""

    RUNNING = "running"
    WAITING = "waiting"
    DONE = "done"
    FAILED = "failed"
    STOPPED = "stopped"


class FactoryRun(BaseModel):
    """One run.json under a project's runs root — the factory's ground truth,
    independent of whether the run was launched from this UI or a terminal."""

    run_id: str
    project_path: str = Field(..., description="The project the run worked on.")
    project_name: str
    outcome: RunOutcome = Field(..., description="Read this, not `state`.")
    state: str = Field(..., description="run.json's raw state, e.g. running/stopped/finished.")
    request_text: str
    branch: str | None
    reason: str | None = Field(None, description="Why the run ended, e.g. 'cost cap reached…'.")
    interview: bool
    accepted: bool
    started_at: str | None
    ended_at: str | None
    resumable: bool = Field(
        ..., description="True when a resume would be accepted: not live, not completed-accepted."
    )
    resume_hint: str | None = Field(
        None, description="Why a resume is not offered; null when `resumable` is true."
    )
    superseded_by: str | None = Field(
        None,
        description="Run id of a newer run of this same request, when one exists.",
    )
    session_ids: list[str] = Field(
        default_factory=list, description="Coding sessions this run's stages reported."
    )


class FactoryRunResumeRequest(BaseModel):
    project_path: str = Field(..., description="Absolute path; must resolve under projects_root.")
    run_id: str


class FactoryRunResumeRead(BaseModel):
    run_id: str
    resumed: bool
    pid: int | None


class DirectoryEntry(BaseModel):
    name: str
    path: str = Field(..., description="Absolute path.")


class DirectoryListing(BaseModel):
    path: str = Field(..., description="The resolved directory this listing is for.")
    parent: str | None = Field(
        None, description="Absolute path of the parent, null at the filesystem root."
    )
    entries: list[DirectoryEntry] = Field(
        default_factory=list, description="Non-hidden subdirectories, sorted by name."
    )
    home: str = Field(..., description="Absolute path of the home directory the backend runs as.")
