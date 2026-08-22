"""Chessmark API entrypoint."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from chessmark.api.deps import close_redis
from chessmark.api.routes import admin, events, games, health, me, models, webhooks
from chessmark.core.config import get_settings
from chessmark.db.session import dispose_engine


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    # Pools are process-wide singletons, so they are closed once here rather than per request.
    await close_redis()
    await dispose_engine()


class InsecureConfigurationError(RuntimeError):
    """Production is missing a control that Phase 9 makes a hard gate (ADR-0011)."""


def create_app() -> FastAPI:
    settings = get_settings()

    # Refuse to start rather than start unprotected. Without this the app would come up and fail
    # closed — nobody could authenticate — which is safe but gives an operator no idea why.
    if settings.environment == "production":
        problems = settings.production_problems()
        if problems:
            raise InsecureConfigurationError(
                "Refusing to start in production:\n  - " + "\n  - ".join(problems)
            )

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
    app.include_router(me.router)
    app.include_router(admin.router)
    app.include_router(webhooks.router)

    return app


app = create_app()
