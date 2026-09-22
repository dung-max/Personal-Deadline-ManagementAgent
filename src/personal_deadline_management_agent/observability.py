"""Request correlation and structured application logging utilities."""

from __future__ import annotations

import json
import logging
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

CORRELATION_HEADER = "X-Correlation-ID"
_CORRELATION_ID: ContextVar[str] = ContextVar("correlation_id", default="-")
_RECORD_FACTORY_INSTALLED = False
_UUID_TEXT = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def get_correlation_id() -> str:
    """Return the correlation ID associated with the current execution context."""
    return _CORRELATION_ID.get()


@contextmanager
def correlation_context(correlation_id: str):
    """Temporarily bind a correlation ID for synchronous application code."""
    token = _CORRELATION_ID.set(correlation_id)
    try:
        yield
    finally:
        _CORRELATION_ID.reset(token)


def _valid_correlation_id(value: str | None) -> str | None:
    if not value or len(value) != 36 or not _UUID_TEXT.fullmatch(value):
        return None
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None
    return value


def resolve_correlation_id(value: str | None) -> str:
    """Validate a caller ID or create a canonical UUID for the request."""
    return _valid_correlation_id(value) or str(uuid4())


class StructuredFormatter(logging.Formatter):
    """Allow production logs to be emitted as safe JSON records."""

    _SAFE_FIELDS = (
        "event_name",
        "correlation_id",
        "http_method",
        "http_path",
        "status_code",
        "duration_ms",
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in self._SAFE_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


class ReadableFormatter(logging.Formatter):
    """Readable local formatter retaining the same operational fields."""

    def format(self, record: logging.LogRecord) -> str:
        correlation_id = getattr(record, "correlation_id", "-")
        event_name = getattr(record, "event_name", "-")
        return (
            f"{super().format(record)} "
            f"event={event_name} correlation_id={correlation_id}"
        )


def configure_logging(environment: str) -> None:
    """Install context fields and an environment-appropriate formatter.

    The record factory adds only allowlisted operational fields; it never copies
    request payloads or arbitrary ``extra`` dictionaries into log output.
    """
    global _RECORD_FACTORY_INSTALLED
    if not _RECORD_FACTORY_INSTALLED:
        old_factory = logging.getLogRecordFactory()

        def record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
            record = old_factory(*args, **kwargs)
            if not hasattr(record, "correlation_id"):
                record.correlation_id = get_correlation_id()
            return record

        logging.setLogRecordFactory(record_factory)
        _RECORD_FACTORY_INSTALLED = True

    root = logging.getLogger()
    for handler in root.handlers:
        if getattr(handler, "_pdma_formatter_installed", False):
            continue
        if environment.lower() == "production":
            handler.setFormatter(StructuredFormatter())
        else:
            handler.setFormatter(ReadableFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        handler._pdma_formatter_installed = True  # type: ignore[attr-defined]


class CorrelationMiddleware:
    """ASGI middleware that establishes and returns a request correlation ID."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        supplied = headers.get(CORRELATION_HEADER.lower().encode())
        correlation_id = resolve_correlation_id(
            supplied.decode("ascii", errors="ignore") if supplied else None
        )
        scope.setdefault("state", {})["correlation_id"] = correlation_id
        token = _CORRELATION_ID.set(correlation_id)
        started = time.perf_counter()
        response_status: int | None = None

        async def send_wrapper(message: Message) -> None:
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = int(message["status"])
                response_headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != CORRELATION_HEADER.lower().encode()
                ]
                response_headers.append(
                    (CORRELATION_HEADER.lower().encode(), correlation_id.encode())
                )
                message = {**message, "headers": response_headers}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            raise
        else:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            logging.getLogger(__name__).info(
                "Request completed",
                extra={
                    "event_name": "request.completed",
                    "http_method": scope.get("method", ""),
                    "http_path": scope.get("path", ""),
                    "status_code": response_status,
                    "duration_ms": duration_ms,
                },
            )
        finally:
            _CORRELATION_ID.reset(token)
