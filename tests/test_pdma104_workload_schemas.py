"""PDMA-104: Workload schemas for Phase 10 time-aware analysis.

Schema-only tests. No workload calculation logic.
"""

from datetime import date, datetime, timezone
from uuid import uuid4

import pytest

from personal_deadline_management_agent.models import TaskPriority, TaskStatus
from personal_deadline_management_agent.schemas.workload import (
    DailyPressure,
    DeadlineCollision,
    FeasibilityWindow,
    OverloadWarning,
    TaskSummary,
    WorkloadAnalysisResult,
)


class TestTaskSummary:
    """TaskSummary schema tests."""

    def test_valid_construction_with_duration(self):
        """Valid TaskSummary with duration_minutes."""
        ts = TaskSummary(
            id=uuid4(),
            taskName="Test task",
            priority=TaskPriority.HIGH,
            status=TaskStatus.TODO,
            deadline=datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc),
            durationMinutes=120,
        )
        assert ts.duration_minutes == 120

    def test_valid_construction_without_duration(self):
        """Valid TaskSummary with duration_minutes=None."""
        ts = TaskSummary(
            id=uuid4(),
            taskName="Test task",
            priority=TaskPriority.HIGH,
            status=TaskStatus.TODO,
            deadline=datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc),
            durationMinutes=None,
        )
        assert ts.duration_minutes is None

    def test_serialization_with_aliases(self):
        """TaskSummary serializes with camelCase aliases."""
        task_id = uuid4()
        ts = TaskSummary(
            id=task_id,
            taskName="Test task",
            priority=TaskPriority.HIGH,
            status=TaskStatus.TODO,
            deadline=datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc),
            durationMinutes=90,
        )
        data = ts.model_dump(by_alias=True)
        assert "taskName" in data
        assert "durationMinutes" in data
        assert data["taskName"] == "Test task"
        assert data["durationMinutes"] == 90


class TestFeasibilityWindow:
    """FeasibilityWindow schema tests."""

    def test_valid_construction(self):
        """Valid FeasibilityWindow with all required fields."""
        fw = FeasibilityWindow(
            taskId=uuid4(),
            taskName="Important task",
            deadline=datetime(2026, 9, 25, 17, 0, tzinfo=timezone.utc),
            durationMinutes=120,
            latestStart=datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc),
        )
        assert fw.duration_minutes == 120
        assert fw.latest_start == datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc)

    def test_serialization_with_aliases(self):
        """FeasibilityWindow serializes with camelCase aliases."""
        task_id = uuid4()
        fw = FeasibilityWindow(
            taskId=task_id,
            taskName="Important task",
            deadline=datetime(2026, 9, 25, 17, 0, tzinfo=timezone.utc),
            durationMinutes=120,
            latestStart=datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc),
        )
        data = fw.model_dump(by_alias=True)
        assert "taskId" in data
        assert "taskName" in data
        assert "durationMinutes" in data
        assert "latestStart" in data
        assert data["taskId"] == task_id
        assert data["durationMinutes"] == 120

    def test_required_fields(self):
        """FeasibilityWindow requires all fields."""
        with pytest.raises(Exception):  # Pydantic ValidationError
            FeasibilityWindow(
                taskId=uuid4(),
                taskName="Missing fields",
            )


class TestDailyPressure:
    """DailyPressure schema tests."""

    def test_valid_construction(self):
        """Valid DailyPressure with all fields."""
        dp = DailyPressure(
            date=date(2026, 9, 25),
            plannedMinutes=480,
            budgetMinutes=360,
            utilizationPct=133.33,
            tasks=[],
        )
        assert dp.planned_minutes == 480
        assert dp.budget_minutes == 360
        assert dp.utilization_pct == 133.33

    def test_serialization_with_aliases(self):
        """DailyPressure serializes with camelCase aliases."""
        dp = DailyPressure(
            date=date(2026, 9, 25),
            plannedMinutes=480,
            budgetMinutes=360,
            utilizationPct=75.0,
            tasks=[],
        )
        data = dp.model_dump(by_alias=True)
        assert "plannedMinutes" in data
        assert "budgetMinutes" in data
        assert "utilizationPct" in data
        assert data["plannedMinutes"] == 480
        assert data["utilizationPct"] == 75.0

    def test_with_tasks(self):
        """DailyPressure can include TaskSummary list."""
        ts = TaskSummary(
            id=uuid4(),
            taskName="Test",
            priority=TaskPriority.HIGH,
            status=TaskStatus.TODO,
            deadline=datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc),
            durationMinutes=60,
        )
        dp = DailyPressure(
            date=date(2026, 9, 25),
            plannedMinutes=60,
            budgetMinutes=480,
            utilizationPct=12.5,
            tasks=[ts],
        )
        assert len(dp.tasks) == 1
        assert dp.tasks[0].task_name == "Test"


