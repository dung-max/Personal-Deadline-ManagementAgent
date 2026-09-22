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
    task_name: str = Field(alias="taskName")
    priority: TaskPriority
    status: TaskStatus
    deadline: datetime | None
    duration_minutes: int | None = Field(default=None, alias="durationMinutes")

    model_config = ConfigDict(populate_by_name=True)


class DeadlineCollision(BaseModel):
    """Two or more tasks sharing the same deadline datetime."""

    deadline: datetime
    tasks: list[TaskSummary]

    model_config = ConfigDict(populate_by_name=True)


class BusyDayWarning(BaseModel):
    """A calendar day containing a high number of active tasks."""

    date: date
    task_count: int = Field(ge=0, alias="taskCount")
    tasks: list[TaskSummary]

    model_config = ConfigDict(populate_by_name=True)


class FeasibilityWindow(BaseModel):
    """Time window showing latest possible start time for a task given its deadline and duration."""

    task_id: UUID = Field(alias="taskId")
    task_name: str = Field(alias="taskName")
    deadline: datetime
    duration_minutes: int = Field(alias="durationMinutes")
    latest_start: datetime = Field(alias="latestStart")

    model_config = ConfigDict(populate_by_name=True)


class DailyPressure(BaseModel):
    """Daily workload pressure showing planned work vs available budget."""

    date: date
    planned_minutes: int = Field(alias="plannedMinutes")
    budget_minutes: int = Field(alias="budgetMinutes")
    utilization_pct: float = Field(alias="utilizationPct")
    tasks: list[TaskSummary]

    model_config = ConfigDict(populate_by_name=True)


class OverloadWarning(BaseModel):
    """Warning about days where planned work exceeds available budget."""

    date: date
    planned_minutes: int = Field(alias="plannedMinutes")
    budget_minutes: int = Field(alias="budgetMinutes")
    excess_minutes: int = Field(alias="excessMinutes")
    tasks: list[TaskSummary]

    model_config = ConfigDict(populate_by_name=True)


class SchedulingPressure(BaseModel):
    """Pair of tasks with overlapping feasibility windows indicating scheduling pressure."""

    task_a_id: UUID = Field(alias="taskAId")
    task_a_name: str = Field(alias="taskAName")
    task_b_id: UUID = Field(alias="taskBId")
    task_b_name: str = Field(alias="taskBName")
    overlap_start: datetime = Field(alias="overlapStart")
    overlap_end: datetime = Field(alias="overlapEnd")

    model_config = ConfigDict(populate_by_name=True)


class WorkloadAnalysisResult(BaseModel):
    """Top-level result of workload analysis."""

    total_tasks: int = Field(ge=0, default=0, alias="totalTasks")
    deadline_collisions: list[DeadlineCollision] = Field(default_factory=list, alias="deadlineCollisions")
    busy_days: list[BusyDayWarning] = Field(default_factory=list, alias="busyDays")
    recommended_order: list[TaskSummary] = Field(default_factory=list, alias="recommendedOrder")
    explanation: str = ""
    total_planned_minutes: int = Field(ge=0, default=0, alias="totalPlannedMinutes")
    feasibility_windows: list[FeasibilityWindow] = Field(default_factory=list, alias="feasibilityWindows")
    daily_pressure: list[DailyPressure] = Field(default_factory=list, alias="dailyPressure")
    overload_warnings: list[OverloadWarning] = Field(default_factory=list, alias="overloadWarnings")
    scheduling_pressure: list[SchedulingPressure] = Field(default_factory=list, alias="schedulingPressure")

    model_config = ConfigDict(populate_by_name=True)
