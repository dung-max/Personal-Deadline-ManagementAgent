"""Reminder-related application exceptions."""

from __future__ import annotations

from uuid import UUID


class ReminderNotFoundError(Exception):
    """Raised when a requested reminder does not exist."""

    def __init__(self, reminder_id: UUID | str) -> None:
        self.reminder_id = reminder_id
        super().__init__(f"Reminder not found: {reminder_id}")


class InvalidReminderError(Exception):
    """Raised when a reminder operation violates a business rule."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
