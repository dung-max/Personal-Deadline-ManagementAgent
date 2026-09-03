"""Health endpoint.

Liveness-only for Phase 1. A readiness check (DB reachability) is deferred.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}
