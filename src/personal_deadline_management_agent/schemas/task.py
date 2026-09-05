"""Task schemas for API request and response contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..models import Task, TaskPriority, TaskStatus

T = TypeVar("T")


class TaskCreateRequest(BaseModel):
    task_name: str = Field(alias="taskName")
    description: str | None = Field(default=None)
    deadline: datetime
    priority: TaskPriority

    model_config = ConfigDict(populate_by_name=True)


class TaskUpdateRequest(BaseModel):
    task_name: str | None = Field(default=None, alias="taskName")
    description: str | None = Field(default=None)
    deadline: datetime | None = Field(default=None)
    priority: TaskPriority | None = Field(default=None)
    status: TaskStatus | None = Field(default=None)

    model_config = ConfigDict(populate_by_name=True)


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
