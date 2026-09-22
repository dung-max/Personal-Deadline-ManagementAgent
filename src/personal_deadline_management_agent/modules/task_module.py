"""Task module.

Application use-case orchestration layer and transaction boundary for Task operations.
Owns commit/rollback for write use cases using UnitOfWork.
Delegates business logic to TaskService.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from ..models import Task, TaskPriority, TaskStatus
from ..services.task_service import TaskService, _UNSET
from ..uow import UnitOfWork


class TaskModule:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow
        self._service = TaskService(uow.tasks)

    def create_task(
        self,
        task_name: str,
        description: str | None,
        deadline: datetime,
        priority: TaskPriority,
        duration_minutes: int | None = None,
    ) -> Task:
        try:
            result = self._service.create_task(
                task_name=task_name,
                description=description,
                deadline=deadline,
                priority=priority,
                duration_minutes=duration_minutes,
            )
            self._uow.commit()
            return result
        except Exception:
            self._uow.rollback()
            raise

    def get_task(self, task_id: UUID) -> Task:
        return self._service.get_task(task_id)

    def list_tasks(self) -> list[Task]:
        return self._service.list_tasks()

    def update_task(
        self,
        task_id: UUID,
        task_name: str | None = None,
        description: object = _UNSET,
        deadline: datetime | None = None,
        priority: TaskPriority | None = None,
        status: TaskStatus | None = None,
        duration_minutes: object = _UNSET,
    ) -> Task:
        try:
            result = self._service.update_task(
                task_id=task_id,
                task_name=task_name,
                description=description,
                deadline=deadline,
                priority=priority,
                status=status,
                duration_minutes=duration_minutes,
            )
            self._uow.commit()
            return result
        except Exception:
            self._uow.rollback()
            raise

    def delete_task(self, task_id: UUID) -> None:
        try:
            self._service.delete_task(task_id)
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise

    def find_tasks_by_deadline_range(
        self, start: datetime, end: datetime
    ) -> list[Task]:
        """Read-only query for tasks within a deadline range.

        No commit/rollback needed for read-only operations.
        """
        return self._uow.tasks.find_by_deadline_range(start, end)
