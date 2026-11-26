"""MeetPilot API (modular monolith).

MeetPilot never joins meetings. This service only ever receives audio that the user's own
desktop app captured locally after explicit user action.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.ai.router import router as copilot_router
from app.api.v1 import health
from app.auth.router import router as auth_router
from app.auth.security import TokenService
from app.brain.embeddings import Embedder, FastEmbedder
from app.brain.router import router as brain_router
from app.brain.service import index_missing
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.base import Base
from app.db.session import make_engine, make_sessionmaker
from app.meetings.router import router as meetings_router
from app.providers.llm.base import LLMProvider
from app.providers.llm.factory import make_llm_provider
from app.providers.stt.base import StreamingSTTProvider
from app.providers.stt.factory import make_stt_provider
from app.realtime.gateway import ActiveStreams
from app.realtime.gateway import router as realtime_router
from app.usage.router import router as usage_router

log = logging.getLogger("meetpilot.http")


def create_app(
    settings: Settings | None = None,
    stt_provider: StreamingSTTProvider | None = None,
    llm_provider: LLMProvider | None = None,
    embedder: Embedder | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    engine = make_engine(settings.effective_database_url())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Local SQLite (development/test) is created on startup; PostgreSQL uses Alembic.
        if engine.url.get_backend_name() == "sqlite":
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        backfill: asyncio.Task[int] | None = None
        if app.state.embedder is not None:
            # Index memories saved before semantic search (or with another model).
            backfill = asyncio.create_task(
                index_missing(app.state.sessionmaker, app.state.embedder)
            )
        yield
        if backfill is not None and not backfill.done():
            backfill.cancel()
        await engine.dispose()

    if settings.jwt_secret is None and settings.env == "development":
        log.warning("MEETPILOT_JWT_SECRET not set: using a random key (tokens reset on restart)")

    app = FastAPI(
        title="MeetPilot API",
        version=settings.version,
        lifespan=lifespan,
        docs_url="/api/docs" if settings.env != "production" else None,
        openapi_url="/api/openapi.json" if settings.env != "production" else None,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = make_sessionmaker(engine)
    app.state.tokens = TokenService(settings)
    app.state.active_streams = ActiveStreams()
    app.state.embedder = embedder or (
        FastEmbedder(settings.embedding_model)
        if settings.brain_semantic_search and settings.env != "test"
        else None
    )
    app.state.stt_provider = stt_provider or make_stt_provider(settings)
    app.state.llm_provider = llm_provider or make_llm_provider(settings)
    # Suggested answers may use a stronger model (anthropic_answer_model).
    app.state.answer_llm_provider = (
        app.state.llm_provider
        if llm_provider is not None
        or settings.llm_provider != "anthropic"
        or not settings.anthropic_answer_model
        else make_llm_provider(settings, answers=True)
    )
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
        )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        start = time.perf_counter()
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        # Path and status only: never bodies, query strings or headers.
        log.info(
            "request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": round((time.perf_counter() - start) * 1000, 1),
            },
        )
        return response

    for r in (
        health.router,
        auth_router,
        meetings_router,
        usage_router,
        realtime_router,
        copilot_router,
        brain_router,
    ):
        app.include_router(r, prefix="/api/v1")
    return app


app = create_app()
