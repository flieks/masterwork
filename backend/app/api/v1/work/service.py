"""Work-item business logic: source CRUD, sync orchestration, and the
session-start prompt assembly.

Session launch is deferred (see plan.md): this backend has no reusable
session-launch path, so `start_work_item` returns the assembled prompt with
`launched=False` and links a `work_item_sessions` row with `session_id=None`.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.launcher import schemas as launcher_schemas
from app.api.v1.launcher import service as launcher_service
from app.api.v1.settings.service import read_settings
from app.api.v1.work import schemas, serializers
from app.core.exceptions import (
    InvalidRepoPathError,
    InvalidWorkSourceError,
    PullRequestNotFoundError,
    WorkItemNotFoundError,
    WorkSourceNotFoundError,
    WorkSyncError,
)
from app.db.models.work import KIND_SPAWNED, WorkItem, WorkPullRequest, WorkSource
from app.providers.azuredevops import AzureDevOpsClient, AzureDevOpsError
from app.repositories import work as work_repo
from app.services import repo_paths, work_prs, work_sync

_ORG_URL_RE = re.compile(r"^https://dev\.azure\.com/[A-Za-z0-9._~-]+/?$")


def _parse_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        return None


async def get_source_or_404(db: AsyncSession, source_id: str) -> WorkSource:
    parsed = _parse_uuid(source_id)
    source = await work_repo.get_source(db, parsed) if parsed is not None else None
    if source is None:
        raise WorkSourceNotFoundError(f"unknown work source: {source_id}")
    return source


async def get_item_or_404(db: AsyncSession, item_id: int) -> WorkItem:
    item = await work_repo.get_item(db, item_id)
    if item is None:
        raise WorkItemNotFoundError(f"unknown work item: {item_id}")
    return item


async def get_pr_or_404(db: AsyncSession, pr_id: int) -> WorkPullRequest:
    pr = await work_repo.get_pr(db, pr_id)
    if pr is None:
        raise PullRequestNotFoundError(f"unknown pull request: {pr_id}")
    return pr


async def list_sources(db: AsyncSession) -> list[schemas.WorkSource]:
    sources = await work_repo.list_sources(db)
    return [serializers.work_source_to_schema(s) for s in sources]


async def create_source(
    db: AsyncSession, body: schemas.WorkSourceCreateRequest
) -> schemas.WorkSource:
    if not _ORG_URL_RE.match(body.org_url):
        raise InvalidWorkSourceError(
            f"org_url must look like https://dev.azure.com/<org>, got: {body.org_url}"
        )
    source = await work_repo.create_source(
        db,
        org_url=body.org_url,
        project=body.project,
        team=body.team,
        query_wiql=body.query_wiql,
        secret_ref=body.secret_ref,
    )
    await db.commit()
    return serializers.work_source_to_schema(source)


async def list_items(
    db: AsyncSession, *, source_id: str | None, state: str | None
) -> list[schemas.WorkItem]:
    parsed_source_id: uuid.UUID | None = None
    if source_id is not None:
        parsed_source_id = _parse_uuid(source_id)
        if parsed_source_id is None:
            raise WorkSourceNotFoundError(f"unknown work source: {source_id}")
    items = await work_repo.list_items(db, source_id=parsed_source_id, state=state)
    return [serializers.work_item_to_schema(i) for i in items]


async def sync_source(
    db: AsyncSession,
    source_id: str,
    client_factory: Callable[[WorkSource], AzureDevOpsClient],
) -> schemas.WorkSyncResult:
    """`client_factory` builds the AzureDevOpsClient for one source — the
    route injects it via `get_devops_client_factory`, tests inject a fake."""
    source = await get_source_or_404(db, source_id)
    client = client_factory(source)
    try:
        counts = await work_sync.sync_source(db, source, client)
    except AzureDevOpsError as exc:
        raise WorkSyncError(
            f"sync failed for {source.org_url.rstrip('/')}/{source.project}: {exc}"
        ) from exc
    await db.commit()
    return schemas.WorkSyncResult(
        fetched=counts.fetched, inserted=counts.inserted, updated=counts.updated
    )


def _assemble_prompt(item: WorkItem) -> str:
    lines = [
        f"{item.item_type} #{item.external_id}: {item.title}",
        item.external_url,
        "",
        "## Story",
        item.description_md,
    ]
    if item.acceptance_md:
        lines += ["", "## Acceptance criteria", item.acceptance_md]
    return "\n".join(lines)


async def start_work_item(db: AsyncSession, item_id: int) -> schemas.WorkItemStartResponse:
    item = await get_item_or_404(db, item_id)
    prompt = _assemble_prompt(item)
    link = await work_repo.create_item_session(
        db, work_item_id=item.id, session_id=None, kind=KIND_SPAWNED
    )
    await db.commit()
    return schemas.WorkItemStartResponse(
        prompt=prompt, launched=False, session_id=None, link_id=link.id
    )


# --- pull requests -----------------------------------------------------


async def list_pull_requests(
    db: AsyncSession, *, source_id: str | None
) -> list[schemas.WorkPullRequest]:
    parsed_source_id: uuid.UUID | None = None
    if source_id is not None:
        parsed_source_id = _parse_uuid(source_id)
        if parsed_source_id is None:
            raise WorkSourceNotFoundError(f"unknown work source: {source_id}")
    prs = await work_repo.list_prs(db, source_id=parsed_source_id)
    return [serializers.work_pr_to_schema(pr) for pr in prs]


async def sync_pull_requests(
    db: AsyncSession,
    source_id: str,
    client_factory: Callable[[WorkSource], AzureDevOpsClient],
) -> schemas.WorkSyncResult:
    source = await get_source_or_404(db, source_id)
    client = client_factory(source)
    try:
        counts = await work_prs.sync_pull_requests(db, source, client)
    except AzureDevOpsError as exc:
        raise WorkSyncError(
            f"PR sync failed for {source.org_url.rstrip('/')}/{source.project}: {exc}"
        ) from exc
    await db.commit()
    return schemas.WorkSyncResult(
        fetched=counts.fetched, inserted=counts.inserted, updated=counts.updated
    )


async def list_pull_request_threads(
    db: AsyncSession,
    pr_id: int,
    client_factory: Callable[[WorkSource], AzureDevOpsClient],
) -> list[schemas.WorkPrThread]:
    """Fetches from DevOps and upserts before returning — fetch-and-persist on read."""
    pr = await get_pr_or_404(db, pr_id)
    source = await get_source_or_404(db, str(pr.source_id))
    client = client_factory(source)
    try:
        threads = await work_prs.sync_pr_threads(db, pr, client)
    except AzureDevOpsError as exc:
        raise WorkSyncError(f"could not fetch threads for PR {pr.external_id}: {exc}") from exc
    await db.commit()
    return [serializers.work_pr_thread_to_schema(t) for t in threads]


def _validate_repo_path(value: str) -> Path:
    """Echoes launcher_service._validate_browse_path's shape."""
    expanded = Path(value).expanduser()
    if not expanded.is_absolute():
        raise InvalidRepoPathError(f"local_path must be absolute, got: {value}")
    try:
        resolved = expanded.resolve()
    except OSError as exc:  # e.g. a symlink loop
        raise InvalidRepoPathError(f"local_path could not be resolved: {value}") from exc
    if not resolved.is_dir():
        raise InvalidRepoPathError(f"local_path does not exist or is not a directory: {resolved}")
    return resolved


