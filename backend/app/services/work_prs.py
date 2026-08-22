"""Sync Azure DevOps pull requests + review threads into the local mirror,
and assemble the delegate prompt from them.

PR/comment text is untrusted external data: stored verbatim in `raw` and
`comments`, placed into the assembled prompt as data only, and handed to the
launcher as a single request-text argument — never a shell string, never
`eval`'d, never interpolated into a query. `assemble_pr_prompt` states the
no-push / no-DevOps rules in the prompt itself, since phase 1 adds no DevOps
write capability for a hostile comment to reach anyway.

PRs are synced in bulk for a source (one `list_active_prs` call); threads are
fetched on demand for a single PR only — pulling every open PR's threads on
sync would be one HTTP call per PR.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.work import WorkPrThread, WorkPullRequest, WorkSource
from app.providers.azuredevops import AzureDevOpsClient
from app.repositories import work as work_repo
from app.services.work_sync import SyncCounts

_RESOLVED_STATUSES = {"fixed", "closed", "wontFix", "byDesign"}
_HEADS_PREFIX = "refs/heads/"


def _strip_ref_prefix(ref: Any) -> str:
    text = str(ref) if isinstance(ref, str) else ""
    return text[len(_HEADS_PREFIX) :] if text.startswith(_HEADS_PREFIX) else text


def _repo_remote_url(repository: dict[str, Any]) -> str:
    remote = repository.get("remoteUrl")
    if isinstance(remote, str) and remote:
        return remote
    web = repository.get("webUrl")
    return web if isinstance(web, str) else ""


def _created_by(payload: dict[str, Any]) -> str | None:
    created_by = payload.get("createdBy")
    if not isinstance(created_by, dict):
        return None
    name = created_by.get("displayName")
    return name if isinstance(name, str) and name else None


def _pr_url(source: WorkSource, repository_name: str, external_id: int) -> str:
    return (
        f"{source.org_url.rstrip('/')}/{source.project}/_git/{repository_name}"
        f"/pullrequest/{external_id}"
    )


def _parse_creation_date(value: Any, *, fallback: datetime) -> datetime:
    if not isinstance(value, str) or not value:
        return fallback
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return fallback


async def _upsert_pr_payload(
    db: AsyncSession, source: WorkSource, payload: dict[str, Any], *, now: datetime
) -> bool | None:
    """Upsert one PR payload; None when the payload has no usable id."""
    external_id = payload.get("pullRequestId")
    if not isinstance(external_id, int):
        return None
    repository = payload.get("repository")
    repository = repository if isinstance(repository, dict) else {}
    repository_name = str(repository.get("name") or "")
    return await work_repo.upsert_pr(
        db,
        source_id=source.id,
        external_id=external_id,
        repository_id=str(repository.get("id") or ""),
        repository_name=repository_name,
        repository_remote_url=_repo_remote_url(repository),
        title=str(payload.get("title") or ""),
        description=str(payload.get("description") or ""),
        source_branch=_strip_ref_prefix(payload.get("sourceRefName")),
        target_branch=_strip_ref_prefix(payload.get("targetRefName")),
        status=str(payload.get("status") or ""),
        is_draft=bool(payload.get("isDraft")),
        created_by=_created_by(payload),
        external_url=_pr_url(source, repository_name, external_id),
        raw=payload,
        external_changed_at=_parse_creation_date(payload.get("creationDate"), fallback=now),
        synced_at=now,
    )


async def sync_pull_requests(
    db: AsyncSession, source: WorkSource, client: AzureDevOpsClient
) -> SyncCounts:
    """One `list_active_prs` call for the whole source; upsert each returned
    payload on (source_id, external_id). A payload without a usable
    pullRequestId is skipped, exactly as a work item without an id is."""
    now = datetime.now(tz=UTC)
    payloads = await client.list_active_prs()

    inserted = 0
    updated = 0
    for payload in payloads:
        inserted_row = await _upsert_pr_payload(db, source, payload, now=now)
        if inserted_row is None:
            continue
        if inserted_row:
            inserted += 1
        else:
            updated += 1

    await db.flush()
    return SyncCounts(fetched=len(payloads), inserted=inserted, updated=updated)


def _thread_context_line(context: dict[str, Any] | None) -> int | None:
    if not isinstance(context, dict):
        return None
    for key in ("rightFileStart", "rightFileEnd"):
        position = context.get(key)
        if isinstance(position, dict):
            line = position.get("line")
            if isinstance(line, int):
                return line
    return None


def _thread_comments(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_comments = payload.get("comments")
    if not isinstance(raw_comments, list):
        return []
    comments: list[dict[str, Any]] = []
    for comment in raw_comments:
        if not isinstance(comment, dict):
            continue
        author = comment.get("author")
        display_name = author.get("displayName") if isinstance(author, dict) else None
        comments.append(
            {
                "id": comment.get("id"),
                "author": display_name if isinstance(display_name, str) else None,
                "content": str(comment.get("content") or ""),
                "comment_type": comment.get("commentType"),
                "published_at": comment.get("publishedDate"),
            }
        )
    return comments


async def sync_pr_threads(
    db: AsyncSession, pr: WorkPullRequest, client: AzureDevOpsClient
) -> list[WorkPrThread]:
    """On-demand, for one PR: fetch its threads and upsert each on
    (pull_request_id, external_id). Re-running it updates rather than
    duplicating, and reflects a changed upstream comment."""
    now = datetime.now(tz=UTC)
    payloads = await client.list_pr_threads(pr.repository_id, pr.external_id)

    for payload in payloads:
        external_id = payload.get("id")
        if not isinstance(external_id, int):
            continue
        status = payload.get("status")
        status_str = status if isinstance(status, str) and status else None
        context = payload.get("threadContext")
        context = context if isinstance(context, dict) else None
        await work_repo.upsert_pr_thread(
            db,
            pull_request_id=pr.id,
            external_id=external_id,
            status=status_str,
            is_resolved=status_str in _RESOLVED_STATUSES,
            file_path=context.get("filePath") if context else None,
            right_file_line=_thread_context_line(context),
            comments=_thread_comments(payload),
            raw=payload,
            synced_at=now,
        )

    await db.flush()
    return await work_repo.list_pr_threads(db, pr.id)


def _thread_heading(thread: WorkPrThread) -> str:
    if thread.file_path is None:
        return "### On the pull request"
    if thread.right_file_line is not None:
        return f"### {thread.file_path}:{thread.right_file_line}"
    return f"### {thread.file_path}"


def assemble_pr_prompt(pr: WorkPullRequest, threads: list[WorkPrThread]) -> str:
    """The delegate prompt: PR identity, its unresolved threads only (resolved
    threads and threads with no comments are skipped), and the no-push /
    no-DevOps rules stated in plain words."""
    unresolved = [t for t in threads if not t.is_resolved and t.comments]

    lines = [
        f"Pull request #{pr.external_id}: {pr.title}",
        pr.external_url,
        f"Repository: {pr.repository_name}",
        f"Branch: {pr.source_branch} -> {pr.target_branch}",
        "",
        f"Check out `{pr.source_branch}` before doing anything else.",
        "",
        "## Unresolved review comments",
    ]
    if not unresolved:
        lines += ["", "(No unresolved review comments.)"]
    for thread in unresolved:
        lines += ["", _thread_heading(thread)]
        for comment in thread.comments:
            author = comment.get("author") or "Someone"
            content = comment.get("content") or ""
            lines.append(f"- {author}: {content}")

    lines += [
        "",
        "## Rules",
        "- Address every comment above.",
        "- Run this repository's own checks before you finish.",
        "- Do NOT push. Do NOT create or update a branch on the remote.",
        "- Do NOT touch Azure DevOps: no comment replies, no thread resolution, no PR update.",
    ]
    return "\n".join(lines)
