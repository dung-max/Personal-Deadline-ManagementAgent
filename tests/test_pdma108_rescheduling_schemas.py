"""Tests for PDMA-108: Rescheduling Suggestion Schemas."""

from datetime import datetime, timezone, date
from uuid import uuid4

import pytest

from personal_deadline_management_agent.models import TaskPriority, TaskStatus
from personal_deadline_management_agent.schemas.workload import (
    CandidateSlot,
    ReschedulingSuggestion,
    ReschedulingResult,
    TaskSummary,
)


class TestCandidateSlot:
    """Test CandidateSlot schema structure and validation."""

    def test_valid_candidate_slot(self):
        """Should construct a valid CandidateSlot."""
        start = datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc)
        end = datetime(2026, 9, 23, 11, 0, tzinfo=timezone.utc)

        slot = CandidateSlot(
            start=start,
            end=end,
            available_minutes=120,
        )

        assert slot.start == start
        assert slot.end == end
        assert slot.available_minutes == 120
        assert slot.duration_minutes == 120

    def test_candidate_slot_end_before_start_raises_error(self):
        """Should raise ValueError when end <= start."""
        start = datetime(2026, 9, 23, 11, 0, tzinfo=timezone.utc)
        end = datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc)

        with pytest.raises(ValueError, match="end must be after start"):
            CandidateSlot(
                start=start,
                end=end,
                available_minutes=120,
            )

    def test_candidate_slot_end_equals_start_raises_error(self):
        """Should raise ValueError when end == start."""
        timestamp = datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc)

        with pytest.raises(ValueError, match="end must be after start"):
            CandidateSlot(
                start=timestamp,
                end=timestamp,
                available_minutes=1,
            )

    def test_candidate_slot_zero_available_minutes_raises_error(self):
        """Should raise ValidationError when available_minutes <= 0."""
        start = datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc)
        end = datetime(2026, 9, 23, 11, 0, tzinfo=timezone.utc)

        with pytest.raises(Exception):  # Pydantic ValidationError
            CandidateSlot(
                start=start,
                end=end,
                available_minutes=0,
            )

    def test_candidate_slot_negative_available_minutes_raises_error(self):
        """Should raise ValidationError when available_minutes < 0."""
        start = datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc)
        end = datetime(2026, 9, 23, 11, 0, tzinfo=timezone.utc)

        with pytest.raises(Exception):  # Pydantic ValidationError
            CandidateSlot(
                start=start,
                end=end,
                available_minutes=-10,
            )

    def test_candidate_slot_serialization(self):
        """Should serialize with camelCase aliases."""
        start = datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc)
        end = datetime(2026, 9, 23, 11, 0, tzinfo=timezone.utc)

        slot = CandidateSlot(
            start=start,
            end=end,
            available_minutes=120,
        )

        data = slot.model_dump(by_alias=True)

        assert "start" in data
        assert "end" in data
        assert "availableMinutes" in data
        assert data["availableMinutes"] == 120


class TestReschedulingSuggestion:
    """Test ReschedulingSuggestion schema structure and validation."""

    def test_valid_rescheduling_suggestion(self):
        """Should construct a valid ReschedulingSuggestion."""
        task_id = uuid4()
        current_deadline = datetime(2026, 9, 25, 17, 0, tzinfo=timezone.utc)
        start = datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc)
        end = datetime(2026, 9, 23, 11, 0, tzinfo=timezone.utc)

        slot = CandidateSlot(start=start, end=end, available_minutes=120)

        suggestion = ReschedulingSuggestion(
            task_id=task_id,
            task_name="Review PR",
            duration_minutes=120,
            current_deadline=current_deadline,
            candidate_slot=slot,
            reason="Fits available slot on Tuesday morning",
        )

        assert suggestion.task_id == task_id
        assert suggestion.task_name == "Review PR"
        assert suggestion.duration_minutes == 120
        assert suggestion.current_deadline == current_deadline
        assert suggestion.candidate_slot == slot
        assert suggestion.reason == "Fits available slot on Tuesday morning"

    def test_rescheduling_suggestion_zero_duration_raises_error(self):
        """Should raise ValidationError when duration_minutes <= 0."""
        task_id = uuid4()
        current_deadline = datetime(2026, 9, 25, 17, 0, tzinfo=timezone.utc)
        start = datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc)
        end = datetime(2026, 9, 23, 11, 0, tzinfo=timezone.utc)

        slot = CandidateSlot(start=start, end=end, available_minutes=120)

        with pytest.raises(Exception):  # Pydantic ValidationError
            ReschedulingSuggestion(
                task_id=task_id,
                task_name="Review PR",
                duration_minutes=0,
                current_deadline=current_deadline,
                candidate_slot=slot,
                reason="Test",
            )

    def test_rescheduling_suggestion_negative_duration_raises_error(self):
        """Should raise ValidationError when duration_minutes < 0."""
        task_id = uuid4()
        current_deadline = datetime(2026, 9, 25, 17, 0, tzinfo=timezone.utc)
        start = datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc)
        end = datetime(2026, 9, 23, 11, 0, tzinfo=timezone.utc)

        slot = CandidateSlot(start=start, end=end, available_minutes=120)

        with pytest.raises(Exception):  # Pydantic ValidationError
            ReschedulingSuggestion(
                task_id=task_id,
                task_name="Review PR",
                duration_minutes=-30,
                current_deadline=current_deadline,
                candidate_slot=slot,
                reason="Test",
            )

    def test_rescheduling_suggestion_serialization(self):
        """Should serialize with camelCase aliases."""
        task_id = uuid4()
        current_deadline = datetime(2026, 9, 25, 17, 0, tzinfo=timezone.utc)
        start = datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc)
        end = datetime(2026, 9, 23, 11, 0, tzinfo=timezone.utc)

        slot = CandidateSlot(start=start, end=end, available_minutes=120)

        suggestion = ReschedulingSuggestion(
            task_id=task_id,
            task_name="Review PR",
            duration_minutes=120,
            current_deadline=current_deadline,
            candidate_slot=slot,
            reason="Fits available slot on Tuesday morning",
        )

        data = suggestion.model_dump(by_alias=True)

        assert "taskId" in data
        assert "taskName" in data
        assert "durationMinutes" in data
        assert "currentDeadline" in data
        assert "candidateSlot" in data
        assert "reason" in data
        assert data["taskName"] == "Review PR"


