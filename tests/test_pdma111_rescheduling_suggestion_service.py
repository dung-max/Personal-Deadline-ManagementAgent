"""PDMA-111: Rescheduling Suggestion Service tests."""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, MagicMock, call
from uuid import UUID, uuid4

import pytest

from personal_deadline_management_agent.models import TaskPriority, TaskStatus
from personal_deadline_management_agent.schemas.workload import (
    CandidateSlot,
    ReschedulingSuggestion,
    ReschedulingResult,
    TaskSummary,
)
from personal_deadline_management_agent.services.rescheduling_suggestion_service import (
    ReschedulingSuggestionService,
)
from personal_deadline_management_agent.services.rescheduling_candidate_service import (
    ReschedulingCandidateService,
)
from personal_deadline_management_agent.services.rescheduling_constraint_service import (
    ReschedulingConstraintService,
)


def _dt(hour: int, minute: int = 0, day: int = 25) -> datetime:
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
    return TaskSummary(
        id=task_id or uuid4(),
        task_name=name,
        priority=priority,
        status=status,
        deadline=deadline,
        duration_minutes=duration,
    )


# ===========================================================================
# Basic tests
# ===========================================================================


def test_empty_workload_returns_empty_result():
    """Empty workload returns empty ReschedulingResult."""
    svc = ReschedulingSuggestionService()
    result = svc.suggest_rescheduling([])
    assert result.suggestions == []
    assert result.unscheduled_tasks == []
    assert result.overloaded_days == []


def test_single_eligible_task_produces_one_suggestion():
    """One eligible task produces one suggestion."""
    svc = ReschedulingSuggestionService()
    task = _task("Task A", deadline=_dt(18), duration=60)
    result = svc.suggest_rescheduling([task])
    assert len(result.suggestions) == 1
    assert result.suggestions[0].task_name == "Task A"
    assert result.suggestions[0].duration_minutes == 60
    assert result.suggestions[0].current_deadline == _dt(18)
    assert result.suggestions[0].candidate_slot.end == _dt(18)


def test_multiple_eligible_tasks_produce_multiple_suggestions():
    """Multiple eligible tasks produce multiple suggestions."""
    svc = ReschedulingSuggestionService()
    tasks = [
        _task("Task A", deadline=_dt(10), duration=60),
        _task("Task B", deadline=_dt(14), duration=60),
        _task("Task C", deadline=_dt(18), duration=60),
    ]
    result = svc.suggest_rescheduling(tasks)
    assert len(result.suggestions) == 3
    names = [s.task_name for s in result.suggestions]
    assert "Task A" in names
    assert "Task B" in names
    assert "Task C" in names


# ===========================================================================
# Candidate transformation tests
# ===========================================================================


def test_candidate_fields_correctly_copied():
    """Candidate slot fields are correctly copied into suggestions."""
    svc = ReschedulingSuggestionService()
    task = _task("Task A", deadline=_dt(18), duration=120)
    result = svc.suggest_rescheduling([task])
    assert len(result.suggestions) == 1
    suggestion = result.suggestions[0]
    # Candidate slot should span feasibility window
    slot = suggestion.candidate_slot
    assert slot.start == _dt(16)  # 18 - 120min
    assert slot.end == _dt(18)
    assert slot.available_minutes == 120


def test_task_metadata_preserved():
    """Task metadata (name, duration, deadline) is preserved in suggestions."""
    svc = ReschedulingSuggestionService()
    task_id = uuid4()
    task = _task("Important Task", deadline=_dt(18, day=26), duration=90, task_id=task_id)
    result = svc.suggest_rescheduling([task])
    assert result.suggestions[0].task_id == task_id
    assert result.suggestions[0].task_name == "Important Task"
    assert result.suggestions[0].duration_minutes == 90
    assert result.suggestions[0].current_deadline == _dt(18, day=26)


def test_candidate_slot_preserved():
    """Candidate slot is preserved without modification."""
    svc = ReschedulingSuggestionService()
    task = _task("Task A", deadline=_dt(18), duration=60)
    result = svc.suggest_rescheduling([task])
    slot = result.suggestions[0].candidate_slot
    assert isinstance(slot, CandidateSlot)
    assert slot.start == _dt(17)
    assert slot.end == _dt(18)


# ===========================================================================
# Unscheduled tests
# ===========================================================================


def test_missing_duration_remains_unscheduled():
    """Task with missing duration remains unscheduled."""
    svc = ReschedulingSuggestionService()
    task = _task("Task A", deadline=_dt(18), duration=None)
    result = svc.suggest_rescheduling([task])
    assert len(result.suggestions) == 0
    assert len(result.unscheduled_tasks) == 1
    assert result.unscheduled_tasks[0].task_name == "Task A"


