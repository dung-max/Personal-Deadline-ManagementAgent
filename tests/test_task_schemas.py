"""Tests for TaskCreateRequest, TaskUpdateRequest, TaskResponseData, and TaskSummary schemas.

Covers duration_minutes field: validation, PATCH semantics (omitted / null / int),
serialization, and Phase 7 backward compatibility.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from personal_deadline_management_agent.models import Task, TaskPriority, TaskStatus
from personal_deadline_management_agent.schemas.task import (
    TaskCreateRequest,
    TaskResponseData,
    TaskUpdateRequest,
)
from personal_deadline_management_agent.schemas.workload import TaskSummary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEADLINE = "2027-01-15T10:00:00Z"


def _create_task(
    *,
    task_name: str = "Test",
    deadline: str = _DEADLINE,
    priority: str = "MEDIUM",
    duration_minutes: int | None = None,
) -> TaskCreateRequest:
    payload: dict[str, Any] = {
        "taskName": task_name,
        "deadline": deadline,
        "priority": priority,
    }
    if duration_minutes is not None:
        payload["durationMinutes"] = duration_minutes
    return TaskCreateRequest(**payload)


def _task_domain(
    *,
    duration_minutes: int | None = None,
    deadline: datetime | None = None,
) -> Task:
    task = Task(
        task_name="Domain Task",
        description="A task",
        deadline=deadline or datetime(2027, 6, 1, tzinfo=timezone.utc),
        priority=TaskPriority.MEDIUM.value,
        status=TaskStatus.TODO.value,
        duration_minutes=duration_minutes,
    )
    task.id = uuid4()
    task.created_at = datetime(2027, 1, 1, tzinfo=timezone.utc)
    task.updated_at = datetime(2027, 1, 1, tzinfo=timezone.utc)
    return task


# ===========================================================================
# TaskCreateRequest — duration_minutes
# ===========================================================================


class TestTaskCreateRequestDuration:
    """Duration field on TaskCreateRequest."""

    def test_omitted_duration_is_none(self) -> None:
        req = _create_task()
        assert req.duration_minutes is None

    def test_explicit_none_duration_is_none(self) -> None:
        req = _create_task(duration_minutes=None)
        assert req.duration_minutes is None

    def test_positive_duration_accepted(self) -> None:
        req = _create_task(duration_minutes=60)
        assert req.duration_minutes == 60

    def test_large_positive_duration_accepted(self) -> None:
        req = _create_task(duration_minutes=480)
        assert req.duration_minutes == 480

    def test_duration_zero_rejected(self) -> None:
        with pytest.raises(ValidationError, match="positive"):
            _create_task(duration_minutes=0)

    def test_negative_duration_rejected(self) -> None:
        with pytest.raises(ValidationError, match="positive"):
            _create_task(duration_minutes=-30)

    def test_duration_from_camel_alias(self) -> None:
        req = TaskCreateRequest(
            taskName="Alias",
            deadline=_DEADLINE,
            priority="LOW",
            durationMinutes=120,
        )
        assert req.duration_minutes == 120

    def test_duration_from_snake(self) -> None:
        req = TaskCreateRequest(
            taskName="Snake",
            deadline=_DEADLINE,
            priority="LOW",
            duration_minutes=90,
        )
        assert req.duration_minutes == 90

    def test_duration_type_is_int_or_none(self) -> None:
        req_none = _create_task()
        req_int = _create_task(duration_minutes=45)
        assert isinstance(req_none.duration_minutes, type(None))
        assert isinstance(req_int.duration_minutes, int)


# ===========================================================================
# TaskUpdateRequest — PATCH semantics (omitted / null / int)
# ===========================================================================


class TestTaskUpdateRequestDuration:
    """PATCH semantics: omitted != null != int."""

    def test_omitted_duration_not_in_fields_set(self) -> None:
        req = TaskUpdateRequest.model_validate({"taskName": "only"})
        assert "duration_minutes" not in req.model_fields_set

    def test_omitted_duration_is_unset_sentinel(self) -> None:
        from personal_deadline_management_agent.schemas.task import _UNSET

        req = TaskUpdateRequest.model_validate({"taskName": "only"})
        assert req.duration_minutes is _UNSET

    def test_explicit_null_in_fields_set(self) -> None:
        req = TaskUpdateRequest.model_validate({"duration_minutes": None})
        assert "duration_minutes" in req.model_fields_set

    def test_explicit_null_value_is_none(self) -> None:
        req = TaskUpdateRequest.model_validate({"duration_minutes": None})
        assert req.duration_minutes is None

    def test_explicit_positive_int_in_fields_set(self) -> None:
        req = TaskUpdateRequest.model_validate({"duration_minutes": 120})
        assert "duration_minutes" in req.model_fields_set
        assert req.duration_minutes == 120

    def test_duration_zero_rejected(self) -> None:
        with pytest.raises(ValidationError, match="positive"):
            TaskUpdateRequest.model_validate({"duration_minutes": 0})

    def test_negative_duration_rejected(self) -> None:
        with pytest.raises(ValidationError, match="positive"):
            TaskUpdateRequest.model_validate({"duration_minutes": -5})

    def test_duration_from_camel_alias_in_patch(self) -> None:
        req = TaskUpdateRequest.model_validate({"durationMinutes": 90})
        assert req.duration_minutes == 90
        assert "duration_minutes" in req.model_fields_set

    def test_duration_none_from_camel_alias(self) -> None:
        req = TaskUpdateRequest.model_validate({"durationMinutes": None})
        assert req.duration_minutes is None
        assert "duration_minutes" in req.model_fields_set

    def test_other_fields_unaffected_by_duration(self) -> None:
        req = TaskUpdateRequest.model_validate(
            {"taskName": "Updated", "duration_minutes": 60}
        )
        assert "task_name" in req.model_fields_set
        assert "duration_minutes" in req.model_fields_set
        assert req.task_name == "Updated"
        assert req.duration_minutes == 60


# ===========================================================================
# TaskResponseData — duration_minutes serialization
# ===========================================================================


class TestTaskResponseDataDuration:
    """Response data includes duration_minutes correctly."""

    def test_domain_with_duration_maps_to_response(self) -> None:
        task = _task_domain(duration_minutes=120)
        resp = TaskResponseData.from_domain(task)
        assert resp.duration_minutes == 120

    def test_domain_without_duration_is_none(self) -> None:
        task = _task_domain(duration_minutes=None)
        resp = TaskResponseData.from_domain(task)
        assert resp.duration_minutes is None

    def test_response_serializes_camel_alias(self) -> None:
        task = _task_domain(duration_minutes=90)
        resp = TaskResponseData.from_domain(task)
        serialized = resp.model_dump(by_alias=True)
        assert "durationMinutes" in serialized
        assert serialized["durationMinutes"] == 90

    def test_response_without_duration_serializes_null(self) -> None:
        task = _task_domain(duration_minutes=None)
        resp = TaskResponseData.from_domain(task)
        serialized = resp.model_dump(by_alias=True)
        assert serialized["durationMinutes"] is None

    def test_from_attributes_with_none(self) -> None:
        task = _task_domain(duration_minutes=None)
        # Use established from_domain() conversion path
        resp = TaskResponseData.from_domain(task)
        assert resp.duration_minutes is None


# ===========================================================================
# TaskSummary (workload) — duration_minutes
# ===========================================================================


class TestTaskSummaryDuration:
    """Workload TaskSummary includes optional duration_minutes."""

    def test_summary_with_duration(self) -> None:
        s = TaskSummary(
            id=uuid4(),
            task_name="A",
            priority=TaskPriority.HIGH,
            status=TaskStatus.TODO,
            deadline=datetime(2027, 3, 1, tzinfo=timezone.utc),
            duration_minutes=120,
        )
        assert s.duration_minutes == 120

    def test_summary_without_duration(self) -> None:
        s = TaskSummary(
            id=uuid4(),
            task_name="B",
            priority=TaskPriority.LOW,
            status=TaskStatus.TODO,
            deadline=datetime(2027, 4, 1, tzinfo=timezone.utc),
        )
        assert s.duration_minutes is None

    def test_summary_serialization_with_duration(self) -> None:
        s = TaskSummary(
            id=uuid4(),
            task_name="C",
            priority=TaskPriority.MEDIUM,
            status=TaskStatus.IN_PROGRESS,
            deadline=None,
            duration_minutes=45,
        )
        dumped = s.model_dump(by_alias=True)
        assert dumped["durationMinutes"] == 45

    def test_summary_serialization_without_duration(self) -> None:
        s = TaskSummary(
            id=uuid4(),
            task_name="D",
            priority=TaskPriority.HIGH,
            status=TaskStatus.TODO,
            deadline=None,
        )
        dumped = s.model_dump(by_alias=True)
        assert dumped["durationMinutes"] is None

    def test_summary_backward_compatible_no_duration_field(self) -> None:
        """Creating TaskSummary without duration_minutes still works."""
        s = TaskSummary(
            id=uuid4(),
            task_name="Legacy",
            priority=TaskPriority.HIGH,
            status=TaskStatus.TODO,
            deadline=datetime(2027, 5, 1, tzinfo=timezone.utc),
        )
        assert s.duration_minutes is None


# ===========================================================================
# Regression: existing fields unaffected
# ===========================================================================


class TestExistingFieldsRegression:
    """Existing fields on all schemas remain backward-compatible."""

    def test_create_request_existing_fields_unaffected(self) -> None:
        req = _create_task(
            task_name="Regression",
            deadline="2027-08-01T00:00:00Z",
            priority="HIGH",
        )
        assert req.task_name == "Regression"
        assert req.priority == "HIGH"
        assert req.duration_minutes is None

    def test_update_request_existing_fields_unaffected(self) -> None:
        req = TaskUpdateRequest.model_validate({"taskName": "Upd"})
        assert "duration_minutes" not in req.model_fields_set
        assert req.task_name == "Upd"

    def test_response_data_existing_fields_unaffected(self) -> None:
        task = _task_domain(duration_minutes=None)
        resp = TaskResponseData.from_domain(task)
        assert resp.task_name == "Domain Task"
        assert resp.duration_minutes is None
        serialized = resp.model_dump(by_alias=True)
        assert serialized["durationMinutes"] is None
        assert "priority" in serialized
        assert "status" in serialized
