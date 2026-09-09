"""Task-related application exceptions."""

from __future__ import annotations

from uuid import UUID


class InvalidTaskError(Exception):
    """Raised when a task operation violates a business rule."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class TaskNotFoundError(Exception):
    """Raised when a requested task does not exist."""

    def __init__(self, task_id: UUID | str) -> None:
        self.task_id = task_id
        super().__init__(f"Task not found: {task_id}")