async def save_repo_path(
    db: AsyncSession, body: schemas.WorkRepoPathCreateRequest
) -> schemas.WorkRepoPath:
    resolved = _validate_repo_path(body.local_path)
    normalized = repo_paths.normalize_remote_url(body.remote_url)
    row = await work_repo.upsert_repo_path(db, remote_url=normalized, local_path=str(resolved))
    await db.commit()
    return serializers.work_repo_path_to_schema(row)


async def delegate_pull_request(
    db: AsyncSession,
    pr_id: int,
    client_factory: Callable[[WorkSource], AzureDevOpsClient],
    spawner: Callable[..., int],
) -> schemas.PullRequestDelegateResponse:
    """Refreshes threads, resolves a local checkout (stored mapping -> scan of
    projects_root -> unresolved), and on a hit launches the fix through the
    single existing spawn path (launcher_service.launch)."""
    pr = await get_pr_or_404(db, pr_id)
    source = await get_source_or_404(db, str(pr.source_id))
    client = client_factory(source)
    try:
        threads = await work_prs.sync_pr_threads(db, pr, client)
    except AzureDevOpsError as exc:
        raise WorkSyncError(f"could not refresh threads for PR {pr.external_id}: {exc}") from exc
    await db.commit()
    unresolved_count = sum(1 for t in threads if not t.is_resolved and t.comments)

    projects_root = Path((await read_settings(db)).projects_root)
    resolution = await repo_paths.resolve_local_path(db, pr.repository_remote_url, projects_root)
    if resolution.matched_from == "scan":
        # Persisted by resolve_local_path via flush; committed here, before the
        # launch, so a failed launch never loses the discovery.
        await db.commit()

    if resolution.local_path is None:
        return schemas.PullRequestDelegateResponse(
            resolved=False,
            remote_url=pr.repository_remote_url,
            local_path=None,
            reason=resolution.reason,
            launch_id=None,
            run_id=None,
            prompt=None,
            unresolved_thread_count=unresolved_count,
        )

    prompt = work_prs.assemble_pr_prompt(pr, threads)
    launched = await launcher_service.launch(
        db,
        launcher_schemas.LaunchRequest(
            project_path=str(resolution.local_path), request_text=prompt
        ),
        spawner,
    )
    return schemas.PullRequestDelegateResponse(
        resolved=True,
        remote_url=pr.repository_remote_url,
        local_path=str(resolution.local_path),
        reason=None,
        launch_id=launched.id,
        run_id=launched.run_id,
        prompt=prompt,
        unresolved_thread_count=unresolved_count,
    )
