"""FastAPI dependency wiring (request-scoped)."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Request
from sqlalchemy.orm import Session, sessionmaker

from .modules.reminder_module import ReminderModule
from .modules.task_module import TaskModule
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


def get_task_module(
    uow: UnitOfWork = Depends(get_uow),
) -> TaskModule:
    return TaskModule(uow)


def get_reminder_module(
    uow: UnitOfWork = Depends(get_uow),
) -> ReminderModule:
    return ReminderModule(uow)
