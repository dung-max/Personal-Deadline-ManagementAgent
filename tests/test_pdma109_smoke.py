"""
PDMA-109 smoke test — runs in-process with pytest,
no shell required.  Designed to verify that the new
ReschedulingConstraintService works correctly.

Run via:  python -m pytest tests/test_pdma109_smoke.py -v
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from personal_deadline_management_agent.models import TaskPriority, TaskStatus
from personal_deadline_management_agent.schemas.workload import (
    CandidateSlot,
    TaskSummary,
    WorkloadAnalysisResult,
)
from personal_deadline_management_agent.services.rescheduling_constraint_service import (
    ReschedulingConstraintService,
)
from personal_deadline_management_agent.services.workload_analysis_service import (
    WorkloadAnalysisService,
)


# ── Helpers ────────────────────────────────────────────────────────────────


def _ts(name, deadline, duration, status=TaskStatus.TODO, priority=TaskPriority.MEDIUM):
    return TaskSummary(
        id=uuid4(),
        task_name=name,
        priority=priority,
        status=status,
        deadline=deadline,
        duration_minutes=duration,
    )


D = lambda h=0, d=0: datetime(2026, 9, 25, h, 0, tzinfo=timezone.utc) + timedelta(days=d)


# ── Tests ──────────────────────────────────────────────────────────────────


class TestCandidateSlot:
    """Validate schema-level constraints on CandidateSlot."""

    def test_valid_slot(self):
        slot = CandidateSlot(start=D(9), end=D(11), available_minutes=120)
        assert slot.available_minutes == 120
        assert slot.end > slot.start

    def test_end_before_start_rejects(self):
        with pytest.raises(ValueError, match="end must be after start"):
            CandidateSlot(start=D(11), end=D(9), available_minutes=120)

    def test_end_equals_start_rejects(self):
        with pytest.raises(ValueError, match="end must be after start"):
            CandidateSlot(start=D(9), end=D(9), available_minutes=1)

    def test_zero_available_minutes_rejects(self):
        with pytest.raises(Exception):
            CandidateSlot(start=D(9), end=D(11), available_minutes=0)

    def test_negative_available_minutes_rejects(self):
        with pytest.raises(Exception):
            CandidateSlot(start=D(9), end=D(11), available_minutes=-10)


class TestReschedulingConstraintService:
    """Verify the constraint engine produces valid CandidateSlots."""

    def test_valid_candidate_slot(self):
        t = _ts("A", deadline=D(18), duration=120)
        svc = ReschedulingConstraintService()
        slots = svc.find_candidate_slots(t)
        assert len(slots) == 1
        assert slots[0].start == D(16)
        assert slots[0].end == D(18)

    def test_candidate_exceeding_deadline_rejected(self):
        t = _ts("A", deadline=D(18), duration=60)
        # Create a slot that exceeds the deadline
        start_time = datetime(2026, 9, 25, 17, 30, tzinfo=timezone.utc)
        end_time = datetime(2026, 9, 25, 19, 0, tzinfo=timezone.utc)
        slot = CandidateSlot(start=start_time, end=end_time, available_minutes=90)
        svc = ReschedulingConstraintService()
        assert svc.validate_candidate(t, slot) is False

    def test_candidate_shorter_than_task_rejected(self):
        t = _ts("A", deadline=D(18), duration=120)
        slot = CandidateSlot(start=D(17, 0), end=D(17, 30), available_minutes=30)
        svc = ReschedulingConstraintService()
        assert svc.validate_candidate(t, slot) is False

    def test_missing_duration_no_candidate(self):
        t = _ts("A", deadline=D(18), duration=None)
        svc = ReschedulingConstraintService()
        assert svc.find_candidate_slots(t) == []

    def test_missing_deadline_no_candidate(self):
        t = _ts("A", deadline=None, duration=60)
        svc = ReschedulingConstraintService()
        assert svc.find_candidate_slots(t) == []

    def test_boundary_start_equals_latest_start(self):
        t = _ts("A", deadline=D(18), duration=60)
        slot = CandidateSlot(start=D(17), end=D(18), available_minutes=60)
        svc = ReschedulingConstraintService()
        assert svc.validate_candidate(t, slot) is True

    def test_boundary_end_equals_deadline(self):
        t = _ts("A", deadline=D(18), duration=60)
        slot = CandidateSlot(start=D(17), end=D(18), available_minutes=60)
        svc = ReschedulingConstraintService()
        assert svc.validate_candidate(t, slot) is True

    def test_slot_outside_feasibility_rejected(self):
        t = _ts("A", deadline=D(18), duration=60)
        slot = CandidateSlot(start=D(16), end=D(17), available_minutes=60)
        svc = ReschedulingConstraintService()
        assert svc.validate_candidate(t, slot) is False

    def test_daily_budget_respected(self):
        svc = ReschedulingConstraintService(budget_minutes=120)
        existing = _ts("Existing", deadline=D(10), duration=100)
        t = _ts("Task A", deadline=D(18), duration=60)
        slots = svc.find_candidate_slots(t, all_tasks=[existing, t])
        assert slots == []

    def test_daily_budget_fit(self):
        svc = ReschedulingConstraintService(budget_minutes=480)
        existing = _ts("Existing", deadline=D(10), duration=100)
        t = _ts("Task A", deadline=D(18), duration=60)
        slots = svc.find_candidate_slots(t, all_tasks=[existing, t])
        assert len(slots) == 1

    def test_daily_budget_equality_boundary(self):
        svc = ReschedulingConstraintService(budget_minutes=160)
        existing = _ts("Existing", deadline=D(10), duration=100)
        t = _ts("Task A", deadline=D(18), duration=60)
        slots = svc.find_candidate_slots(t, all_tasks=[existing, t])
        assert len(slots) == 1

    def test_completed_tasks_excluded_from_capacity(self):
        svc = ReschedulingConstraintService(budget_minutes=120)
        done = _ts("Done", deadline=D(10), duration=100, status=TaskStatus.COMPLETED)
        t = _ts("Task A", deadline=D(18), duration=60)
        slots = svc.find_candidate_slots(t, all_tasks=[done, t])
        assert len(slots) == 1

    def test_idempotent(self):
        svc = ReschedulingConstraintService()
        t = _ts("A", deadline=D(18), duration=60)
        a = svc.find_candidate_slots(t)
        b = svc.find_candidate_slots(t)
        assert a[0].model_dump() == b[0].model_dump()

    def test_deterministic_ordering(self):
        svc = ReschedulingConstraintService()
        tasks = [_ts("Z", deadline=D(18), duration=60), _ts("A", deadline=D(18), duration=60)]
        slots_a = svc.find_candidate_slots(tasks[1])
        slots_z = svc.find_candidate_slots(tasks[0])
        assert slots_a[0].start == slots_z[0].start


class TestPhase10Regression:
    """Existing workload analysis results must not regress."""

    def test_phase7_collision_unchanged(self):
        t1 = _ts("A", deadline=D(18), duration=60, priority=TaskPriority.HIGH)
        t2 = _ts("B", deadline=D(18), duration=60, priority=TaskPriority.HIGH)
        result = WorkloadAnalysisService().analyze([t1, t2])
        assert len(result.deadline_collisions) == 1
        assert len(result.feasibility_windows) == 2
        assert result.total_planned_minutes == 120

    def test_daily_pressure_uses_default_budget(self):
        t = _ts("A", deadline=D(18), duration=60)
        result = WorkloadAnalysisService(budget_minutes=200).analyze([t])
        assert result.daily_pressure[0].budget_minutes == 200

    def test_overload_warning_triggered(self):
        t = _ts("A", deadline=D(18), duration=60)
        result = WorkloadAnalysisService(budget_minutes=30).analyze([t])
        assert len(result.overload_warnings) == 1
        assert result.overload_warnings[0].excess_minutes == 30
