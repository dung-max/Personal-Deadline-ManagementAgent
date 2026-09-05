"""Task-related application exceptions."""

from __future__ import annotations

from uuid import UUID


class TaskNotFoundError(Exception):
    """Raised when a requested task does not exist."""

    def __init__(self, task_id: UUID | str) -> None:
        self.task_id = task_id
        super().__init__(f"Task not found: {task_id}")
