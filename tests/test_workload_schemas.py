"""Unit tests for the workload analysis schemas."""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from personal_deadline_management_agent.models import TaskPriority, TaskStatus
from personal_deadline_management_agent.schemas import (
    BusyDayWarning,
    DeadlineCollision,
    TaskSummary,
    WorkloadAnalysisResult,
)


def _summary(
    name: str = "Task",
    priority: TaskPriority = TaskPriority.MEDIUM,
    status: TaskStatus = TaskStatus.TODO,
    deadline: datetime | None = None,
) -> TaskSummary:
    return TaskSummary(
        id=uuid4(),
        task_name=name,
        priority=priority,
        status=status,
        deadline=deadline or datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc),
    )


# --- TaskSummary --------------------------------------------------------------


def test_task_summary_valid_construction():
    task_id = uuid4()
    deadline = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
    summary = TaskSummary(
        id=task_id,
        task_name="Prepare report",
        priority=TaskPriority.HIGH,
        status=TaskStatus.TODO,
        deadline=deadline,
    )
    assert summary.id == task_id
    assert summary.task_name == "Prepare report"
    assert summary.priority == TaskPriority.HIGH
    assert summary.status == TaskStatus.TODO
    assert summary.deadline == deadline


def test_task_summary_serialization_round_trip():
    summary = _summary("Round trip")
    restored = TaskSummary.model_validate(summary.model_dump())
    assert restored == summary


def test_task_summary_with_none_deadline():
    """Test TaskSummary accepts None for deadline."""
    task_id = uuid4()
    summary = TaskSummary(
        id=task_id,
        task_name="No Deadline Task",
        priority=TaskPriority.MEDIUM,
        status=TaskStatus.TODO,
        deadline=None,
    )
    assert summary.id == task_id
    assert summary.task_name == "No Deadline Task"
    assert summary.deadline is None


# --- DeadlineCollision --------------------------------------------------------


def test_deadline_collision_nested_tasks():
    deadline = datetime(2026, 9, 20, 17, 0, tzinfo=timezone.utc)
    task_a = _summary("Task A", priority=TaskPriority.HIGH, deadline=deadline)
    task_b = _summary("Task B", priority=TaskPriority.HIGH, deadline=deadline)

    collision = DeadlineCollision(deadline=deadline, tasks=[task_a, task_b])

    assert collision.deadline == deadline
    assert len(collision.tasks) == 2
    assert collision.tasks[0] == task_a
    assert collision.tasks[1] == task_b


def test_deadline_collision_serialization_round_trip():
    deadline = datetime(2026, 9, 20, 17, 0, tzinfo=timezone.utc)
    collision = DeadlineCollision(
        deadline=deadline,
        tasks=[_summary("A", deadline=deadline), _summary("B", deadline=deadline)],
    )
    restored = DeadlineCollision.model_validate(collision.model_dump())
    assert restored == collision


# --- BusyDayWarning -----------------------------------------------------------


def test_busy_day_warning_valid_construction():
    day = date(2026, 9, 18)
    tasks = [_summary("A"), _summary("B"), _summary("C")]

    warning = BusyDayWarning(date=day, task_count=3, tasks=tasks)

    assert warning.date == day
    assert warning.task_count == 3
    assert len(warning.tasks) == 3


def test_busy_day_warning_serialization_round_trip():
    warning = BusyDayWarning(
        date=date(2026, 9, 18),
        task_count=2,
        tasks=[_summary("A"), _summary("B")],
    )
    restored = BusyDayWarning.model_validate(warning.model_dump())
    assert restored == warning


def test_busy_day_warning_rejects_negative_count():
    with pytest.raises(ValidationError):
        BusyDayWarning(date=date(2026, 9, 18), task_count=-1, tasks=[])


# --- WorkloadAnalysisResult ---------------------------------------------------


def test_workload_analysis_result_all_fields():
    deadline = datetime(2026, 9, 20, 17, 0, tzinfo=timezone.utc)
    task_a = _summary("A", priority=TaskPriority.HIGH, deadline=deadline)
    task_b = _summary("B", priority=TaskPriority.HIGH, deadline=deadline)

    result = WorkloadAnalysisResult(
        total_tasks=3,
        deadline_collisions=[
            DeadlineCollision(deadline=deadline, tasks=[task_a, task_b])
        ],
        busy_days=[
            BusyDayWarning(date=date(2026, 9, 18), task_count=3, tasks=[task_a, task_b, _summary("C")])
        ],
        recommended_order=[task_a, task_b],
        explanation="Two high-priority tasks collide on Sep 20.",
    )

    assert result.total_tasks == 3
    assert len(result.deadline_collisions) == 1
    assert result.deadline_collisions[0].tasks[0] == task_a
    assert len(result.busy_days) == 1
    assert result.busy_days[0].task_count == 3
    assert result.recommended_order == [task_a, task_b]
    assert "collide" in result.explanation


def test_workload_analysis_result_empty():
    result = WorkloadAnalysisResult()

    assert result.total_tasks == 0
    assert result.deadline_collisions == []
    assert result.busy_days == []
    assert result.recommended_order == []
    assert result.explanation == ""


def test_workload_analysis_result_serialization_round_trip():
    deadline = datetime(2026, 9, 20, 17, 0, tzinfo=timezone.utc)
    task = _summary("A", deadline=deadline)
    result = WorkloadAnalysisResult(
        total_tasks=1,
        deadline_collisions=[DeadlineCollision(deadline=deadline, tasks=[task])],
        busy_days=[BusyDayWarning(date=date(2026, 9, 20), task_count=1, tasks=[task])],
        recommended_order=[task],
        explanation="ok",
    )
    restored = WorkloadAnalysisResult.model_validate(result.model_dump())
    assert restored == result


def test_workload_analysis_result_rejects_negative_total():
    with pytest.raises(ValidationError):
        WorkloadAnalysisResult(total_tasks=-1)