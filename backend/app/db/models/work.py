"""Azure DevOps work-item mirror: registered sources, the items and pull
requests pulled from them, and the coding sessions requested for one.

Read-only inbound. Nothing in this app writes back to DevOps (see
app/providers/azuredevops.py and app/services/work_outbound.py), and
`work_sources.secret_ref` only NAMES the environment variable holding the
PAT — the PAT itself is never stored here.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.types import JSONColumn, UTCDateTime

DEFAULT_PROVIDER = "azuredevops"
DEFAULT_SECRET_REF = "AZURE_DEVOPS_PAT"

KIND_SPAWNED = "spawned"
KIND_LINKED = "linked"


class WorkSource(Base):
    """A registered DevOps org/project/team to pull work items from."""

    __tablename__ = "work_sources"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(
        String(50), default=DEFAULT_PROVIDER, server_default=text(f"'{DEFAULT_PROVIDER}'")
    )
    org_url: Mapped[str] = mapped_column(String(500))
    project: Mapped[str] = mapped_column(String(200))
    team: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Overrides DEFAULT_WIQL in work_sync when set.
    query_wiql: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Names the env var holding the PAT; read at call time, never persisted.
    secret_ref: Mapped[str] = mapped_column(
        String(200), default=DEFAULT_SECRET_REF, server_default=text(f"'{DEFAULT_SECRET_REF}'")
    )
    # The team's current iteration path (DevOps $timeframe=current), refreshed on sync.
    current_iteration: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # The PAT owner's DevOps display name, refreshed on sync; what "@Me" matches on.
    owner_display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, server_default=func.now(), onupdate=func.now()
    )


class WorkItem(Base):
    """One mirrored DevOps work item, upserted on (source_id, external_id)."""

    __tablename__ = "work_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("work_sources.id", ondelete="CASCADE")
    )
    external_id: Mapped[int] = mapped_column(Integer)
    # DevOps System.Parent — external id of the parent item (story of a task).
    parent_external_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # True when the row was fetched only as a missing parent of a WIQL hit,
    # not returned by the source's own query (a story someone else owns).
    pulled_as_parent: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    external_url: Mapped[str] = mapped_column(String(1000))
    item_type: Mapped[str] = mapped_column(String(100))
    title: Mapped[str] = mapped_column(Text)
    # HTML -> markdown converted on sync; see app/services/work_sync.py.
    description_md: Mapped[str] = mapped_column(Text, default="", server_default="")
    acceptance_md: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(100))
    iteration: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # DevOps System.AssignedTo display name; null when unassigned.
    assigned_to: Mapped[str | None] = mapped_column(String(300), nullable=True)
    priority: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(JSONColumn, nullable=True)
    # The whole DevOps payload, unchanged — untrusted external data: stored,
    # rendered as markdown, never executed or eval'd.
    raw: Mapped[dict[str, Any]] = mapped_column(JSONColumn)
    external_changed_at: Mapped[datetime] = mapped_column(UTCDateTime)
    synced_at: Mapped[datetime] = mapped_column(UTCDateTime)

    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_work_items_source_external"),
        # The list endpoint filters on exactly this pair.
        Index("ix_work_items_source_state", "source_id", "state"),
    )


class WorkPullRequest(Base):
    """One mirrored DevOps pull request, upserted on (source_id, external_id).

    `repository_remote_url` is the join key `delegatePullRequest` resolves to a
    local checkout via app/services/repo_paths.py — normalized before comparison,
    never the repository name (the local folder is often named differently).
    """

    __tablename__ = "work_pull_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("work_sources.id", ondelete="CASCADE")
    )
    external_id: Mapped[int] = mapped_column(Integer)
    repository_id: Mapped[str] = mapped_column(String(200))
    repository_name: Mapped[str] = mapped_column(String(300))
    repository_remote_url: Mapped[str] = mapped_column(String(1000))
    title: Mapped[str] = mapped_column(Text)
    # DevOps sends plain text here, not HTML — stored verbatim, never rendered as HTML/markdown.
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    source_branch: Mapped[str] = mapped_column(String(500))
    target_branch: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(50))
    is_draft: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_by: Mapped[str | None] = mapped_column(String(300), nullable=True)
    external_url: Mapped[str] = mapped_column(String(1000))
    # The untouched DevOps payload — untrusted external data: stored, placed
    # into the delegate prompt as data, never executed or eval'd.
    raw: Mapped[dict[str, Any]] = mapped_column(JSONColumn)
    external_changed_at: Mapped[datetime] = mapped_column(UTCDateTime)
    synced_at: Mapped[datetime] = mapped_column(UTCDateTime)

    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_work_prs_source_external"),
    )


class WorkPrThread(Base):
    """One PR review thread, upserted on (pull_request_id, external_id).

    Fetched on demand (per PR), not on bulk PR sync — see app/services/work_prs.py.
    """

    __tablename__ = "work_pr_threads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pull_request_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("work_pull_requests.id", ondelete="CASCADE")
    )
    external_id: Mapped[int] = mapped_column(Integer)
    # Absent on some system threads.
    status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_resolved: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # Null for a PR-level thread (no threadContext).
    file_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    right_file_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # [{id, author, content, comment_type, published_at}, ...] in DevOps order —
    # untrusted external data, rendered as plain text only.
    comments: Mapped[list[dict[str, Any]]] = mapped_column(JSONColumn)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONColumn)
    synced_at: Mapped[datetime] = mapped_column(UTCDateTime)

    __table_args__ = (
        UniqueConstraint("pull_request_id", "external_id", name="uq_work_pr_threads_pr_external"),
    )


class WorkRepoPath(Base):
    """Remote-to-folder memory for `delegatePullRequest`.

    Keyed on the NORMALIZED remote URL, never the repository name — a local
    checkout is frequently named something else. See app/services/repo_paths.py.
    """

    __tablename__ = "work_repo_paths"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    remote_url: Mapped[str] = mapped_column(String(1000))
    local_path: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())

    __table_args__ = (UniqueConstraint("remote_url", name="uq_work_repo_paths_remote"),)


class WorkItemSession(Base):
    """A coding session requested for (or linked to) one work item.

    `session_id` is nullable: this backend has no reusable session-launch path
    (see plan.md), so `start_work_item` writes this row with `session_id=None`
    — the prompt was handed out, nothing is bound to it yet. The FK still
    cascades once a session id is filled in.

    `pushed_state`/`last_comment_at` are outbound bookkeeping columns nothing
    writes in v1 — outbound stays a stub, see app/services/work_outbound.py.
    """

    __tablename__ = "work_item_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    work_item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("work_items.id", ondelete="CASCADE")
    )
    session_id: Mapped[str | None] = mapped_column(
        String(200), ForeignKey("coding_sessions.id", ondelete="CASCADE"), nullable=True
    )
    kind: Mapped[str] = mapped_column(String(20))
    pushed_state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_comment_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())
