"""Reminder service.

Business/application logic layer for Reminder operations.
Depends on ReminderRepository and TaskRepository (for deadline validation);
does not access the database Session directly.
Does not own transaction lifecycle (commit/rollback remain with UnitOfWork).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from ..exceptions.reminder import InvalidReminderError, ReminderNotFoundError
from ..exceptions.task import TaskNotFoundError
from ..models import Reminder, ReminderStatus
from ..repositories.reminder_repository import ReminderRepository
from ..repositories.task_repository import TaskRepository

_ASSIGNABLE_STATUSES = {
    ReminderStatus.PENDING.value,
    ReminderStatus.CANCELLED.value,
}


class ReminderService:
    def __init__(
        self,
        reminder_repository: ReminderRepository,
        task_repository: TaskRepository,
    ) -> None:
        self._reminder_repository = reminder_repository
        self._task_repository = task_repository

    def create_reminder(
        self,
        task_id: UUID,
        remind_at: datetime,
    ) -> Reminder:
        task = self._task_repository.get_by_id(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)

        if remind_at > task.deadline:
            raise InvalidReminderError(
                f"remind_at ({remind_at}) must be before or at the task deadline "
                f"({task.deadline})"
            )

        reminder = Reminder(
            task_id=task_id,
            remind_at=remind_at,
            status=ReminderStatus.PENDING.value,
        )
        return self._reminder_repository.create(reminder)

    def get_reminder(self, reminder_id: UUID) -> Reminder:
        reminder = self._reminder_repository.get_by_id(reminder_id)
        if reminder is None:
            raise ReminderNotFoundError(reminder_id)
        return reminder

    def list_reminders_by_task(self, task_id: UUID) -> list[Reminder]:
        task = self._task_repository.get_by_id(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        return self._reminder_repository.list_by_task_id(task_id)

    def update_reminder(
        self,
        reminder_id: UUID,
        remind_at: datetime | None = None,
        status: ReminderStatus | str | None = None,
    ) -> Reminder:
        reminder = self.get_reminder(reminder_id)

        if reminder.status == ReminderStatus.SENT.value:
            raise InvalidReminderError(
                "A SENT reminder cannot be updated"
            )

        if remind_at is not None:
            task = self._task_repository.get_by_id(reminder.task_id)
            if task is None:
                raise TaskNotFoundError(reminder.task_id)
            if remind_at > task.deadline:
                raise InvalidReminderError(
                    f"remind_at ({remind_at}) must be before or at the task deadline "
                    f"({task.deadline})"
                )

        status_value: str | None = None
        if status is not None:
            status_value = (
                status.value if isinstance(status, ReminderStatus) else status
            )
            if status_value not in _ASSIGNABLE_STATUSES:
                raise InvalidReminderError(
                    f"Status cannot be set to {status_value}"
                )

        # All validation passed; mutate only now so a rejected update leaves
        # the ORM object untouched.
        if remind_at is not None:
            reminder.remind_at = remind_at
        if status_value is not None:
            reminder.status = status_value

        return self._reminder_repository.update(reminder)

    def delete_reminder(self, reminder_id: UUID) -> None:
        reminder = self._reminder_repository.get_by_id(reminder_id)
        if reminder is None:
            raise ReminderNotFoundError(reminder_id)
        self._reminder_repository.delete(reminder_id)
