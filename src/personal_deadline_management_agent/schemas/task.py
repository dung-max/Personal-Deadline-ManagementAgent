"""Task schemas for API request and response contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..models import Task, TaskPriority, TaskStatus
from ..utils.datetime_utils import require_aware_utc

T = TypeVar("T")

# Sentinel: mirrors services/task_service._UNSET / models _UNSET.
# Allows PATCH to distinguish "omitted" from "explicit null" when the
# field is nullable (description, duration_minutes).
_UNSET: object = object()


class TaskCreateRequest(BaseModel):
    task_name: str = Field(alias="taskName")
    description: str | None = Field(default=None)
    deadline: datetime
    priority: TaskPriority
    duration_minutes: int | None = Field(default=None, alias="durationMinutes")

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("deadline")
    @classmethod
    def _deadline_must_be_aware(cls, value: datetime) -> datetime:
        """Reject naive datetimes and normalize aware ones to UTC."""
        return require_aware_utc(value)

    @field_validator("duration_minutes")
    @classmethod
    def _duration_must_be_positive(cls, value: int | None) -> int | None:
        """Require positive duration when provided."""
        if value is not None and value <= 0:
            raise ValueError("duration_minutes must be a positive integer")
        return value


class TaskUpdateRequest(BaseModel):
    task_name: str | None = Field(default=None, alias="taskName")
    description: object = Field(default=_UNSET)
    deadline: datetime | None = Field(default=None)
    priority: TaskPriority | None = Field(default=None)
    status: TaskStatus | None = Field(default=None)
    duration_minutes: object = Field(default=_UNSET, alias="durationMinutes")

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("deadline")
    @classmethod
    def _deadline_must_be_aware(cls, value: datetime | None) -> datetime | None:
        """Reject naive datetimes and normalize aware ones to UTC."""
        if value is None:
            return None
        return require_aware_utc(value)

    @field_validator("description")
    @classmethod
    def _description_must_be_str_or_none(cls, value: object) -> object:
        """Allow _UNSET (omitted), None (explicit clear), or str."""
        if value is _UNSET or value is None:
            return value
        if isinstance(value, str):
            return value
        raise ValueError("description must be a string or null")

    @field_validator("duration_minutes")
    @classmethod
    def _update_duration_must_be_positive_or_none(cls, value: object) -> object:
        """Allow _UNSET (omitted), None (explicit clear), positive int; reject 0/negative.

        The field is typed as ``object`` so that ``_UNSET`` / ``None`` / int
        are all distinguishable. Actual OMIT vs null vs int disambiguation
        happens via ``payload.model_fields_set`` in the handler (matching the
        ``description`` PATCH pattern). This validator only gates the value.
        """
        if value is _UNSET or value is None:
            return value
        if isinstance(value, int) and value > 0:
            return value
        raise ValueError("duration_minutes must be a positive integer or null")


class TaskResponseData(BaseModel):
    task_id: UUID = Field(alias="taskId")
    task_name: str = Field(alias="taskName")
    description: str | None = Field(default=None)
    deadline: datetime
    duration_minutes: int | None = Field(default=None, alias="durationMinutes")
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
            durationMinutes=task.duration_minutes,
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
