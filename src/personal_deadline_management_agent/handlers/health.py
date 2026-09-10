"""Health endpoints.

``/health`` is liveness-only (used by the Docker healthcheck).
``/health/ready`` is a readiness probe that verifies database connectivity
with ``SELECT 1`` — 200 when the DB is reachable, 503 otherwise.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
def readiness_check(request: Request) -> JSONResponse:
    """Return 200 when the database is reachable, 503 otherwise.

    Uses the app's session factory directly — no Repository/UoW abstraction
    is needed for a simple connectivity probe.
    """
    session_factory = request.app.state.session_factory
    session = session_factory()
    try:
        session.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Readiness check failed: database unreachable")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "not ready"},
        )
    finally:
        session.close()
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"status": "ready"},
    )