"""PDMA-107: Structured Scheduling Pressure output.

Tests that the WorkloadAnalysisService now exposes feasibility-window
overlap as a structured field in WorkloadAnalysisResult rather than
only in explanation text.

Scope: smallest clearly justified increment given no explicit PDMA-107
spec exists in the repository. Phase 10 already implements:
  - Duration (PDMA-100)
  - Feasibility windows / total planned / daily pressure / overload (PDMA-105)
  - Configurable budget (PDMA-106)
The remaining gap is that scheduling pressure was calculated but rendered
only as prose. PDMA-107 surfaces it as a typed field and feeds it into
AgentResponseGenerator.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest

from personal_deadline_management_agent.models import TaskPriority, TaskStatus
from personal_deadline_management_agent.schemas.workload import (
    SchedulingPressure,
    TaskSummary,
)
from personal_deadline_management_agent.services.workload_analysis_service import (
    WorkloadAnalysisService,
)


# --- Helpers -----------------------------------------------------------------


def _summary(
    task_name: str,
    priority: TaskPriority = TaskPriority.MEDIUM,
    status: TaskStatus = TaskStatus.TODO,
    deadline: datetime | None = None,
    duration_minutes: int | None = None,
) -> TaskSummary:
    return TaskSummary(
        id=uuid4(),
        task_name=task_name,
        priority=priority,
        status=status,
        deadline=deadline,
        duration_minutes=duration_minutes,
    )


# --- Schema-level ------------------------------------------------------------


def test_scheduling_pressure_schema_aliases():
    """SchedulingPressure serializes with camelCase aliases."""
    sp = SchedulingPressure(
        task_a_id=uuid4(),
        task_a_name="A",
        task_b_id=uuid4(),
        task_b_name="B",
        overlap_start=datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc),
        overlap_end=datetime(2026, 9, 25, 14, 0, tzinfo=timezone.utc),
    )
    data = sp.model_dump(by_alias=True)
    assert "taskAId" in data
    assert "taskAName" in data
    assert "taskBId" in data
    assert "taskBName" in data
    assert "overlapStart" in data
    assert "overlapEnd" in data


# --- Service: structured scheduling_pressure field ---------------------------


def test_scheduling_pressure_normal_case():
    """Two overlapping feasibility windows produce one SchedulingPressure entry."""
    svc = WorkloadAnalysisService()
    # Task A: deadline 2026-09-25 18:00, duration 240 -> window [14:00, 18:00]
    # Task B: deadline 2026-09-25 15:00, duration 60  -> window [14:00, 15:00]
    # Overlap window: [14:00, 15:00]
    deadline_a = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)
    deadline_b = datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc)
    tasks = [
        _summary("Alpha", priority=TaskPriority.HIGH, status=TaskStatus.TODO, deadline=deadline_a, duration_minutes=240),
        _summary("Beta", priority=TaskPriority.HIGH, status=TaskStatus.TODO, deadline=deadline_b, duration_minutes=60),
    ]
    result = svc.analyze(tasks)
    assert len(result.scheduling_pressure) == 1
    sp = result.scheduling_pressure[0]
    assert sp.overlap_start == datetime(2026, 9, 25, 14, 0, tzinfo=timezone.utc)
    assert sp.overlap_end == datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc)
    # Task ordering is deterministic: Alpha < Beta
    assert sp.task_a_name == "Alpha"
    assert sp.task_b_name == "Beta"


def test_scheduling_pressure_boundary_equality_not_overlap():
    """Boundary-touching windows are NOT scheduling pressure."""
    svc = WorkloadAnalysisService()
    # Touching: window A [14:00, 16:00], window B [16:00, 18:00]
    deadline_a = datetime(2026, 9, 25, 16, 0, tzinfo=timezone.utc)
    deadline_b = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)
    tasks = [
        _summary("Alpha", deadline=deadline_a, duration_minutes=120),
        _summary("Beta", deadline=deadline_b, duration_minutes=120),
    ]
    result = svc.analyze(tasks)
    assert result.scheduling_pressure == []


def test_scheduling_pressure_unknown_duration_excluded():
    """Task without duration cannot participate in scheduling pressure."""
    svc = WorkloadAnalysisService()
    deadline = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)
    tasks = [
        _summary("Alpha", deadline=deadline, duration_minutes=240),
        _summary("Beta", deadline=deadline, duration_minutes=None),
    ]
    result = svc.analyze(tasks)
    assert result.scheduling_pressure == []


def test_scheduling_pressure_missing_deadline_excluded():
    """Task without deadline cannot produce a feasibility window; no pressure entry."""
    svc = WorkloadAnalysisService()
    tasks = [
        _summary("Alpha", deadline=None, duration_minutes=60),
        _summary("Beta", deadline=datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc), duration_minutes=60),
    ]
    result = svc.analyze(tasks)
    assert result.scheduling_pressure == []


def test_scheduling_pressure_non_overlapping():
    """Non-overlapping windows produce no entries."""
    svc = WorkloadAnalysisService()
    tasks = [
        _summary("Alpha", deadline=datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc), duration_minutes=60),
        _summary("Beta", deadline=datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc), duration_minutes=60),
    ]
    result = svc.analyze(tasks)
    assert result.scheduling_pressure == []


def test_scheduling_pressure_deterministic_ordering():
    """Multiple overlaps are ordered deterministically by task names."""
    svc = WorkloadAnalysisService()
    deadline = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)
    tasks = [
        _summary("Gamma", deadline=deadline, duration_minutes=240),
        _summary("Alpha", deadline=deadline, duration_minutes=240),
        _summary("Beta", deadline=deadline, duration_minutes=240),
    ]
    result = svc.analyze(tasks)
    assert len(result.scheduling_pressure) == 3
    # Ordered by (task_a_name, task_b_name)
    assert result.scheduling_pressure[0].task_a_name == "Alpha"
    assert result.scheduling_pressure[0].task_b_name == "Beta"
    assert result.scheduling_pressure[1].task_a_name == "Alpha"
    assert result.scheduling_pressure[1].task_b_name == "Gamma"
    assert result.scheduling_pressure[2].task_a_name == "Beta"
    assert result.scheduling_pressure[2].task_b_name == "Gamma"


def test_scheduling_pressure_empty_workload():
    """Empty workload yields no scheduling pressure."""
    svc = WorkloadAnalysisService()
    result = svc.analyze([])
    assert result.scheduling_pressure == []


def test_scheduling_pressure_respects_configured_budget():
    """Scheduling pressure does not depend on budget; overload does."""
    deadline = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    tasks = [
        _summary("Alpha", deadline=deadline, duration_minutes=100),
        _summary("Beta", deadline=deadline, duration_minutes=100),
    ]
    svc_small = WorkloadAnalysisService(budget_minutes=50)
    svc_large = WorkloadAnalysisService(budget_minutes=1440)
    # Scheduling pressure should be identical regardless of budget
    assert len(svc_small.analyze(tasks).scheduling_pressure) == len(svc_large.analyze(tasks).scheduling_pressure)


def test_scheduling_pressure_timezone_aware():
    """UTC+ timezone deadlines produce correct scheduling pressure."""
    svc = WorkloadAnalysisService()
    # 18:00 UTC+7 with offset, but UTC equivalent deadlines
    deadline_a = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)
    deadline_b = datetime(2026, 9, 25, 19, 0, tzinfo=timezone.utc)
    tasks = [
        _summary("Alpha", deadline=deadline_a, duration_minutes=120),
        _summary("Beta", deadline=deadline_b, duration_minutes=120),
    ]
    result = svc.analyze(tasks)
    assert len(result.scheduling_pressure) == 1
    assert result.scheduling_pressure[0].overlap_start.tzinfo is not None


def test_scheduling_pressure_appears_in_serialization():
    """SchedulingPressure appears under camelCase key in WorkloadAnalysisResult."""
    svc = WorkloadAnalysisService()
    deadline = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)
    tasks = [
        _summary("Alpha", deadline=deadline, duration_minutes=240),
        _summary("Beta", deadline=deadline, duration_minutes=240),
    ]
    result = svc.analyze(tasks)
    data = result.model_dump(by_alias=True)
    assert "schedulingPressure" in data
    assert len(data["schedulingPressure"]) == 1


# --- Regression guards -------------------------------------------------------


def test_phase7_behavior_unchanged_with_scheduling_pressure():
    """Phase 7 collision/busy-day/order remain unchanged when scheduling pressure exists."""
    svc = WorkloadAnalysisService()
    deadline = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)
    tasks = [
        _summary("A", priority=TaskPriority.HIGH, status=TaskStatus.TODO, deadline=deadline, duration_minutes=60),
        _summary("B", priority=TaskPriority.HIGH, status=TaskStatus.TODO, deadline=deadline, duration_minutes=60),
    ]
    result = svc.analyze(tasks)
    # Collision still detected
    assert len(result.deadline_collisions) == 1
    # Recommended order still deterministic
    assert result.recommended_order[0].task_name == "A"
    assert result.recommended_order[1].task_name == "B"
    # Scheduling pressure is now also present (overlapping windows)
    assert len(result.scheduling_pressure) == 1


def test_pdma106_budget_still_flows_to_daily_pressure():
    """PDMA-106 budget injection still governs daily pressure/overload."""
    deadline = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
    tasks = [_summary("Alpha", deadline=deadline, duration_minutes=200)]
    svc = WorkloadAnalysisService(budget_minutes=100)
    result = svc.analyze(tasks)
    assert result.daily_pressure[0].budget_minutes == 100
    assert result.overload_warnings[0].excess_minutes == 100
