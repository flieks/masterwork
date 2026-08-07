"""Chat endpoints: sessions and the synchronous message exchange."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_claude_runner, get_db, get_providers
from app.api.v1.chat import schemas, service
from app.providers.base import Provider
from app.services.claude_runner import ClaudeRunner

router = APIRouter(tags=["chat"])


@router.get(
    "/chat/sessions",
    response_model=list[schemas.ChatSession],
    operation_id="listChatSessions",
)
async def list_chat_sessions(
    project_id: str | None = Query(
        None,
        description='Omit for all sessions; "none" for global sessions; a uuid for a project.',
    ),
    asset_id: str | None = Query(
        None,
        description="Filter to one asset's sessions, e.g. 'claude:agent:architect'. "
        "Takes precedence over project_id.",
    ),
    db: AsyncSession = Depends(get_db),
) -> list[schemas.ChatSession]:
    return await service.list_sessions(db, project_id, asset_id)


@router.post(
    "/chat/sessions",
    response_model=schemas.ChatSession,
    status_code=status.HTTP_201_CREATED,
    operation_id="createChatSession",
)
async def create_chat_session(
    body: schemas.ChatSessionCreateRequest,
    db: AsyncSession = Depends(get_db),
    providers: list[Provider] = Depends(get_providers),
) -> schemas.ChatSession:
    return await service.create_session(db, providers, body)


@router.patch(
    "/chat/sessions/{session_id}",
    response_model=schemas.ChatSession,
    operation_id="updateChatSession",
)
async def update_chat_session(
    session_id: str,
    body: schemas.ChatSessionUpdateRequest,
    db: AsyncSession = Depends(get_db),
) -> schemas.ChatSession:
    return await service.update_session(db, session_id, body)


@router.delete(
    "/chat/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteChatSession",
)
async def delete_chat_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> None:
    await service.delete_session(db, session_id)


@router.get(
    "/chat/sessions/{session_id}/messages",
    response_model=list[schemas.ChatMessage],
    operation_id="listChatMessages",
)
async def list_chat_messages(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> list[schemas.ChatMessage]:
    return await service.list_messages(db, session_id)


@router.post(
    "/chat/sessions/{session_id}/messages",
    response_model=schemas.ChatExchange,
    operation_id="createChatMessage",
)
async def create_chat_message(
    session_id: str,
    body: schemas.ChatMessageCreateRequest,
    db: AsyncSession = Depends(get_db),
    providers: list[Provider] = Depends(get_providers),
    runner: ClaudeRunner = Depends(get_claude_runner),
) -> schemas.ChatExchange:
    return await service.create_message(db, providers, runner, session_id, body)
