"""PDMA-110: Deterministic candidate generation for multiple tasks.

Tests ReschedulingCandidateService which wraps ReschedulingConstraintService
to analyze an entire workload.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from personal_deadline_management_agent.models import TaskPriority, TaskStatus
from personal_deadline_management_agent.schemas.workload import TaskSummary
from personal_deadline_management_agent.services.rescheduling_candidate_service import (
    ReschedulingCandidateService,
)


def _dt(hour: int, minute: int = 0, day: int = 25) -> datetime:
    """Helper: datetime in Sep 2026 UTC."""
    return datetime(2026, 9, day, hour, minute, tzinfo=timezone.utc)


def _task(
    name: str,
    *,
    deadline: datetime | None = _dt(18),
    duration: int | None = 60,
    priority: TaskPriority = TaskPriority.MEDIUM,
    status: TaskStatus = TaskStatus.TODO,
    task_id: UUID | None = None,
) -> TaskSummary:
    """Helper: create TaskSummary."""
    return TaskSummary(
        id=task_id or uuid4(),
        task_name=name,
        priority=priority,
        status=status,
        deadline=deadline,
        duration_minutes=duration,
    )


# ---------------------------------------------------------------------------
# Basic tests
# ---------------------------------------------------------------------------


def test_empty_input_returns_empty_result():
    """Empty input produces empty ReschedulingResult."""
    svc = ReschedulingCandidateService()
    result = svc.generate_candidates([])
    assert result.suggestions == []
    assert result.unscheduled_tasks == []
    assert result.overloaded_days == []


def test_single_eligible_task_produces_one_suggestion():
    """Single task with known duration and deadline produces one suggestion."""
    svc = ReschedulingCandidateService()
    task = _task("Task A", deadline=_dt(18), duration=60)
    result = svc.generate_candidates([task])
    assert len(result.suggestions) == 1
    assert result.suggestions[0].task_name == "Task A"
    assert result.suggestions[0].duration_minutes == 60
    assert result.suggestions[0].current_deadline == _dt(18)
    assert result.suggestions[0].candidate_slot.end == _dt(18)
    assert result.unscheduled_tasks == []


def test_multiple_eligible_tasks_produce_multiple_suggestions():
    """Multiple eligible tasks produce multiple suggestions."""
    svc = ReschedulingCandidateService()
    tasks = [
        _task("Task A", deadline=_dt(10), duration=60),
        _task("Task B", deadline=_dt(14), duration=90),
        _task("Task C", deadline=_dt(18), duration=120),
    ]
    result = svc.generate_candidates(tasks)
    assert len(result.suggestions) == 3
    names = [s.task_name for s in result.suggestions]
    assert "Task A" in names
    assert "Task B" in names
    assert "Task C" in names


# ---------------------------------------------------------------------------
# Missing information tests
# ---------------------------------------------------------------------------


def test_missing_duration_goes_to_unscheduled():
    """Task with missing duration_minutes goes to unscheduled."""
    svc = ReschedulingCandidateService()
    task = _task("Task A", deadline=_dt(18), duration=None)
    result = svc.generate_candidates([task])
    assert len(result.suggestions) == 0
    assert len(result.unscheduled_tasks) == 1
    assert result.unscheduled_tasks[0].task_name == "Task A"


def test_missing_deadline_goes_to_unscheduled():
    """Task with missing deadline goes to unscheduled."""
    svc = ReschedulingCandidateService()
    task = _task("Task A", deadline=None, duration=60)
    result = svc.generate_candidates([task])
    assert len(result.suggestions) == 0
    assert len(result.unscheduled_tasks) == 1
    assert result.unscheduled_tasks[0].task_name == "Task A"


def test_missing_both_goes_to_unscheduled():
    """Task with missing duration and deadline goes to unscheduled."""
    svc = ReschedulingCandidateService()
    task = _task("Task A", deadline=None, duration=None)
    result = svc.generate_candidates([task])
    assert len(result.suggestions) == 0
    assert len(result.unscheduled_tasks) == 1


def test_zero_duration_goes_to_unscheduled():
    """Task with zero duration goes to unscheduled."""
    svc = ReschedulingCandidateService()
    task = _task("Task A", deadline=_dt(18), duration=0)
    result = svc.generate_candidates([task])
    assert len(result.suggestions) == 0
    assert len(result.unscheduled_tasks) == 1


def test_negative_duration_goes_to_unscheduled():
    """Task with negative duration goes to unscheduled."""
    svc = ReschedulingCandidateService()
    task = _task("Task A", deadline=_dt(18), duration=-60)
    result = svc.generate_candidates([task])
    assert len(result.suggestions) == 0
    assert len(result.unscheduled_tasks) == 1


# ---------------------------------------------------------------------------
# Capacity tests
# ---------------------------------------------------------------------------


def test_existing_active_workload_consumes_capacity():
    """Existing active task workload reduces available daily capacity."""
    svc = ReschedulingCandidateService(budget_minutes=120)
    tasks = [
        _task("Task A", deadline=_dt(18), duration=100, status=TaskStatus.TODO),
        _task("Task B", deadline=_dt(18), duration=60, status=TaskStatus.TODO),
    ]
    result = svc.generate_candidates(tasks, budget_minutes=120)
    # Both tasks require same deadline day; total=160min exceeds 120min budget
    # Constraint engine processes one at a time but sees the other's workload
    # At least one should be unscheduled due to capacity
    assert len(result.suggestions) < 2


def test_completed_tasks_do_not_consume_capacity():
    """Completed tasks do not consume daily capacity."""
    svc = ReschedulingCandidateService(budget_minutes=120)
    tasks = [
        _task("Task A", deadline=_dt(18), duration=100, status=TaskStatus.COMPLETED),
        _task("Task B", deadline=_dt(18), duration=60, status=TaskStatus.TODO),
    ]
    result = svc.generate_candidates(tasks, budget_minutes=120)
    # Task A is completed, doesn't count toward capacity
    # Task B=60min fits in 120min budget
    assert len(result.suggestions) == 1
    assert result.suggestions[0].task_name == "Task B"


def test_candidate_rejected_when_daily_budget_exceeded():
    """Candidate rejected when adding it would exceed daily budget."""
    svc = ReschedulingCandidateService(budget_minutes=100)
    tasks = [
        _task("Task A", deadline=_dt(18), duration=80, status=TaskStatus.TODO),
        _task("Task B", deadline=_dt(18), duration=60, status=TaskStatus.TODO),
    ]
    result = svc.generate_candidates(tasks, budget_minutes=100)
    # 80+60=140 > 100, so not both can fit
    assert len(result.suggestions) < 2
    assert len(result.unscheduled_tasks) > 0


def test_candidate_accepted_when_daily_budget_exactly_fits():
    """Candidate accepted when it exactly fits daily budget."""
    svc = ReschedulingCandidateService(budget_minutes=120)
    tasks = [
        _task("Task A", deadline=_dt(18), duration=60, status=TaskStatus.TODO),
        _task("Task B", deadline=_dt(18), duration=60, status=TaskStatus.TODO),
    ]
    result = svc.generate_candidates(tasks, budget_minutes=120)
    # 60+60=120, exactly fits
    assert len(result.suggestions) == 2


def test_multiple_tasks_on_same_deadline_respect_capacity():
    """Multiple tasks with same deadline date respect daily capacity model."""
    svc = ReschedulingCandidateService(budget_minutes=180)
    tasks = [
        _task("Task A", deadline=_dt(18), duration=60),
        _task("Task B", deadline=_dt(18), duration=60),
        _task("Task C", deadline=_dt(18), duration=60),
        _task("Task D", deadline=_dt(18), duration=60),
    ]
    result = svc.generate_candidates(tasks, budget_minutes=180)
    # 4*60=240 > 180, so max 3 can fit
    assert len(result.suggestions) <= 3


# ---------------------------------------------------------------------------
# Ordering tests
# ---------------------------------------------------------------------------


def test_suggestions_are_deterministic():
    """Suggestions list is deterministically ordered."""
    svc = ReschedulingCandidateService()
    tasks = [
        _task("Task C", deadline=_dt(18), duration=60, priority=TaskPriority.LOW),
        _task("Task A", deadline=_dt(10), duration=60, priority=TaskPriority.HIGH),
        _task("Task B", deadline=_dt(14), duration=60, priority=TaskPriority.MEDIUM),
    ]
    result1 = svc.generate_candidates(tasks)
    result2 = svc.generate_candidates(tasks)
    assert [s.task_name for s in result1.suggestions] == [
        s.task_name for s in result2.suggestions
    ]
    # Order should be: deadline ASC, priority DESC, name ASC
    names = [s.task_name for s in result1.suggestions]
    assert names == ["Task A", "Task B", "Task C"]


def test_unscheduled_tasks_are_deterministic():
    """Unscheduled tasks list is deterministically ordered."""
    svc = ReschedulingCandidateService()
    tasks = [
        _task("Task C", deadline=None, duration=60, priority=TaskPriority.LOW),
        _task("Task A", deadline=_dt(10), duration=None, priority=TaskPriority.HIGH),
        _task("Task B", deadline=None, duration=None, priority=TaskPriority.MEDIUM),
    ]
    result1 = svc.generate_candidates(tasks)
    result2 = svc.generate_candidates(tasks)
    assert [t.task_name for t in result1.unscheduled_tasks] == [
        t.task_name for t in result2.unscheduled_tasks
    ]


def test_overloaded_days_are_ascending():
    """Overloaded days are sorted in ascending date order."""
    svc = ReschedulingCandidateService(budget_minutes=60)
    tasks = [
        _task("Task A", deadline=_dt(18, day=25), duration=100),
        _task("Task B", deadline=_dt(18, day=26), duration=100),
        _task("Task C", deadline=_dt(18, day=24), duration=100),
    ]
    result = svc.generate_candidates(tasks, budget_minutes=60)
    if len(result.overloaded_days) > 1:
        for i in range(len(result.overloaded_days) - 1):
            assert result.overloaded_days[i] < result.overloaded_days[i + 1]


def test_repeated_execution_with_identical_input_returns_identical_output():
    """Idempotence: same input produces same output."""
    svc = ReschedulingCandidateService()
    tasks = [
        _task("Task A", deadline=_dt(10), duration=60),
        _task("Task B", deadline=_dt(14), duration=90),
        _task("Task C", deadline=None, duration=60),
    ]
    result1 = svc.generate_candidates(tasks)
    result2 = svc.generate_candidates(tasks)
    assert len(result1.suggestions) == len(result2.suggestions)
    assert len(result1.unscheduled_tasks) == len(result2.unscheduled_tasks)
    assert len(result1.overloaded_days) == len(result2.overloaded_days)
    assert [s.task_name for s in result1.suggestions] == [
        s.task_name for s in result2.suggestions
    ]


# ---------------------------------------------------------------------------
# Constraint tests
# ---------------------------------------------------------------------------


def test_candidate_ends_exactly_at_deadline():
    """Generated candidate slot ends exactly at task deadline."""
    svc = ReschedulingCandidateService()
    task = _task("Task A", deadline=_dt(18), duration=120)
    result = svc.generate_candidates([task])
    assert len(result.suggestions) == 1
    assert result.suggestions[0].candidate_slot.end == _dt(18)


def test_candidate_starts_exactly_at_latest_start():
    """Generated candidate slot starts at feasibility latest_start."""
    svc = ReschedulingCandidateService()
    task = _task("Task A", deadline=_dt(18), duration=120)
    result = svc.generate_candidates([task])
    assert len(result.suggestions) == 1
    expected_start = _dt(18) - timedelta(minutes=120)
    assert result.suggestions[0].candidate_slot.start == expected_start


def test_candidate_never_exceeds_deadline():
    """No candidate slot extends past task deadline."""
    svc = ReschedulingCandidateService()
    tasks = [
        _task("Task A", deadline=_dt(10), duration=60),
        _task("Task B", deadline=_dt(18), duration=240),
    ]
    result = svc.generate_candidates(tasks)
    for suggestion in result.suggestions:
        assert suggestion.candidate_slot.end <= suggestion.current_deadline


def test_candidate_has_sufficient_duration():
    """Candidate slot duration matches task duration requirement."""
    svc = ReschedulingCandidateService()
    task = _task("Task A", deadline=_dt(18), duration=90)
    result = svc.generate_candidates([task])
    assert len(result.suggestions) == 1
    slot = result.suggestions[0].candidate_slot
    slot_duration = int((slot.end - slot.start).total_seconds() / 60)
    assert slot_duration >= 90


# ---------------------------------------------------------------------------
# Scope / Architecture tests
# ---------------------------------------------------------------------------


def test_no_llm_invocation():
    """Service does not invoke LLM (reason is deterministic)."""
    svc = ReschedulingCandidateService()
    task = _task("Task A", deadline=_dt(18), duration=60)
    result = svc.generate_candidates([task])
    # Reason should be the deterministic constant
    assert result.suggestions[0].reason == (
        "Fits within the task feasibility window and remaining daily capacity."
    )


def test_service_is_stateless():
    """Service can be reused without state pollution."""
    svc = ReschedulingCandidateService()
    tasks1 = [_task("Task A", deadline=_dt(10), duration=60)]
    tasks2 = [_task("Task B", deadline=_dt(14), duration=90)]
    result1 = svc.generate_candidates(tasks1)
    result2 = svc.generate_candidates(tasks2)
    assert len(result1.suggestions) == 1
    assert result1.suggestions[0].task_name == "Task A"
    assert len(result2.suggestions) == 1
    assert result2.suggestions[0].task_name == "Task B"


def test_duplicate_tasks_handled_deterministically():
    """Duplicate tasks (by id) are deduplicated."""
    svc = ReschedulingCandidateService()
    task_id = uuid4()
    tasks = [
        _task("Task A", deadline=_dt(18), duration=60, task_id=task_id),
        _task("Task A duplicate", deadline=_dt(18), duration=60, task_id=task_id),
    ]
    result = svc.generate_candidates(tasks)
    # Only one suggestion for the duplicate id
    assert len(result.suggestions) == 1


def test_completed_tasks_excluded_from_rescheduling():
    """Completed tasks do not appear in suggestions or unscheduled."""
    svc = ReschedulingCandidateService()
    tasks = [
        _task("Task A", deadline=_dt(18), duration=60, status=TaskStatus.COMPLETED),
        _task("Task B", deadline=_dt(18), duration=60, status=TaskStatus.TODO),
    ]
    result = svc.generate_candidates(tasks)
    assert len(result.suggestions) == 1
    assert result.suggestions[0].task_name == "Task B"
    # Completed task should not be in unscheduled either
    assert len(result.unscheduled_tasks) == 0


def test_cancelled_tasks_excluded_from_rescheduling():
    """Cancelled tasks do not appear in suggestions or unscheduled."""
    svc = ReschedulingCandidateService()
    tasks = [
        _task("Task A", deadline=_dt(18), duration=60, status=TaskStatus.CANCELLED),
        _task("Task B", deadline=_dt(18), duration=60, status=TaskStatus.TODO),
    ]
    result = svc.generate_candidates(tasks)
    assert len(result.suggestions) == 1
    assert result.suggestions[0].task_name == "Task B"
    assert len(result.unscheduled_tasks) == 0


# ---------------------------------------------------------------------------
# Budget override tests
# ---------------------------------------------------------------------------


def test_budget_override_respected():
    """Explicit budget_minutes parameter overrides instance default."""
    svc = ReschedulingCandidateService(budget_minutes=480)
    tasks = [
        _task("Task A", deadline=_dt(18), duration=300),
        _task("Task B", deadline=_dt(18), duration=300),
    ]
    # Override with tighter budget
    result = svc.generate_candidates(tasks, budget_minutes=400)
    # 300+300=600 > 400, so not both fit
    assert len(result.suggestions) < 2
