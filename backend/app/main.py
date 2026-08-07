"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.assets.routes import router as assets_router
from app.api.v1.chat.routes import router as chat_router
from app.api.v1.instructions.routes import router as instructions_router
from app.api.v1.projects.routes import router as projects_router
from app.api.v1.proposals.routes import router as proposals_router
from app.api.v1.simulations.routes import router as simulations_router
from app.config import settings
from app.core.exceptions import DomainError

API_PREFIX = "/api/v1"

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(_: FastAPI) -> AsyncIterator[None]:
    # A restart orphans in-flight background simulations; mark them failed so
    # they don't poll forever. Best-effort: never block startup on it.
    try:
        from app.api.v1.simulations.service import RESTART_ERROR
        from app.db.session import AsyncSessionLocal
        from app.repositories.simulations import fail_all_running

        async with AsyncSessionLocal() as session:
            await fail_all_running(session, error=RESTART_ERROR)
            await session.commit()
    except Exception:
        logger.warning("could not sweep orphaned simulations at startup", exc_info=True)
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.version,
        openapi_url="/openapi.json",
        lifespan=_lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(DomainError)
    async def _domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.get("/health", tags=["health"], operation_id="health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(assets_router, prefix=API_PREFIX)
    app.include_router(chat_router, prefix=API_PREFIX)
    app.include_router(instructions_router, prefix=API_PREFIX)
    app.include_router(projects_router, prefix=API_PREFIX)
    app.include_router(proposals_router, prefix=API_PREFIX)
    app.include_router(simulations_router, prefix=API_PREFIX)
    return app


app = create_app()
