"""FastAPI dependency wiring (request-scoped)."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Request
from sqlalchemy.orm import Session, sessionmaker

from .uow import UnitOfWork


def get_session_factory(request: Request) -> sessionmaker[Session]:
    return request.app.state.session_factory


def get_uow(
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
) -> Iterator[UnitOfWork]:
    uow = UnitOfWork(session_factory())
    try:
        yield uow
    finally:
        uow.close()
