"""FastAPI composition root.

Only responsible for: create app, load config, dependency wiring,
router registration, exception handlers, and lifespan. No business logic.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import Settings, load_config
from .db import create_engine_from_url, create_session_factory
from .handlers import health
from .models import Task  # noqa: F401 — register Task with Base.metadata


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine_from_url(settings.database_url)
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        yield
        engine.dispose()

    app = FastAPI(
        title="Personal Deadline Management Agent",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(health.router)
    return app


app = create_app()
