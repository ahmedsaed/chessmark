"""Chessmark API entrypoint."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from chessmark.api.routes import health
from chessmark.core.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup and shutdown hooks. Datastore pools get wired in here."""
    yield


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Chessmark API",
        version="0.1.0",
        description="LLM agents playing chess.",
        lifespan=lifespan,
        docs_url="/docs" if settings.debug else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)

    return app


app = create_app()
