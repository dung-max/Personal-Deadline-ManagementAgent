"""Reminder repository.

Persistence layer for Reminder entities using SQLAlchemy Session.
Transaction ownership remains with UnitOfWork; this repository does NOT commit or rollback.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Reminder, Task


class ReminderRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, reminder: Reminder) -> Reminder:
        self._session.add(reminder)
        self._session.flush()
        self._session.refresh(reminder)
        return reminder

    def get_by_id(self, reminder_id: UUID) -> Reminder | None:
        return self._session.get(Reminder, reminder_id)

    def list_by_task_id(self, task_id: UUID) -> list[Reminder]:
        stmt = (
            select(Reminder)
            .where(Reminder.task_id == task_id)
            .order_by(Reminder.remind_at.asc())
        )
        return list(self._session.scalars(stmt).all())

    def find_by_task_name(self, phrase: str) -> list[Reminder]:
        """Reminders whose parent task name matches ``phrase``.

        Used by the resource resolver for natural-language reminder references.
        Case-insensitive substring match on the associated task's name
        (order: remind_at ASC).
        """
        pattern = f"%{phrase}%"
        stmt = (
            select(Reminder)
            .join(Task, Reminder.task_id == Task.id)
            .where(Task.task_name.ilike(pattern))
            .order_by(Reminder.remind_at.asc())
        )
        return list(self._session.scalars(stmt).all())

    def update(self, reminder: Reminder) -> Reminder:
        merged = self._session.merge(reminder)
        self._session.flush()
        self._session.refresh(merged)
        return merged

    def delete(self, reminder_id: UUID) -> bool:
        reminder = self.get_by_id(reminder_id)
        if reminder is None:
            return False
        self._session.delete(reminder)
        self._session.flush()
        return True
