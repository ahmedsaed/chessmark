"""Chessmark API entrypoint."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from chessmark.api.deps import close_redis
from chessmark.api.routes import events, games, health, models
from chessmark.core.config import get_settings
from chessmark.db.session import dispose_engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    # Pools are process-wide singletons, so they are closed once here rather than per request.
    await close_redis()
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Chessmark API",
        version="0.1.0",
        description=(
            "LLM agents playing chess. Reading is open to everyone — spectating and replays are "
            "the shareable surface. Creating a game spends money and is gated from Phase 9."
        ),
        lifespan=lifespan,
        docs_url="/docs" if settings.debug else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # The browser needs to read the SSE cursor header to resume a dropped stream.
        expose_headers=["Last-Event-ID"],
    )

    app.include_router(health.router)
    app.include_router(models.router)
    app.include_router(games.router)
    app.include_router(events.router)

    return app


app = create_app()
