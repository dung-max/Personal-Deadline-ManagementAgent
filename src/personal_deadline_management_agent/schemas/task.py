"""Task schemas for API request and response contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..models import Task, TaskPriority, TaskStatus
from ..utils.datetime_utils import require_aware_utc

T = TypeVar("T")


class TaskCreateRequest(BaseModel):
    task_name: str = Field(alias="taskName")
    description: str | None = Field(default=None)
    deadline: datetime
    priority: TaskPriority

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("deadline")
    @classmethod
    def _deadline_must_be_aware(cls, value: datetime) -> datetime:
        """Reject naive datetimes and normalize aware ones to UTC."""
        return require_aware_utc(value)


class TaskUpdateRequest(BaseModel):
    task_name: str | None = Field(default=None, alias="taskName")
    description: str | None = Field(default=None)
    deadline: datetime | None = Field(default=None)
    priority: TaskPriority | None = Field(default=None)
    status: TaskStatus | None = Field(default=None)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("deadline")
    @classmethod
    def _deadline_must_be_aware(cls, value: datetime | None) -> datetime | None:
        """Reject naive datetimes and normalize aware ones to UTC."""
        if value is None:
            return None
        return require_aware_utc(value)


class TaskResponseData(BaseModel):
    task_id: UUID = Field(alias="taskId")
    task_name: str = Field(alias="taskName")
    description: str | None = Field(default=None)
    deadline: datetime
    priority: str
    status: str
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")

    model_config = ConfigDict(
        populate_by_name=True,
        serialize_by_alias=True,
        from_attributes=True,
    )

    @classmethod
    def from_domain(cls, task: Task) -> TaskResponseData:
        return cls(
            taskId=task.id,
            taskName=task.task_name,
            description=task.description,
            deadline=task.deadline,
            priority=task.priority,
            status=task.status,
            createdAt=task.created_at,
            updatedAt=task.updated_at,
        )


class ErrorDetail(BaseModel):
    code: str
    message: str


class SuccessResponse(BaseModel, Generic[T]):
    success: bool = True
    message: str
    data: T

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)


class ErrorResponse(BaseModel):
    success: bool = False
    message: str
    error: ErrorDetail

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)
