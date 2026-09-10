"""Reminder repository.

Persistence layer for Reminder entities using SQLAlchemy Session.
Transaction ownership remains with UnitOfWork; this repository does NOT commit or rollback.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..models import Reminder, ReminderStatus, Task


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

    def find_due(self, limit: int, now: datetime) -> list[Reminder]:
        """Return due reminders for the scheduler tick.

        A reminder is due when ``status == PENDING`` and ``remind_at <= now``.
        Results are ordered by ``remind_at ASC, id ASC`` and limited by
        ``limit``.  This method does not commit or rollback.
        """
        stmt = (
            select(Reminder)
            .where(
                Reminder.status == ReminderStatus.PENDING.value,
                Reminder.remind_at <= now,
            )
            .order_by(Reminder.remind_at.asc(), Reminder.id.asc())
            .limit(limit)
        )
        return list(self._session.scalars(stmt).all())

    def mark_sent(self, reminder_id: UUID, now: datetime) -> int:
        """Atomically claim a due reminder for the scheduler.

        Transitions ``PENDING -> SENT`` only when the reminder is still due
        (``remind_at <= now``).  The conditional WHERE clause makes the claim
        atomic: concurrent workers racing on the same reminder will see at most
        one row updated.  ``updated_at`` is set explicitly because bulk UPDATE
        bypasses the ORM ``onupdate`` lambda.

        ``SENT`` means the reminder was successfully processed/claimed by the
        scheduler — it does NOT guarantee external delivery.  ``updated_at`` is
        a generic modification timestamp, not an audit timestamp.

        Returns the number of rows updated (0 or 1).  Does not commit or
        rollback.
        """
        stmt = (
            update(Reminder)
            .where(
                Reminder.id == reminder_id,
                Reminder.status == ReminderStatus.PENDING.value,
                Reminder.remind_at <= now,
            )
            .values(
                status=ReminderStatus.SENT.value,
                updated_at=now,
            )
        )
        result = self._session.execute(stmt)
        return result.rowcount or 0
