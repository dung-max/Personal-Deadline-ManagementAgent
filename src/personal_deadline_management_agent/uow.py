"""Unit of Work.

One UoW per atomic use case. The Module (use-case orchestrator) is the only
caller of commit(). close() does not commit, so an uncommitted session is
rolled back as a fail-safe.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from .repositories.reminder_repository import ReminderRepository
from .repositories.task_repository import TaskRepository


class UnitOfWork:
    def __init__(self, session: Session) -> None:
        self._session = session
        self.tasks = TaskRepository(session)
        self.reminders = ReminderRepository(session)

    def commit(self) -> None:
        self._session.commit()

    def rollback(self) -> None:
        self._session.rollback()

    def close(self) -> None:
        self._session.close()