class TestReschedulingResult:
    """Test ReschedulingResult schema structure."""

    def test_valid_empty_rescheduling_result(self):
        """Should construct an empty ReschedulingResult."""
        result = ReschedulingResult()

        assert result.suggestions == []
        assert result.overloaded_days == []
        assert result.unscheduled_tasks == []

    def test_rescheduling_result_with_suggestions(self):
        """Should construct ReschedulingResult with multiple suggestions."""
        task_id_1 = uuid4()
        task_id_2 = uuid4()
        current_deadline = datetime(2026, 9, 25, 17, 0, tzinfo=timezone.utc)

        slot_1 = CandidateSlot(
            start=datetime(2026, 9, 23, 9, 0, tzinfo=timezone.utc),
            end=datetime(2026, 9, 23, 11, 0, tzinfo=timezone.utc),
            available_minutes=120,
        )

        slot_2 = CandidateSlot(
            start=datetime(2026, 9, 24, 14, 0, tzinfo=timezone.utc),
            end=datetime(2026, 9, 24, 15, 30, tzinfo=timezone.utc),
            available_minutes=90,
        )

        suggestion_1 = ReschedulingSuggestion(
            task_id=task_id_1,
            task_name="Task A",
            duration_minutes=120,
            current_deadline=current_deadline,
            candidate_slot=slot_1,
            reason="Morning slot",
        )

        suggestion_2 = ReschedulingSuggestion(
            task_id=task_id_2,
            task_name="Task B",
            duration_minutes=90,
            current_deadline=current_deadline,
            candidate_slot=slot_2,
            reason="Afternoon slot",
        )

        result = ReschedulingResult(suggestions=[suggestion_1, suggestion_2])

        assert len(result.suggestions) == 2
        assert result.suggestions[0].task_name == "Task A"
        assert result.suggestions[1].task_name == "Task B"

    def test_rescheduling_result_with_unscheduled_tasks(self):
        """Should construct ReschedulingResult with unscheduled tasks."""
        task_id = uuid4()
        task_summary = TaskSummary(
            id=task_id,
            task_name="Cannot fit",
            priority=TaskPriority.HIGH,
            status=TaskStatus.TODO,
            deadline=datetime(2026, 9, 23, 17, 0, tzinfo=timezone.utc),
            duration_minutes=300,
        )

        result = ReschedulingResult(unscheduled_tasks=[task_summary])

        assert len(result.unscheduled_tasks) == 1
        assert result.unscheduled_tasks[0].task_name == "Cannot fit"

    def test_rescheduling_result_with_overloaded_days(self):
        """Should construct ReschedulingResult with overloaded days."""
        result = ReschedulingResult(
            overloaded_days=[date(2026, 9, 23), date(2026, 9, 24)]
        )

        assert len(result.overloaded_days) == 2
        assert date(2026, 9, 23) in result.overloaded_days

    def test_rescheduling_result_serialization(self):
        """Should serialize with camelCase aliases."""
        task_id = uuid4()
        task_summary = TaskSummary(
            id=task_id,
            task_name="Task X",
            priority=TaskPriority.MEDIUM,
            status=TaskStatus.TODO,
            deadline=datetime(2026, 9, 23, 17, 0, tzinfo=timezone.utc),
            duration_minutes=60,
        )

        result = ReschedulingResult(
            suggestions=[],
            overloaded_days=[date(2026, 9, 23)],
            unscheduled_tasks=[task_summary],
        )

        data = result.model_dump(by_alias=True)

        assert "suggestions" in data
        assert "overloadedDays" in data
        assert "unscheduledTasks" in data
        assert len(data["unscheduledTasks"]) == 1
