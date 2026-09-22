"""Tests for PDMA-107: Scheduling Pressure structured output."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from personal_deadline_management_agent.schemas.workload import (
    FeasibilityWindow,
    SchedulingPressure,
    WorkloadAnalysisResult,
)


class TestSchedulingPressureSchema:
    """Test SchedulingPressure schema structure and serialization."""

    def test_scheduling_pressure_construction(self):
        """Should construct SchedulingPressure with all required fields."""
        task_a_id = uuid4()
        task_b_id = uuid4()
        overlap_start = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)
        overlap_end = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)

        pressure = SchedulingPressure(
            task_a_id=task_a_id,
            task_a_name="Task A",
            task_b_id=task_b_id,
            task_b_name="Task B",
            overlap_start=overlap_start,
            overlap_end=overlap_end,
        )

        assert pressure.task_a_id == task_a_id
        assert pressure.task_a_name == "Task A"
        assert pressure.task_b_id == task_b_id
        assert pressure.task_b_name == "Task B"
        assert pressure.overlap_start == overlap_start
        assert pressure.overlap_end == overlap_end

    def test_scheduling_pressure_serialization(self):
        """Should serialize with camelCase aliases."""
        task_a_id = uuid4()
        task_b_id = uuid4()
        overlap_start = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)
        overlap_end = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)

        pressure = SchedulingPressure(
            task_a_id=task_a_id,
            task_a_name="Task A",
            task_b_id=task_b_id,
            task_b_name="Task B",
            overlap_start=overlap_start,
            overlap_end=overlap_end,
        )

        data = pressure.model_dump(by_alias=True)

        assert "taskAId" in data
        assert "taskAName" in data
        assert "taskBId" in data
        assert "taskBName" in data
        assert "overlapStart" in data
        assert "overlapEnd" in data

    def test_workload_analysis_result_includes_scheduling_pressure(self):
        """WorkloadAnalysisResult should include scheduling_pressure field."""
        result = WorkloadAnalysisResult()

        assert hasattr(result, "scheduling_pressure")
        assert result.scheduling_pressure == []

    def test_workload_analysis_result_with_scheduling_pressure(self):
        """WorkloadAnalysisResult should serialize scheduling_pressure correctly."""
        task_a_id = uuid4()
        task_b_id = uuid4()
        overlap_start = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)
        overlap_end = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)

        pressure = SchedulingPressure(
            task_a_id=task_a_id,
            task_a_name="Task A",
            task_b_id=task_b_id,
            task_b_name="Task B",
            overlap_start=overlap_start,
            overlap_end=overlap_end,
        )

        result = WorkloadAnalysisResult(scheduling_pressure=[pressure])

        data = result.model_dump(by_alias=True)
        assert "schedulingPressure" in data
        assert len(data["schedulingPressure"]) == 1
        assert data["schedulingPressure"][0]["taskAName"] == "Task A"
