"""Chat business logic: sessions, and the synchronous message exchange that
shells out to the claude runner, parses proposal/project blocks, and persists.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.assets import service as asset_service
from app.api.v1.chat import schemas, serializers
from app.core.exceptions import AssetNotFoundError, ProjectNotFoundError, SessionNotFoundError
from app.db.models.chat import DEFAULT_SESSION_TITLE, ChatSession
from app.db.models.project import Project
from app.providers.base import Provider, ScannedAsset
from app.repositories import chat as chat_repo
from app.repositories import projects as project_repo
from app.repositories import proposals as proposal_repo
from app.services.claude_runner import (
    APP_SYSTEM_PROMPT,
    ASSET_CHAT_INSTRUCTIONS,
    PROJECT_BLOCK_INSTRUCTIONS,
    ClaudeRunner,
    ClaudeRunnerError,
)
from app.services.proposal_parser import (
    ParsedProjectUpdate,
    ParsedProposal,
    extract_reply_blocks,
)
from app.services.redact import redact

_TITLE_MAX_LEN = 60
_GLOBAL_SENTINEL = "none"


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


def _derive_title(content: str) -> str:
    collapsed = " ".join(content.split())
    return collapsed[:_TITLE_MAX_LEN] if collapsed else DEFAULT_SESSION_TITLE


def _parse_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        return None


def _truncate(text: str, limit: int) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[:limit] + "…"


async def _get_session_or_404(db: AsyncSession, session_id: str) -> ChatSession:
    parsed = _parse_uuid(session_id)
    session = await chat_repo.get_session(db, parsed) if parsed is not None else None
    if session is None:
        raise SessionNotFoundError(f"unknown session: {session_id}")
    return session


async def _resolve_project(db: AsyncSession, project_id: str) -> uuid.UUID:
    """Validate a project id string, returning its uuid or raising 404."""
    parsed = _parse_uuid(project_id)
    project = await project_repo.get_project(db, parsed) if parsed is not None else None
    if project is None:
        raise ProjectNotFoundError(f"unknown project: {project_id}")
    return project.id


async def list_sessions(
    db: AsyncSession, project_id: str | None = None, asset_id: str | None = None
) -> list[schemas.ChatSession]:
    if asset_id is not None:
        sessions = await chat_repo.list_sessions(db, asset_id=asset_id)
    elif project_id is None:
        sessions = await chat_repo.list_sessions(db)
    elif project_id == _GLOBAL_SENTINEL:
        sessions = await chat_repo.list_sessions(db, scoped=True, project_id=None)
    else:
        resolved = await _resolve_project(db, project_id)
        sessions = await chat_repo.list_sessions(db, scoped=True, project_id=resolved)
    return [serializers.session_to_schema(s) for s in sessions]


async def create_session(
    db: AsyncSession,
    providers: list[Provider],
    body: schemas.ChatSessionCreateRequest,
) -> schemas.ChatSession:
    project_id = await _resolve_project(db, body.project_id) if body.project_id else None
    if body.asset_id:
        asset_service.find_asset(providers, body.asset_id)  # 404s on an unknown asset
    session = await chat_repo.create_session(db, body.title, project_id, body.asset_id)
    await db.commit()
    return serializers.session_to_schema(session)


async def update_session(
    db: AsyncSession, session_id: str, body: schemas.ChatSessionUpdateRequest
) -> schemas.ChatSession:
    session = await _get_session_or_404(db, session_id)
    session.title = body.title
    # Set updated_at explicitly: it bumps ordering and avoids the async lazy
    # refresh the server-side onupdate would otherwise force on serialization.
    session.updated_at = _utcnow()
    await db.commit()
    return serializers.session_to_schema(session)


async def delete_session(db: AsyncSession, session_id: str) -> None:
    session = await _get_session_or_404(db, session_id)
    await chat_repo.delete_session(db, session)
    await db.commit()


async def list_messages(db: AsyncSession, session_id: str) -> list[schemas.ChatMessage]:
    session = await _get_session_or_404(db, session_id)
    messages = await chat_repo.list_messages(db, session.id)
    return [serializers.message_to_schema(m, m.proposal) for m in messages]


def _asset_id_for_path(providers: list[Provider], path: str) -> str | None:
    target = Path(path)
    for provider in providers:
        asset_id = provider.asset_id_for_path(target)
        if asset_id is not None:
            return asset_id
    return None


def _changes_to_json(providers: list[Provider], parsed: ParsedProposal) -> list[dict[str, Any]]:
    return [
        {
            "path": change.path,
            "action": change.action,
            "new_content": change.new_content,
            "description": change.description,
            "asset_id": _asset_id_for_path(providers, change.path),
        }
        for change in parsed.changes
    ]


def _project_update_to_json(session: ChatSession, parsed: ParsedProjectUpdate) -> dict[str, Any]:
    return {
        "project_id": str(session.project_id),
        "name": parsed.name,
        "goal": parsed.goal,
        "flow_mermaid": parsed.flow_mermaid,
        "asset_ids": parsed.asset_ids,
        "description": parsed.description,
    }


def _missing_content_error(parsed: ParsedProposal) -> str | None:
    """A proposal whose update/create changes lack content can never apply."""
    missing = [
        c.path for c in parsed.changes if c.action in ("update", "create") and c.new_content is None
    ]
    if not missing:
        return None
    return (
        f"the assistant omitted new_content for: {', '.join(missing)} — "
        "ask it to re-send the proposal with the complete file content"
    )


def _proposal_summary(proposal: ParsedProposal | None, project: ParsedProjectUpdate | None) -> str:
    if proposal is not None:
        return proposal.summary
    if project is not None:
        return project.description
    return ""


def _linked_asset_lines(providers: list[Provider], asset_ids: list[str]) -> list[str]:
    index = {asset.id: asset for provider in providers for asset in provider.scan()}
    lines: list[str] = []
    for asset_id in asset_ids:
        asset = index.get(asset_id)
        if asset is None:  # skip unknown ids gracefully
            continue
        # redact(): titles/descriptions come straight from the asset file.
        lines.append(redact(f"  - {asset_id} — {asset.title}: {asset.description}"))
    return lines


def _project_context(project: Project, providers: list[Provider]) -> str:
    asset_lines = _linked_asset_lines(providers, list(project.asset_ids))
    assets_text = "\n".join(asset_lines) if asset_lines else "  (none linked yet)"
    # Enumerate valid ids: proposals referencing anything else fail validation
    # (e.g. plugin skills, which are visible under ~/.claude but not indexed).
    installed = redact(", ".join(sorted(a.id for p in providers for a in p.scan())))
    return (
        "Current project:\n"
        f"- name: {project.name}\n"
        f"- goal: {project.goal or '(empty)'}\n"
        "- linked assets:\n"
        f"{assets_text}\n"
        "- current flow diagram (mermaid):\n"
        f"{project.flow_mermaid or '(none yet)'}\n"
        "- installed asset ids (a `project` block's asset_ids may ONLY use these; "
        "claude-plugin:* assets are linkable but read-only — never propose file "
        "changes to them):\n"
        f"  {installed}"
    )


_ASSET_CONTENT_MAX_CHARS = 8000


def _asset_context(asset: ScannedAsset) -> str:
    content = asset.content
    if len(content) > _ASSET_CONTENT_MAX_CHARS:
        content = content[:_ASSET_CONTENT_MAX_CHARS] + "\n…(truncated — Read the file for the rest)"
    editable = "read-only (plugin-provided)" if asset.read_only else "editable"
    # redact(): title/description/content come straight from the asset file.
    return redact(
        "Current asset:\n"
        f"- id: {asset.id}\n"
        f"- kind: {asset.kind}\n"
        f"- title: {asset.title}\n"
        f"- description: {asset.description}\n"
        f"- path: {asset.path}\n"
        f"- {editable}\n"
        "- current content:\n"
        f"<<<ASSET\n{content}\nASSET>>>"
    )


def _asset_state_line(asset: ScannedAsset) -> str:
    return f"[current asset: id={asset.id}; path={asset.path}]"


def _project_state_line(project: Project) -> str:
    linked = ", ".join(project.asset_ids) if project.asset_ids else "none"
    return (
        f"[current project state: name={project.name!r}; "
        f"goal={_truncate(project.goal, 200)!r}; linked assets=[{linked}]; "
        f"flow diagram present={'yes' if project.flow_mermaid else 'no'}]"
    )


def _error_content(exc: ClaudeRunnerError) -> str:
    return f"The assistant could not complete this request.\n\n> {exc}\n\nPlease try again."


async def create_message(
    db: AsyncSession,
    providers: list[Provider],
    runner: ClaudeRunner,
    session_id: str,
    body: schemas.ChatMessageCreateRequest,
) -> schemas.ChatExchange:
    session = await _get_session_or_404(db, session_id)
    is_first = session.claude_session_id is None

    project = (
        await project_repo.get_project(db, session.project_id)
        if session.project_id is not None
        else None
    )

    # Persist the user message first so it survives even if the CLI call fails.
    user_message = await chat_repo.add_message(db, session.id, "user", body.content)
    if session.title == DEFAULT_SESSION_TITLE:
        session.title = _derive_title(body.content)
    session.updated_at = _utcnow()
    await db.commit()

    # Project- or asset-scoped: extend the first system prompt, and prepend a
    # fresh state line to every user prompt (--resume reuses the original
    # system prompt).
    system_prompt = APP_SYSTEM_PROMPT if is_first else None
    prompt = body.content
    if project is not None:
        if is_first:
            system_prompt = (
                f"{APP_SYSTEM_PROMPT}\n\n{PROJECT_BLOCK_INSTRUCTIONS}\n\n"
                f"{_project_context(project, providers)}"
            )
        prompt = f"{_project_state_line(project)}\n\n{body.content}"
    elif session.asset_id is not None:
        # A deleted/renamed asset must not break its chat history — fall back to
        # an unscoped exchange rather than 404ing the message.
        try:
            asset = asset_service.find_asset(providers, session.asset_id)
        except AssetNotFoundError:
            asset = None
        if asset is not None:
            if is_first:
                system_prompt = (
                    f"{APP_SYSTEM_PROMPT}\n\n{ASSET_CHAT_INSTRUCTIONS}\n\n{_asset_context(asset)}"
                )
            prompt = f"{_asset_state_line(asset)}\n\n{body.content}"

    try:
        result = await runner.run(
            prompt,
            resume_session_id=None if is_first else session.claude_session_id,
            system_prompt=system_prompt,
        )
    except ClaudeRunnerError as exc:
        error_message = await chat_repo.add_message(db, session.id, "error", _error_content(exc))
        session.updated_at = _utcnow()
        await db.commit()
        return schemas.ChatExchange(
            user_message=serializers.message_to_schema(user_message, None),
            assistant_message=serializers.message_to_schema(error_message, None),
        )

    session.claude_session_id = result.session_id
    visible_text, parsed_proposal, parsed_project = extract_reply_blocks(
        result.reply, include_project=session.project_id is not None
    )
    assistant_message = await chat_repo.add_message(db, session.id, "assistant", visible_text)

    proposal = None
    if parsed_proposal is not None or parsed_project is not None:
        invalid = _missing_content_error(parsed_proposal) if parsed_proposal is not None else None
        proposal = await proposal_repo.create_proposal(
            db,
            message_id=assistant_message.id,
            summary=_proposal_summary(parsed_proposal, parsed_project),
            changes=_changes_to_json(providers, parsed_proposal)
            if parsed_proposal is not None
            else [],
            project_update=_project_update_to_json(session, parsed_project)
            if parsed_project is not None
            else None,
            status="failed" if invalid else "pending",
            error=invalid,
        )
    session.updated_at = _utcnow()
    await db.commit()

    return schemas.ChatExchange(
        user_message=serializers.message_to_schema(user_message, None),
        assistant_message=serializers.message_to_schema(assistant_message, proposal),
    )
