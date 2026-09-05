"""Task service.

Business/application logic layer for Task operations.
Depends on TaskRepository; does not access the database Session directly.
Does not own transaction lifecycle (commit/rollback remain with UnitOfWork).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from ..exceptions.task import TaskNotFoundError
from ..models import Task, TaskPriority, TaskStatus
from ..repositories.task_repository import TaskRepository


class TaskService:
    def __init__(self, task_repository: TaskRepository) -> None:
        self._task_repository = task_repository

    def create_task(
        self,
        task_name: str,
        description: str | None,
        deadline: datetime,
        priority: TaskPriority,
    ) -> Task:
        priority_value = (
            priority.value if isinstance(priority, TaskPriority) else priority
        )
        task = Task(
            task_name=task_name,
            description=description,
            deadline=deadline,
            priority=priority_value,
            status=TaskStatus.TODO.value,
        )
        return self._task_repository.create(task)

    def get_task(self, task_id: UUID) -> Task:
        task = self._task_repository.get_by_id(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        return task

    def list_tasks(self) -> list[Task]:
        return self._task_repository.list()

    def update_task(
        self,
        task_id: UUID,
        task_name: str | None = None,
        description: str | None = None,
        deadline: datetime | None = None,
        priority: TaskPriority | None = None,
        status: TaskStatus | None = None,
    ) -> Task:
        task = self.get_task(task_id)

        if task_name is not None:
            task.task_name = task_name
        if description is not None:
            task.description = description
        if deadline is not None:
            task.deadline = deadline
        if priority is not None:
            task.priority = (
                priority.value if isinstance(priority, TaskPriority) else priority
            )
        if status is not None:
            task.status = (
                status.value if isinstance(status, TaskStatus) else status
            )

        return self._task_repository.update(task)

    def delete_task(self, task_id: UUID) -> None:
        task = self._task_repository.get_by_id(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        self._task_repository.delete(task_id)