def test_missing_deadline_remains_unscheduled():
    """Task with missing deadline remains unscheduled."""
    svc = ReschedulingSuggestionService()
    task = _task("Task A", deadline=None, duration=60)
    result = svc.suggest_rescheduling([task])
    assert len(result.suggestions) == 0
    assert len(result.unscheduled_tasks) == 1


def test_capacity_rejected_task_remains_unscheduled():
    """Task rejected due to capacity remains unscheduled."""
    svc = ReschedulingSuggestionService(budget_minutes=100)
    tasks = [
        _task("Task A", deadline=_dt(18), duration=80),
        _task("Task B", deadline=_dt(18), duration=60),
    ]
    result = svc.suggest_rescheduling(tasks, budget_minutes=100)
    # 80+60=140 > 100, so at least one unscheduled
    assert len(result.unscheduled_tasks) > 0


# ===========================================================================
# Reason tests
# ===========================================================================


def test_reason_is_deterministic():
    """Reason string is deterministic."""
    svc = ReschedulingSuggestionService()
    task = _task("Task A", deadline=_dt(18), duration=60)
    result1 = svc.suggest_rescheduling([task])
    result2 = svc.suggest_rescheduling([task])
    assert result1.suggestions[0].reason == result2.suggestions[0].reason
    assert result1.suggestions[0].reason == (
        "Fits within the task feasibility window and remaining daily capacity."
    )


def test_same_input_produces_identical_reason():
    """Same input produces identical reason for all suggestions."""
    svc = ReschedulingSuggestionService()
    tasks = [
        _task("Task A", deadline=_dt(10), duration=60),
        _task("Task B", deadline=_dt(14), duration=60),
    ]
    result = svc.suggest_rescheduling(tasks)
    reasons = [s.reason for s in result.suggestions]
    assert all(r == reasons[0] for r in reasons)


# ===========================================================================
# Ordering tests
# ===========================================================================


def test_suggestions_have_deterministic_ordering():
    """Suggestions are deterministically ordered by deadline, priority, name."""
    svc = ReschedulingSuggestionService()
    tasks = [
        _task("Task C", deadline=_dt(18), duration=60, priority=TaskPriority.LOW),
        _task("Task A", deadline=_dt(10), duration=60, priority=TaskPriority.HIGH),
        _task("Task B", deadline=_dt(14), duration=60, priority=TaskPriority.MEDIUM),
    ]
    result1 = svc.suggest_rescheduling(tasks)
    result2 = svc.suggest_rescheduling(tasks)
    assert [s.task_name for s in result1.suggestions] == [
        s.task_name for s in result2.suggestions
    ]
    # Verify correct order
    names = [s.task_name for s in result1.suggestions]
    assert names == ["Task A", "Task B", "Task C"]


def test_unscheduled_tasks_have_deterministic_ordering():
    """Unscheduled tasks are deterministically ordered."""
    svc = ReschedulingSuggestionService()
    tasks = [
        _task("Task C", deadline=None, duration=60, priority=TaskPriority.LOW),
        _task("Task A", deadline=_dt(10), duration=None, priority=TaskPriority.HIGH),
        _task("Task B", deadline=None, duration=None, priority=TaskPriority.MEDIUM),
    ]
    result1 = svc.suggest_rescheduling(tasks)
    result2 = svc.suggest_rescheduling(tasks)
    assert [t.task_name for t in result1.unscheduled_tasks] == [
        t.task_name for t in result2.unscheduled_tasks
    ]


def test_overloaded_days_are_ascending():
    """Overloaded days are sorted in ascending date order."""
    svc = ReschedulingSuggestionService(budget_minutes=60)
    tasks = [
        _task("Task A", deadline=_dt(18, day=27), duration=100),
        _task("Task B", deadline=_dt(18, day=25), duration=100),
        _task("Task C", deadline=_dt(18, day=26), duration=100),
    ]
    result = svc.suggest_rescheduling(tasks, budget_minutes=60)
    if len(result.overloaded_days) > 1:
        for i in range(len(result.overloaded_days) - 1):
            assert result.overloaded_days[i] < result.overloaded_days[i + 1]


def test_duplicate_task_ids_do_not_produce_duplicate_suggestions():
    """Duplicate task IDs do not produce duplicate suggestions."""
    svc = ReschedulingSuggestionService()
    task_id = uuid4()
    tasks = [
        _task("Task A", deadline=_dt(18), duration=60, task_id=task_id),
        _task("Task A duplicate", deadline=_dt(18), duration=60, task_id=task_id),
    ]
    result = svc.suggest_rescheduling(tasks)
    assert len(result.suggestions) == 1


# ===========================================================================
# Status tests
# ===========================================================================


def test_completed_tasks_not_suggested():
    """Completed tasks are not suggested."""
    svc = ReschedulingSuggestionService()
    tasks = [
        _task("Task A", deadline=_dt(18), duration=60, status=TaskStatus.COMPLETED),
        _task("Task B", deadline=_dt(18), duration=60, status=TaskStatus.TODO),
    ]
    result = svc.suggest_rescheduling(tasks)
    names = [s.task_name for s in result.suggestions]
    assert "Task A" not in names
    assert "Task B" in names


