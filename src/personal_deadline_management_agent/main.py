"""FastAPI composition root.

Only responsible for: create app, load config, dependency wiring,
router registration, exception handlers, and lifespan. No business logic.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .config import Settings, load_config
from .db import create_engine_from_url, create_session_factory
from .exceptions.reminder import InvalidReminderError, ReminderNotFoundError
from .exceptions.task import InvalidTaskError, TaskNotFoundError
from .handlers import agent_handler, health, reminder_handler, task_handler
from .models import PendingConfirmation, Reminder, Task  # noqa: F401 — register models with Base.metadata
from .observability import CorrelationMiddleware, configure_logging, correlation_context

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_config()
    configure_logging(settings.environment)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine_from_url(settings.database_url)
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        try:
            yield
        finally:
            try:
                engine.dispose()
            except Exception:
                logger.warning("Failed to dispose engine", exc_info=True)

    app = FastAPI(
        title="Personal Deadline Management Agent",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.add_middleware(CorrelationMiddleware)

    @app.exception_handler(TaskNotFoundError)
    async def task_not_found_exception_handler(
        request: Request, exc: TaskNotFoundError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "success": False,
                "message": "Task not found",
                "error": {
                    "code": "TASK_NOT_FOUND",
                    "message": str(exc),
                },
            },
        )

    @app.exception_handler(ReminderNotFoundError)
    async def reminder_not_found_exception_handler(
        request: Request, exc: ReminderNotFoundError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={
                "success": False,
                "message": "Reminder not found",
                "error": {
                    "code": "REMINDER_NOT_FOUND",
                    "message": str(exc),
                },
            },
        )

    @app.exception_handler(InvalidReminderError)
    async def invalid_reminder_exception_handler(
        request: Request, exc: InvalidReminderError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "success": False,
                "message": str(exc),
                "error": {
                    "code": "INVALID_REMINDER",
                    "message": str(exc),
                },
            },
        )

    @app.exception_handler(InvalidTaskError)
    async def invalid_task_exception_handler(
        request: Request, exc: InvalidTaskError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "success": False,
                "message": str(exc),
                "error": {
                    "code": "INVALID_TASK",
                    "message": str(exc),
                },
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = exc.errors()
        error_messages = []
        for err in errors:
            loc = ".".join(str(item) for item in err.get("loc", []))
            msg = err.get("msg", "")
            error_messages.append(f"{loc}: {msg}" if loc else msg)
        detail_msg = "; ".join(error_messages) if error_messages else "Invalid request"
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "success": False,
                "message": "Validation error",
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": detail_msg,
                },
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        correlation_id = request.state.correlation_id
        with correlation_context(correlation_id):
            logger.exception(
                "Unhandled exception: %s %s",
                request.method,
                request.url.path,
            )
        response = JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "success": False,
                "message": "Internal server error",
                "error": {
                    "code": "INTERNAL_SERVER_ERROR",
                    "message": "Internal server error",
                },
            },
        )
        response.headers["X-Correlation-ID"] = request.state.correlation_id
        return response

    app.include_router(health.router)
    app.include_router(task_handler.router)
    app.include_router(reminder_handler.router)
    app.include_router(agent_handler.router)
    return app


try:
    app = create_app()
except Exception:
    logger.exception("Failed to create application")
    raise
