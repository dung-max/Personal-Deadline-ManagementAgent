"""Reminder module.

Application use-case orchestration layer and transaction boundary for Reminder
operations. Owns commit/rollback for write use cases using UnitOfWork.
Delegates business logic to ReminderService.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from ..models import Reminder, ReminderStatus
from ..services.reminder_service import ReminderService
from ..uow import UnitOfWork


class ReminderModule:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow
        self._service = ReminderService(uow.reminders, uow.tasks)

    def create_reminder(
        self,
        task_id: UUID,
        remind_at: datetime,
    ) -> Reminder:
        try:
            result = self._service.create_reminder(
                task_id=task_id,
                remind_at=remind_at,
            )
            self._uow.commit()
            return result
        except Exception:
            self._uow.rollback()
            raise

    def get_reminder(self, reminder_id: UUID) -> Reminder:
        return self._service.get_reminder(reminder_id)

    def list_reminders_by_task(self, task_id: UUID) -> list[Reminder]:
        return self._service.list_reminders_by_task(task_id)

    def update_reminder(
        self,
        reminder_id: UUID,
        remind_at: datetime | None = None,
        status: ReminderStatus | str | None = None,
    ) -> Reminder:
        try:
            result = self._service.update_reminder(
                reminder_id=reminder_id,
                remind_at=remind_at,
                status=status,
            )
            self._uow.commit()
            return result
        except Exception:
            self._uow.rollback()
            raise

    def delete_reminder(self, reminder_id: UUID) -> None:
        try:
            self._service.delete_reminder(reminder_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
