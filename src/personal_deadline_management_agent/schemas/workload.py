"""Workload analysis schemas.

Structured Pydantic models for the workload-analysis result.  These are
pure data contracts — no business logic, no database access.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..models import TaskPriority, TaskStatus


# ---------------------------------------------------------------------------
# Workload analysis schemas
# ---------------------------------------------------------------------------


class TaskSummary(BaseModel):
    """Lightweight representation of a task for workload analysis."""

    id: UUID
    task_name: str
    priority: TaskPriority
    status: TaskStatus
    deadline: datetime | None

    model_config = ConfigDict(populate_by_name=True)


class DeadlineCollision(BaseModel):
    """Two or more tasks sharing the same deadline datetime."""

    deadline: datetime
    tasks: list[TaskSummary]

    model_config = ConfigDict(populate_by_name=True)


class BusyDayWarning(BaseModel):
    """A calendar day containing a high number of active tasks."""

    date: date
    task_count: int = Field(ge=0)
    tasks: list[TaskSummary]

    model_config = ConfigDict(populate_by_name=True)


class WorkloadAnalysisResult(BaseModel):
    """Top-level result of workload analysis."""

    total_tasks: int = Field(ge=0, default=0)
    deadline_collisions: list[DeadlineCollision] = Field(default_factory=list)
    busy_days: list[BusyDayWarning] = Field(default_factory=list)
    recommended_order: list[TaskSummary] = Field(default_factory=list)
    explanation: str = ""

    model_config = ConfigDict(populate_by_name=True)
