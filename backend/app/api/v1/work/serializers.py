"""Map WorkSource/WorkItem ORM rows to the contract's Pydantic schemas.

Explicit, not `model_validate(row)` with `from_attributes=True`: the pk/fk
columns are `uuid.UUID`, and Pydantic v2 does not coerce UUID to `str` for a
`str`-typed field — it raises. `str(row.id)` here is the whole fix.
"""

from __future__ import annotations

from app.api.v1.work import schemas
from app.db.models.work import WorkItem, WorkPrThread, WorkPullRequest, WorkRepoPath, WorkSource


def work_source_to_schema(source: WorkSource) -> schemas.WorkSource:
    return schemas.WorkSource(
        id=str(source.id),
        provider=source.provider,
        org_url=source.org_url,
        project=source.project,
        team=source.team,
        query_wiql=source.query_wiql,
        secret_ref=source.secret_ref,
        current_iteration=source.current_iteration,
        owner_display_name=source.owner_display_name,
        last_sync_at=source.last_sync_at,
        created_at=source.created_at,
        updated_at=source.updated_at,
    )


def work_item_to_schema(item: WorkItem) -> schemas.WorkItem:
    return schemas.WorkItem(
        id=item.id,
        source_id=str(item.source_id),
        external_id=item.external_id,
        parent_external_id=item.parent_external_id,
        pulled_as_parent=item.pulled_as_parent,
        external_url=item.external_url,
        item_type=item.item_type,
        title=item.title,
        description_md=item.description_md,
        acceptance_md=item.acceptance_md,
        state=item.state,
        iteration=item.iteration,
        assigned_to=item.assigned_to,
        priority=item.priority,
        tags=list(item.tags) if item.tags is not None else None,
        external_changed_at=item.external_changed_at,
        synced_at=item.synced_at,
    )


def work_pr_to_schema(pr: WorkPullRequest) -> schemas.WorkPullRequest:
    return schemas.WorkPullRequest(
        id=pr.id,
        source_id=str(pr.source_id),
        external_id=pr.external_id,
        repository_id=pr.repository_id,
        repository_name=pr.repository_name,
        repository_remote_url=pr.repository_remote_url,
        title=pr.title,
        description=pr.description,
        source_branch=pr.source_branch,
        target_branch=pr.target_branch,
        status=pr.status,
        is_draft=pr.is_draft,
        created_by=pr.created_by,
        external_url=pr.external_url,
        external_changed_at=pr.external_changed_at,
        synced_at=pr.synced_at,
    )


def work_pr_thread_to_schema(thread: WorkPrThread) -> schemas.WorkPrThread:
    return schemas.WorkPrThread(
        id=thread.id,
        pull_request_id=thread.pull_request_id,
        external_id=thread.external_id,
        status=thread.status,
        is_resolved=thread.is_resolved,
        file_path=thread.file_path,
        right_file_line=thread.right_file_line,
        comments=[schemas.WorkPrThreadComment(**c) for c in thread.comments],
        synced_at=thread.synced_at,
    )


def work_repo_path_to_schema(repo_path: WorkRepoPath) -> schemas.WorkRepoPath:
    return schemas.WorkRepoPath(
        id=repo_path.id,
        remote_url=repo_path.remote_url,
        local_path=repo_path.local_path,
        created_at=repo_path.created_at,
    )