class TestOverloadWarning:
    """OverloadWarning schema tests."""

    def test_valid_construction(self):
        """Valid OverloadWarning with all fields."""
        ow = OverloadWarning(
            date=date(2026, 9, 25),
            plannedMinutes=600,
            budgetMinutes=480,
            excessMinutes=120,
            tasks=[],
        )
        assert ow.planned_minutes == 600
        assert ow.budget_minutes == 480
        assert ow.excess_minutes == 120

    def test_serialization_with_aliases(self):
        """OverloadWarning serializes with camelCase aliases."""
        ow = OverloadWarning(
            date=date(2026, 9, 25),
            plannedMinutes=600,
            budgetMinutes=480,
            excessMinutes=120,
            tasks=[],
        )
        data = ow.model_dump(by_alias=True)
        assert "plannedMinutes" in data
        assert "budgetMinutes" in data
        assert "excessMinutes" in data
        assert data["excessMinutes"] == 120


class TestWorkloadAnalysisResult:
    """WorkloadAnalysisResult schema tests including Phase 7 compatibility."""

    def test_phase7_fields_preserved(self):
        """Existing Phase 7 fields remain usable."""
        result = WorkloadAnalysisResult(
            totalTasks=5,
            deadlineCollisions=[],
            busyDays=[],
            recommendedOrder=[],
            explanation="Test explanation",
        )
        assert result.total_tasks == 5
        assert result.explanation == "Test explanation"
        assert result.deadline_collisions == []

    def test_new_phase10_fields_with_defaults(self):
        """New Phase 10 fields have safe defaults."""
        result = WorkloadAnalysisResult()
        assert result.total_planned_minutes == 0
        assert result.feasibility_windows == []
        assert result.daily_pressure == []
        assert result.overload_warnings == []

    def test_new_fields_with_values(self):
        """New Phase 10 fields accept values."""
        fw = FeasibilityWindow(
            taskId=uuid4(),
            taskName="Task A",
            deadline=datetime(2026, 9, 25, 17, 0, tzinfo=timezone.utc),
            durationMinutes=120,
            latestStart=datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc),
        )
        dp = DailyPressure(
            date=date(2026, 9, 25),
            plannedMinutes=240,
            budgetMinutes=480,
            utilizationPct=50.0,
            tasks=[],
        )
        ow = OverloadWarning(
            date=date(2026, 9, 26),
            plannedMinutes=600,
            budgetMinutes=480,
            excessMinutes=120,
            tasks=[],
        )
        result = WorkloadAnalysisResult(
            totalTasks=2,
            totalPlannedMinutes=240,
            feasibilityWindows=[fw],
            dailyPressure=[dp],
            overloadWarnings=[ow],
        )
        assert result.total_planned_minutes == 240
        assert len(result.feasibility_windows) == 1
        assert len(result.daily_pressure) == 1
        assert len(result.overload_warnings) == 1

    def test_serialization_with_aliases(self):
        """WorkloadAnalysisResult serializes all fields with camelCase aliases."""
        result = WorkloadAnalysisResult(
            totalTasks=3,
            totalPlannedMinutes=180,
        )
        data = result.model_dump(by_alias=True)
        assert "totalTasks" in data
        assert "totalPlannedMinutes" in data
        assert "feasibilityWindows" in data
        assert "dailyPressure" in data
        assert "overloadWarnings" in data
        assert "deadlineCollisions" in data
        assert "busyDays" in data
        assert "recommendedOrder" in data

    def test_backward_compatible_construction(self):
        """Phase 7 construction pattern still works."""
        ts = TaskSummary(
            id=uuid4(),
            taskName="Task",
            priority=TaskPriority.MEDIUM,
            status=TaskStatus.TODO,
            deadline=datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc),
        )
        result = WorkloadAnalysisResult(
            totalTasks=1,
            recommendedOrder=[ts],
            explanation="Phase 7 style",
        )
        assert result.total_tasks == 1
        assert len(result.recommended_order) == 1
        assert result.total_planned_minutes == 0
