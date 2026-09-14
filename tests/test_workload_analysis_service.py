"""Unit tests for WorkloadAnalysisService."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from personal_deadline_management_agent.models import Task, TaskPriority, TaskStatus
from personal_deadline_management_agent.schemas.workload import TaskSummary
from personal_deadline_management_agent.services.workload_analysis_service import (
    WorkloadAnalysisService,
)


# --- Test fixtures -----------------------------------------------------------


def _task(
    task_name: str,
    priority: TaskPriority,
    status: TaskStatus,
    deadline: datetime | None = None,
) -> Task:
    """Create a Task instance for testing."""
    task = Task(
        task_name=task_name,
        description=None,
        deadline=deadline,
        priority=priority.value,
        status=status.value,
    )
    task.id = uuid4()
    return task


def _summary(
    task_name: str,
    priority: TaskPriority,
    status: TaskStatus,
    deadline: datetime | None = None,
) -> TaskSummary:
    """Create a TaskSummary instance for testing."""
    return TaskSummary(
        id=uuid4(),
        task_name=task_name,
        priority=priority,
        status=status,
        deadline=deadline,
    )


# --- Test 1: Empty workload --------------------------------------------------


def test_empty_workload():
    """Test analysis with empty input."""
    service = WorkloadAnalysisService()
    result = service.analyze([])

    assert result.total_tasks == 0
    assert result.deadline_collisions == []
    assert result.busy_days == []
    assert result.recommended_order == []
    assert "no active tasks" in result.explanation.lower()


# --- Test 2: Active task filtering -------------------------------------------


def test_active_task_filtering():
    """Test that only TODO and IN_PROGRESS tasks are included."""
    service = WorkloadAnalysisService()
    deadline = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)

    tasks = [
        _task("Task A", TaskPriority.HIGH, TaskStatus.TODO, deadline),
        _task("Task B", TaskPriority.HIGH, TaskStatus.IN_PROGRESS, deadline),
        _task("Task C", TaskPriority.HIGH, TaskStatus.COMPLETED, deadline),
    ]

    result = service.analyze(tasks)

    assert result.total_tasks == 2
    assert len(result.recommended_order) == 2
    assert all(t.task_name in ["Task A", "Task B"] for t in result.recommended_order)


# --- Test 3: Total task count ------------------------------------------------


def test_total_task_count_excludes_completed():
    """Test that completed tasks are excluded from total_tasks."""
    service = WorkloadAnalysisService()
    deadline = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)

    tasks = [
        _task("Active 1", TaskPriority.MEDIUM, TaskStatus.TODO, deadline),
        _task("Active 2", TaskPriority.MEDIUM, TaskStatus.IN_PROGRESS, deadline),
        _task("Completed 1", TaskPriority.MEDIUM, TaskStatus.COMPLETED, deadline),
        _task("Completed 2", TaskPriority.MEDIUM, TaskStatus.COMPLETED, deadline),
    ]

    result = service.analyze(tasks)

    assert result.total_tasks == 2


# --- Test 4: High-priority deadline collision --------------------------------


def test_high_priority_deadline_collision():
    """Test collision detection for two active HIGH tasks with same deadline."""
    service = WorkloadAnalysisService()
    deadline = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)

    tasks = [
        _task("Task A", TaskPriority.HIGH, TaskStatus.TODO, deadline),
        _task("Task B", TaskPriority.HIGH, TaskStatus.TODO, deadline),
    ]

    result = service.analyze(tasks)

    assert len(result.deadline_collisions) == 1
    collision = result.deadline_collisions[0]
    assert collision.deadline == deadline
    assert len(collision.tasks) == 2
    assert {t.task_name for t in collision.tasks} == {"Task A", "Task B"}


# --- Test 5: No collision for different deadlines ----------------------------


def test_no_collision_for_different_deadlines():
    """Test that different deadlines do not create a collision."""
    service = WorkloadAnalysisService()
    deadline1 = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)
    deadline2 = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)

    tasks = [
        _task("Task A", TaskPriority.HIGH, TaskStatus.TODO, deadline1),
        _task("Task B", TaskPriority.HIGH, TaskStatus.TODO, deadline2),
    ]

    result = service.analyze(tasks)

    assert len(result.deadline_collisions) == 0


# --- Test 6: No collision for non-HIGH tasks ---------------------------------


def test_no_collision_for_non_high_tasks():
    """Test that MEDIUM/LOW tasks do not create collisions."""
    service = WorkloadAnalysisService()
    deadline = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)

    tasks = [
        _task("Task A", TaskPriority.MEDIUM, TaskStatus.TODO, deadline),
        _task("Task B", TaskPriority.MEDIUM, TaskStatus.TODO, deadline),
        _task("Task C", TaskPriority.LOW, TaskStatus.TODO, deadline),
    ]

    result = service.analyze(tasks)

    assert len(result.deadline_collisions) == 0


# --- Test 7: No collision with completed task --------------------------------


def test_no_collision_with_completed_task():
    """Test that completed tasks do not participate in collisions."""
    service = WorkloadAnalysisService()
    deadline = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)

    tasks = [
        _task("Active", TaskPriority.HIGH, TaskStatus.TODO, deadline),
        _task("Completed", TaskPriority.HIGH, TaskStatus.COMPLETED, deadline),
    ]

    result = service.analyze(tasks)

    assert len(result.deadline_collisions) == 0


# --- Test 8: Collision ordering ----------------------------------------------


def test_collision_ordering():
    """Test that collision groups are ordered by deadline ASC."""
    service = WorkloadAnalysisService()
    deadline1 = datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc)
    deadline2 = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)

    tasks = [
        _task("Task A", TaskPriority.HIGH, TaskStatus.TODO, deadline2),
        _task("Task B", TaskPriority.HIGH, TaskStatus.TODO, deadline2),
        _task("Task C", TaskPriority.HIGH, TaskStatus.TODO, deadline1),
        _task("Task D", TaskPriority.HIGH, TaskStatus.TODO, deadline1),
    ]

    result = service.analyze(tasks)

    assert len(result.deadline_collisions) == 2
    assert result.deadline_collisions[0].deadline == deadline1
    assert result.deadline_collisions[1].deadline == deadline2

    # Tasks inside each group should be sorted by task_name ASC
    group1_names = [t.task_name for t in result.deadline_collisions[0].tasks]
    assert group1_names == ["Task C", "Task D"]

    group2_names = [t.task_name for t in result.deadline_collisions[1].tasks]
    assert group2_names == ["Task A", "Task B"]


# --- Test 9: Busy day threshold ----------------------------------------------


def test_busy_day_threshold():
    """Test that >5 active tasks creates a busy day warning."""
    service = WorkloadAnalysisService()
    date_base = datetime(2026, 9, 20, tzinfo=timezone.utc)

    # Create 5 tasks - should NOT be busy
    tasks_5 = [
        _task(f"Task {i}", TaskPriority.MEDIUM, TaskStatus.TODO, date_base.replace(hour=i))
        for i in range(5)
    ]
    result_5 = service.analyze(tasks_5)
    assert len(result_5.busy_days) == 0

    # Create 6 tasks - should be busy
    tasks_6 = [
        _task(f"Task {i}", TaskPriority.MEDIUM, TaskStatus.TODO, date_base.replace(hour=i))
        for i in range(6)
    ]
    result_6 = service.analyze(tasks_6)
    assert len(result_6.busy_days) == 1
    assert result_6.busy_days[0].task_count == 6


# --- Test 10: Busy day excludes completed tasks ------------------------------


def test_busy_day_excludes_completed_tasks():
    """Test that completed tasks do not count toward busy day."""
    service = WorkloadAnalysisService()
    date_base = datetime(2026, 9, 20, tzinfo=timezone.utc)

    tasks = [
        _task(f"Active {i}", TaskPriority.MEDIUM, TaskStatus.TODO, date_base.replace(hour=i))
        for i in range(5)
    ]
    tasks.append(
        _task("Completed", TaskPriority.MEDIUM, TaskStatus.COMPLETED, date_base.replace(hour=5))
    )

    result = service.analyze(tasks)

    # 5 active + 1 completed = not busy
    assert len(result.busy_days) == 0


# --- Test 11: Busy-day grouping ----------------------------------------------


def test_busy_day_grouping():
    """Test that tasks are grouped by UTC calendar date and ordered by date ASC."""
    service = WorkloadAnalysisService()
    date1 = datetime(2026, 9, 18, tzinfo=timezone.utc)
    date2 = datetime(2026, 9, 20, tzinfo=timezone.utc)

    tasks = []
    # 6 tasks on date2
    for i in range(6):
        tasks.append(
            _task(f"Task {i}", TaskPriority.MEDIUM, TaskStatus.TODO, date2.replace(hour=i))
        )
    # 7 tasks on date1
    for i in range(7):
        tasks.append(
            _task(f"Task {i+6}", TaskPriority.MEDIUM, TaskStatus.TODO, date1.replace(hour=i))
        )

    result = service.analyze(tasks)

    assert len(result.busy_days) == 2
    assert result.busy_days[0].date == date1.date()
    assert result.busy_days[0].task_count == 7
    assert result.busy_days[1].date == date2.date()
    assert result.busy_days[1].task_count == 6


# --- Test 12: Tasks without deadline -----------------------------------------


def test_tasks_without_deadline():
    """Test that tasks with deadline=None are handled correctly."""
    service = WorkloadAnalysisService()
    deadline = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)

    tasks = [
        _task("Task A", TaskPriority.HIGH, TaskStatus.TODO, deadline),
        _task("Task B", TaskPriority.MEDIUM, TaskStatus.TODO, None),
        _task("Task C", TaskPriority.LOW, TaskStatus.TODO, None),
    ]

    result = service.analyze(tasks)

    # All 3 are active
    assert result.total_tasks == 3

    # No collisions (Task B and C have no deadline)
    assert len(result.deadline_collisions) == 0

    # No busy days (Task B and C cannot be grouped)
    assert len(result.busy_days) == 0

    # Recommended order: Task A first (has deadline), then B, then C
    assert len(result.recommended_order) == 3
    assert result.recommended_order[0].task_name == "Task A"
    # B and C both have no deadline, so sorted by priority DESC then name ASC
    assert result.recommended_order[1].task_name == "Task B"  # MEDIUM before LOW
    assert result.recommended_order[2].task_name == "Task C"


# --- Test 13: Recommended order ----------------------------------------------


def test_recommended_order():
    """Test deterministic ordering: deadline ASC, priority DESC, task_name ASC."""
    service = WorkloadAnalysisService()
    deadline1 = datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc)
    deadline2 = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)

    tasks = [
        _task("Zebra", TaskPriority.LOW, TaskStatus.TODO, deadline2),
        _task("Alpha", TaskPriority.HIGH, TaskStatus.TODO, deadline1),
        _task("Beta", TaskPriority.MEDIUM, TaskStatus.TODO, deadline1),
        _task("Gamma", TaskPriority.HIGH, TaskStatus.TODO, deadline2),
    ]

    result = service.analyze(tasks)

    expected_order = [
        ("Alpha", TaskPriority.HIGH, deadline1),  # earliest deadline, HIGH priority
        ("Beta", TaskPriority.MEDIUM, deadline1),  # same deadline, lower priority
        ("Gamma", TaskPriority.HIGH, deadline2),  # later deadline, HIGH priority
        ("Zebra", TaskPriority.LOW, deadline2),  # same deadline, lower priority
    ]

    assert len(result.recommended_order) == 4
    for i, (name, priority, deadline) in enumerate(expected_order):
        assert result.recommended_order[i].task_name == name
        assert result.recommended_order[i].priority == priority
        assert result.recommended_order[i].deadline == deadline


# --- Test 14: Does not mutate input ------------------------------------------


def test_does_not_mutate_input():
    """Test that the service does not mutate the input task list."""
    service = WorkloadAnalysisService()
    deadline = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)

    original_tasks = [
        _task("Task C", TaskPriority.LOW, TaskStatus.TODO, deadline),
        _task("Task A", TaskPriority.HIGH, TaskStatus.TODO, deadline),
        _task("Task B", TaskPriority.MEDIUM, TaskStatus.TODO, deadline),
    ]

    # Store original order
    original_order = [t.task_name for t in original_tasks]

    result = service.analyze(original_tasks)

    # Verify input list is unchanged
    assert [t.task_name for t in original_tasks] == original_order

    # Verify result has a different order
    result_order = [t.task_name for t in result.recommended_order]
    assert result_order != original_order
    assert result_order == ["Task A", "Task B", "Task C"]


# --- Test 15: Deterministic explanation --------------------------------------


def test_deterministic_explanation():
    """Test that the explanation is deterministic for the same input."""
    service = WorkloadAnalysisService()
    deadline = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)

    tasks = [
        _task("Task A", TaskPriority.HIGH, TaskStatus.TODO, deadline),
        _task("Task B", TaskPriority.MEDIUM, TaskStatus.TODO, deadline),
    ]

    result1 = service.analyze(tasks)
    result2 = service.analyze(tasks)

    assert result1.explanation == result2.explanation


# --- Test 16: Result schema --------------------------------------------------


def test_result_schema():
    """Test that the result is a WorkloadAnalysisResult instance."""
    from personal_deadline_management_agent.schemas.workload import (
        WorkloadAnalysisResult,
    )

    service = WorkloadAnalysisService()
    deadline = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)

    tasks = [
        _task("Task A", TaskPriority.HIGH, TaskStatus.TODO, deadline),
    ]

    result = service.analyze(tasks)

    assert isinstance(result, WorkloadAnalysisResult)
    assert hasattr(result, "total_tasks")
    assert hasattr(result, "deadline_collisions")
    assert hasattr(result, "busy_days")
    assert hasattr(result, "recommended_order")
    assert hasattr(result, "explanation")


# --- Test 17: TaskSummary input ----------------------------------------------


def test_accepts_task_summary_input():
    """Test that the service accepts TaskSummary objects as input."""
    service = WorkloadAnalysisService()
    deadline = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)

    summaries = [
        _summary("Task A", TaskPriority.HIGH, TaskStatus.TODO, deadline),
        _summary("Task B", TaskPriority.MEDIUM, TaskStatus.TODO, deadline),
    ]

    result = service.analyze(summaries)

    assert result.total_tasks == 2
    assert len(result.recommended_order) == 2


# --- Test 18: Mixed input types ----------------------------------------------


def test_accepts_mixed_input_types():
    """Test that the service accepts a mix of Task and TaskSummary."""
    service = WorkloadAnalysisService()
    deadline = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)

    mixed = [
        _task("Task A", TaskPriority.HIGH, TaskStatus.TODO, deadline),
        _summary("Task B", TaskPriority.MEDIUM, TaskStatus.TODO, deadline),
    ]

    result = service.analyze(mixed)

    assert result.total_tasks == 2
    assert len(result.recommended_order) == 2


# --- Test 19: Collision with 3+ tasks ----------------------------------------


def test_collision_with_three_or_more_tasks():
    """Test collision detection with 3+ tasks."""
    service = WorkloadAnalysisService()
    deadline = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)

    tasks = [
        _task("Task A", TaskPriority.HIGH, TaskStatus.TODO, deadline),
        _task("Task B", TaskPriority.HIGH, TaskStatus.TODO, deadline),
        _task("Task C", TaskPriority.HIGH, TaskStatus.TODO, deadline),
    ]

    result = service.analyze(tasks)

    assert len(result.deadline_collisions) == 1
    collision = result.deadline_collisions[0]
    assert len(collision.tasks) == 3
    assert {t.task_name for t in collision.tasks} == {"Task A", "Task B", "Task C"}


# --- Test 20: Explanation format ---------------------------------------------


def test_explanation_format():
    """Test that explanation contains expected sections."""
    service = WorkloadAnalysisService()
    deadline = datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc)

    tasks = [
        _task("Task A", TaskPriority.HIGH, TaskStatus.TODO, deadline),
        _task("Task B", TaskPriority.HIGH, TaskStatus.TODO, deadline),
    ]
    # Add 6 more tasks to create busy day
    for i in range(6):
        tasks.append(
            _task(f"Task {i+3}", TaskPriority.MEDIUM, TaskStatus.TODO, deadline.replace(hour=i))
        )

    result = service.analyze(tasks)

    explanation = result.explanation

    # Check for expected sections
    assert "8 active tasks" in explanation
    assert "Deadline Collisions:" in explanation
    assert "Busy Days:" in explanation
    assert "Recommended Order:" in explanation
    assert "Note:" in explanation
    assert "task duration, dependencies, calendar availability" in explanation
