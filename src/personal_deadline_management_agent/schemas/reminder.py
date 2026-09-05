"""Reminder schemas for API request and response contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..models import Reminder, ReminderStatus

AssignableReminderStatus = Literal[
    ReminderStatus.PENDING,
    ReminderStatus.CANCELLED,
]


class ReminderCreateRequest(BaseModel):
    remind_at: datetime = Field(alias="remindAt")

    model_config = ConfigDict(populate_by_name=True)


class ReminderUpdateRequest(BaseModel):
    remind_at: datetime | None = Field(default=None, alias="remindAt")
    status: AssignableReminderStatus | None = Field(default=None)

    model_config = ConfigDict(populate_by_name=True)


class ReminderResponseData(BaseModel):
    reminder_id: UUID = Field(alias="reminderId")
    task_id: UUID = Field(alias="taskId")
    remind_at: datetime = Field(alias="remindAt")
    status: str
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")

    model_config = ConfigDict(
        populate_by_name=True,
        serialize_by_alias=True,
        from_attributes=True,
    )

    @classmethod
    def from_domain(cls, reminder: Reminder) -> ReminderResponseData:
        return cls(
            reminderId=reminder.id,
            taskId=reminder.task_id,
            remindAt=reminder.remind_at,
            status=reminder.status,
            createdAt=reminder.created_at,
            updatedAt=reminder.updated_at,
        )
