"""Focused tests for PDMA-109 Candidate Slot / Constraint Engine."""

from datetime import datetime, timezone, timedelta
from uuid import uuid4

from personal_deadline_management_agent.models import TaskPriority, TaskStatus
from personal_deadline_management_agent.schemas.workload import (
    CandidateSlot,
    TaskSummary,
)
from personal_deadline_management_agent.services.rescheduling_constraint_service import (
    ReschedulingConstraintService,
)


_DEFAULT_DEADLINE = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)


def _task(
    name="Task",
    deadline=_DEFAULT_DEADLINE,
    duration=None,
    status=TaskStatus.TODO,
    priority=TaskPriority.MEDIUM,
):
    return TaskSummary(
        id=uuid4(),
        task_name=name,
        priority=priority,
        status=status,
        deadline=deadline,
        duration_minutes=duration,
    )


# 1. Valid candidate for task with known duration/deadline
def test_valid_candidate_with_known_duration_deadline():
    svc = ReschedulingConstraintService()
    deadline = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)
    task = _task(name="Task A", deadline=deadline, duration=120)
    slots = svc.find_candidate_slots(task)
    assert len(slots) == 1
    assert slots[0].start == deadline - timedelta(minutes=120)
    assert slots[0].end == deadline
    assert slots[0].available_minutes == 120


# 2. Candidate that would exceed deadline is rejected (validate_candidate)
def test_candidate_exceeding_deadline_rejected():
    svc = ReschedulingConstraintService()
    deadline = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)
    task = _task(name="Task A", deadline=deadline, duration=60)
    bad_slot = CandidateSlot(
        start=datetime(2026, 9, 25, 17, 30, tzinfo=timezone.utc),
        end=datetime(2026, 9, 25, 19, 0, tzinfo=timezone.utc),  # end > deadline
        available_minutes=90,
    )
    assert svc.validate_candidate(task, bad_slot) is False


# 3. Candidate shorter than task duration is rejected
def test_candidate_shorter_than_task_duration_rejected():
    svc = ReschedulingConstraintService()
    deadline = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)
    task = _task(name="Task A", deadline=deadline, duration=120)
    bad_slot = CandidateSlot(
        start=datetime(2026, 9, 25, 17, 0, tzinfo=timezone.utc),
        end=datetime(2026, 9, 25, 17, 30, tzinfo=timezone.utc),  # 30 min < 120
        available_minutes=30,
    )
    assert svc.validate_candidate(task, bad_slot) is False


# 4. Missing duration produces no fabricated candidate
def test_missing_duration_no_candidate():
    svc = ReschedulingConstraintService()
    deadline = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)
    task = _task(name="Task A", deadline=deadline, duration=None)
    assert svc.find_candidate_slots(task) == []


# 5. Missing deadline produces no fabricated candidate
def test_missing_deadline_no_candidate():
    svc = ReschedulingConstraintService()
    task = _task(name="Task A", deadline=None, duration=60)
    assert svc.find_candidate_slots(task) == []


# 6. Candidate outside feasibility window is rejected
def test_candidate_outside_feasibility_window_rejected():
    svc = ReschedulingConstraintService()
    deadline = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)
    task = _task(name="Task A", deadline=deadline, duration=60)
    # feasibility window is [17:00, 18:00]; slot starts at 16:00 -> outside
    bad_slot = CandidateSlot(
        start=datetime(2026, 9, 25, 16, 0, tzinfo=timezone.utc),
        end=datetime(2026, 9, 25, 17, 0, tzinfo=timezone.utc),
        available_minutes=60,
    )
    assert svc.validate_candidate(task, bad_slot) is False


# 7. Daily budget/capacity constraint is respected
def test_daily_budget_capacity_respected():
    svc = ReschedulingConstraintService(budget_minutes=120)
    deadline = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)
    # Existing workload: one task 100 min on same day
    existing = _task(name="Existing", deadline=datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc), duration=100)
    task = _task(name="Task A", deadline=deadline, duration=60)
    # Total would be 160 > budget 120 -> no candidate
    slots = svc.find_candidate_slots(task, all_tasks=[existing, task])
    assert slots == []


def test_daily_budget_capacity_allows_fit():
    svc = ReschedulingConstraintService(budget_minutes=480)
    deadline = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)
    existing = _task(name="Existing", deadline=datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc), duration=100)
    task = _task(name="Task A", deadline=deadline, duration=60)
    slots = svc.find_candidate_slots(task, all_tasks=[existing, task])
    assert len(slots) == 1