def test_cancelled_tasks_not_suggested():
    """Cancelled tasks are not suggested."""
    svc = ReschedulingSuggestionService()
    tasks = [
        _task("Task A", deadline=_dt(18), duration=60, status=TaskStatus.CANCELLED),
        _task("Task B", deadline=_dt(18), duration=60, status=TaskStatus.TODO),
    ]
    result = svc.suggest_rescheduling(tasks)
    names = [s.task_name for s in result.suggestions]
    assert "Task A" not in names
    assert "Task B" in names


# ===========================================================================
# Dependency behavior tests
# ===========================================================================


def test_candidate_generation_service_called_with_workload():
    """Candidate generation service is called with the expected workload."""
    mock_candidate_service = Mock(spec=ReschedulingCandidateService)
    mock_candidate_service.generate_candidates.return_value = ReschedulingResult()

    svc = ReschedulingSuggestionService(candidate_service=mock_candidate_service)
    tasks = [
        _task("Task A", deadline=_dt(18), duration=60),
        _task("Task B", deadline=_dt(14), duration=60),
    ]
    result = svc.suggest_rescheduling(tasks, budget_minutes=480)

    mock_candidate_service.generate_candidates.assert_called_once_with(
        tasks, budget_minutes=480
    )
    assert result.suggestions == []


def test_candidate_generation_result_transformed():
    """Candidate generation result is returned as ReschedulingResult."""
    mock_candidate_service = Mock(spec=ReschedulingCandidateService)
    expected_result = ReschedulingResult(
        suggestions=[
            ReschedulingSuggestion(
                task_id=uuid4(),
                task_name="Mocked",
                duration_minutes=60,
                current_deadline=_dt(18),
                candidate_slot=CandidateSlot(
                    start=_dt(17), end=_dt(18), availableMinutes=60
                ),
                reason="Test reason",
            )
        ],
        overloaded_days=[],
        unscheduled_tasks=[],
    )
    mock_candidate_service.generate_candidates.return_value = expected_result

    svc = ReschedulingSuggestionService(candidate_service=mock_candidate_service)
    result = svc.suggest_rescheduling([_task("Task A", deadline=_dt(18), duration=60)])

    assert result is expected_result
    assert len(result.suggestions) == 1
    assert result.suggestions[0].task_name == "Mocked"


def test_custom_budget_propagated():
    """Custom budget_minutes is propagated to candidate service."""
    mock_candidate_service = Mock(spec=ReschedulingCandidateService)
    mock_candidate_service.generate_candidates.return_value = ReschedulingResult()

    svc = ReschedulingSuggestionService(candidate_service=mock_candidate_service)
    tasks = [_task("Task A", deadline=_dt(18), duration=60)]
    svc.suggest_rescheduling(tasks, budget_minutes=300)

    mock_candidate_service.generate_candidates.assert_called_once_with(
        tasks, budget_minutes=300
    )


# ===========================================================================
# Safety / Scope tests
# ===========================================================================


def test_no_task_mutation():
    """Tasks are not mutated by suggestion generation."""
    svc = ReschedulingSuggestionService()
    tasks = [
        _task("Task A", deadline=_dt(18), duration=60),
        _task("Task B", deadline=_dt(14), duration=60),
    ]
    original_deadlines = [t.deadline for t in tasks]
    original_durations = [t.duration_minutes for t in tasks]
    svc.suggest_rescheduling(tasks)
    for t, orig_deadline, orig_duration in zip(
        tasks, original_deadlines, original_durations
    ):
        assert t.deadline == orig_deadline
        assert t.duration_minutes == orig_duration


def test_service_rejects_invalid_budget():
    """Service rejects invalid budget values."""
    for invalid in (0, -10, 1441, 9999):
        with pytest.raises(ValueError, match="budget_minutes"):
            ReschedulingSuggestionService(budget_minutes=invalid)


def test_service_accepts_valid_budget_boundaries():
    """Service accepts valid budget boundary values."""
    for valid in (1, 1440, 480, 120):
        svc = ReschedulingSuggestionService(budget_minutes=valid)
        assert svc._default_budget == valid


def test_service_is_stateless_across_calls():
    """Service can be reused without state pollution."""
    svc = ReschedulingSuggestionService()
    tasks1 = [_task("Task A", deadline=_dt(10), duration=60)]
    tasks2 = [_task("Task B", deadline=_dt(14), duration=90)]
    result1 = svc.suggest_rescheduling(tasks1)
    result2 = svc.suggest_rescheduling(tasks2)
    assert len(result1.suggestions) == 1
    assert result1.suggestions[0].task_name == "Task A"
    assert len(result2.suggestions) == 1
    assert result2.suggestions[0].task_name == "Task B"