# 8. Boundary equality
def test_slot_end_equals_deadline_is_valid():
    svc = ReschedulingConstraintService()
    deadline = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)
    task = _task(name="Task A", deadline=deadline, duration=60)
    slot = CandidateSlot(
        start=deadline - timedelta(minutes=60),
        end=deadline,
        available_minutes=60,
    )
    assert svc.validate_candidate(task, slot) is True


def test_slot_start_equals_latest_start_is_valid():
    svc = ReschedulingConstraintService()
    deadline = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)
    task = _task(name="Task A", deadline=deadline, duration=60)
    # latest_start = 17:00
    slot = CandidateSlot(
        start=datetime(2026, 9, 25, 17, 0, tzinfo=timezone.utc),
        end=datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc),
        available_minutes=60,
    )
    assert svc.validate_candidate(task, slot) is True
    # One minute before latest_start -> invalid
    bad_slot = CandidateSlot(
        start=datetime(2026, 9, 25, 16, 59, tzinfo=timezone.utc),
        end=datetime(2026, 9, 25, 17, 59, tzinfo=timezone.utc),
        available_minutes=60,
    )
    assert svc.validate_candidate(task, bad_slot) is False


def test_daily_budget_equality_allows_slot():
    svc = ReschedulingConstraintService(budget_minutes=160)
    deadline = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)
    existing = _task(name="Existing", deadline=datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc), duration=100)
    task = _task(name="Task A", deadline=deadline, duration=60)
    # 100+60 == 160 budget -> allowed (excess only when planned > budget)
    slots = svc.find_candidate_slots(task, all_tasks=[existing, task])
    assert len(slots) == 1


# 9. Deterministic ordering (find across multiple tasks)
def test_deterministic_ordering_across_tasks():
    svc = ReschedulingConstraintService()
    deadline = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)
    tasks = [
        _task(name="Zebra", deadline=deadline, duration=60),
        _task(name="Alpha", deadline=deadline, duration=60),
        _task(name="Beta", deadline=deadline, duration=60),
    ]
    all_slots = []
    for t in sorted(tasks, key=lambda x: x.task_name):
        all_slots.extend(svc.find_candidate_slots(t))
    assert all_slots[0].start <= all_slots[1].start or all_slots[0].start == all_slots[1].start


# 10. Same input produces same result
def test_idempotent_same_input_same_output():
    svc = ReschedulingConstraintService()
    deadline = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)
    task = _task(name="Task A", deadline=deadline, duration=60)
    a = svc.find_candidate_slots(task)
    b = svc.find_candidate_slots(task)
    assert a == b
    assert a[0].model_dump() == b[0].model_dump()


# 11. Existing Phase 10 workload behavior unchanged
def test_phase10_workload_unchanged():
    from personal_deadline_management_agent.services.workload_analysis_service import WorkloadAnalysisService
    deadline = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)
    t1 = _task(name="A", deadline=deadline, duration=60, priority=TaskPriority.HIGH)
    t2 = _task(name="B", deadline=deadline, duration=60, priority=TaskPriority.HIGH)
    svc = WorkloadAnalysisService()
    result = svc.analyze([t1, t2])
    assert len(result.deadline_collisions) == 1
    assert len(result.feasibility_windows) == 2
    assert result.total_planned_minutes == 120


# Additional: validate_candidate with None duration/deadline
def test_validate_candidate_with_missing_duration_rejected():
    svc = ReschedulingConstraintService()
    task = _task(name="Task", deadline=datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc), duration=None)
    slot = CandidateSlot(
        start=datetime(2026, 9, 25, 17, 0, tzinfo=timezone.utc),
        end=datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc),
        available_minutes=60,
    )
    assert svc.validate_candidate(task, slot) is False


def test_validate_candidate_with_missing_deadline_rejected():
    svc = ReschedulingConstraintService()
    task = _task(name="Task", deadline=None, duration=60)
    slot = CandidateSlot(
        start=datetime(2026, 9, 25, 17, 0, tzinfo=timezone.utc),
        end=datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc),
        available_minutes=60,
    )
    assert svc.validate_candidate(task, slot) is False


# Completed / cancelled tasks should not block capacity
def test_completed_tasks_do_not_block_capacity():
    svc = ReschedulingConstraintService(budget_minutes=120)
    deadline = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)
    existing = _task(name="Done", deadline=datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc), duration=100, status=TaskStatus.COMPLETED)
    task = _task(name="Task A", deadline=deadline, duration=60)
    slots = svc.find_candidate_slots(task, all_tasks=[existing, task])
    assert len(slots) == 1
