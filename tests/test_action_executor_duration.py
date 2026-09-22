"""PDMA-103: ActionExecutor duration_minutes wiring tests.

Focused tests covering the Agent execution path for duration_minutes.
Uses MagicMock modules (matching existing test_action_executor.py pattern):

- CREATE_TASK with/without duration → verify kwargs passed to TaskModule
- UPDATE_TASK omitted/null/value → verify PATCH kwargs dispatched correctly
- Invalid duration (0/negative) → TaskService rejection surfaces as
  EXECUTION_FAILED / INVALID_INPUT through the executor boundary
- TaskSummary includes duration via WorkloadAnalysisService._to_summary
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from personal_deadline_management_agent.exceptions.task import InvalidTaskError
from personal_deadline_management_agent.guardrails import DecisionResult, DecisionStatus
from personal_deadline_management_agent.models import Task, TaskPriority, TaskStatus
from personal_deadline_management_agent.schemas import ActionType
from personal_deadline_management_agent.services.action_executor import ActionExecutor
from personal_deadline_management_agent.services.action_validator import ValidatedAction
from personal_deadline_management_agent.services.execution_command import ExecutionCommand
from personal_deadline_management_agent.services.execution_result import (
    ExecutionErrorCode,
    ExecutionStatus,
)
from personal_deadline_management_agent.services.workload_analysis_service import (
    WorkloadAnalysisService,
)

NOW = datetime.now(timezone.utc)
TASK_ID = uuid4()


def _validated(action_type, resource_id=None, parameters=None) -> ValidatedAction:
    return ValidatedAction(
        action_type=action_type,
        resource_id=resource_id,
        parameters=parameters or {},
    )


def _authorized(action: ValidatedAction) -> DecisionResult:
    return DecisionResult(status=DecisionStatus.AUTHORIZED, action=action)


def _command(action_type, resource_id=None, parameters=None) -> ExecutionCommand:
    return ExecutionCommand(
        action_type=action_type,
        resource_id=resource_id,
        parameters=parameters or {},
    )


@pytest.fixture
def task_module() -> MagicMock:
    return MagicMock()


@pytest.fixture
def reminder_module() -> MagicMock:
    return MagicMock()


@pytest.fixture
def executor(
    task_module: MagicMock,
    reminder_module: MagicMock,
) -> ActionExecutor:
    return ActionExecutor(task_module=task_module, reminder_module=reminder_module)


# --- CREATE_TASK --------------------------------------------------------------


class TestCreateTaskDuration:
    def test_create_task_no_duration_passes_none(
        self, executor: ActionExecutor, task_module: MagicMock
    ) -> None:
        params = {"taskName": "No Duration", "deadline": "2026-12-31T23:59:59Z"}
        action = _validated(ActionType.CREATE_TASK, parameters=params)
        command = _command(ActionType.CREATE_TASK, parameters=params)

        result = executor.execute(_authorized(action), command)

        assert result.status == ExecutionStatus.EXECUTED
        task_module.create_task.assert_called_once()
        assert task_module.create_task.call_args.kwargs["duration_minutes"] is None

    def test_create_task_with_duration_passes_value(
        self, executor: ActionExecutor, task_module: MagicMock
    ) -> None:
        params = {
            "taskName": "With Duration",
            "deadline": "2026-12-31T23:59:59Z",
            "durationMinutes": 120,
        }
        action = _validated(ActionType.CREATE_TASK, parameters=params)
        command = _command(ActionType.CREATE_TASK, parameters=params)

        result = executor.execute(_authorized(action), command)

        assert result.status == ExecutionStatus.EXECUTED
        task_module.create_task.assert_called_once()
        assert task_module.create_task.call_args.kwargs["duration_minutes"] == 120

    def test_create_task_duration_zero_maps_to_invalid_input(
        self, executor: ActionExecutor, task_module: MagicMock
    ) -> None:
        task_module.create_task.side_effect = InvalidTaskError(
            "duration_minutes must be a positive integer"
        )
        params = {
            "taskName": "Zero Duration",
            "deadline": "2026-12-31T23:59:59Z",
            "durationMinutes": 0,
        }
        action = _validated(ActionType.CREATE_TASK, parameters=params)
        command = _command(ActionType.CREATE_TASK, parameters=params)

        result = executor.execute(_authorized(action), command)

        assert result.status == ExecutionStatus.EXECUTION_FAILED
        assert result.error_code == ExecutionErrorCode.INVALID_INPUT

    def test_create_task_duration_negative_maps_to_invalid_input(
        self, executor: ActionExecutor, task_module: MagicMock
    ) -> None:
        task_module.create_task.side_effect = InvalidTaskError(
            "duration_minutes must be a positive integer"
        )
        params = {
            "taskName": "Negative Duration",
            "deadline": "2026-12-31T23:59:59Z",
            "durationMinutes": -10,
        }
        action = _validated(ActionType.CREATE_TASK, parameters=params)
        command = _command(ActionType.CREATE_TASK, parameters=params)

        result = executor.execute(_authorized(action), command)

        assert result.status == ExecutionStatus.EXECUTION_FAILED
        assert result.error_code == ExecutionErrorCode.INVALID_INPUT


# --- UPDATE_TASK PATCH dispatch ----------------------------------------------


class TestUpdateTaskDuration:
    def test_update_omitted_does_not_pass_duration_kwarg(
        self, executor: ActionExecutor, task_module: MagicMock
    ) -> None:
        params = {"taskName": "Renamed"}
        action = _validated(
            ActionType.UPDATE_TASK, resource_id=TASK_ID, parameters=params
        )
        command = _command(
            ActionType.UPDATE_TASK, resource_id=TASK_ID, parameters=params
        )

        result = executor.execute(_authorized(action), command)

        assert result.status == ExecutionStatus.EXECUTED
        task_module.update_task.assert_called_once()
        assert "duration_minutes" not in task_module.update_task.call_args.kwargs

    def test_update_null_passes_none(
        self, executor: ActionExecutor, task_module: MagicMock
    ) -> None:
        params = {"durationMinutes": None}
        action = _validated(
            ActionType.UPDATE_TASK, resource_id=TASK_ID, parameters=params
        )
        command = _command(
            ActionType.UPDATE_TASK, resource_id=TASK_ID, parameters=params
        )

        result = executor.execute(_authorized(action), command)

        assert result.status == ExecutionStatus.EXECUTED
        task_module.update_task.assert_called_once()
        assert task_module.update_task.call_args.kwargs["duration_minutes"] is None

    def test_update_value_passes_int(
        self, executor: ActionExecutor, task_module: MagicMock
    ) -> None:
        params = {"durationMinutes": 180}
        action = _validated(
            ActionType.UPDATE_TASK, resource_id=TASK_ID, parameters=params
        )
        command = _command(
            ActionType.UPDATE_TASK, resource_id=TASK_ID, parameters=params
        )

        result = executor.execute(_authorized(action), command)

        assert result.status == ExecutionStatus.EXECUTED
        task_module.update_task.assert_called_once()
        assert task_module.update_task.call_args.kwargs["duration_minutes"] == 180

    def test_update_with_other_fields_still_dispatches(
        self, executor: ActionExecutor, task_module: MagicMock
    ) -> None:
        params = {"taskName": "Renamed", "durationMinutes": 60}
        action = _validated(
            ActionType.UPDATE_TASK, resource_id=TASK_ID, parameters=params
        )
        command = _command(
            ActionType.UPDATE_TASK, resource_id=TASK_ID, parameters=params
        )

        result = executor.execute(_authorized(action), command)

        assert result.status == ExecutionStatus.EXECUTED
        task_module.update_task.assert_called_once()
        kwargs = task_module.update_task.call_args.kwargs
        assert kwargs["task_name"] == "Renamed"
        assert kwargs["duration_minutes"] == 60
        assert kwargs["task_id"] == TASK_ID

    def test_update_invalid_duration_maps_to_invalid_input(
        self, executor: ActionExecutor, task_module: MagicMock
    ) -> None:
        task_module.update_task.side_effect = InvalidTaskError(
            "duration_minutes must be a positive integer or null"
        )
        params = {"durationMinutes": 0}
        action = _validated(
            ActionType.UPDATE_TASK, resource_id=TASK_ID, parameters=params
        )
        command = _command(
            ActionType.UPDATE_TASK, resource_id=TASK_ID, parameters=params
        )

        result = executor.execute(_authorized(action), command)

        assert result.status == ExecutionStatus.EXECUTION_FAILED
        assert result.error_code == ExecutionErrorCode.INVALID_INPUT


# --- TaskSummary conversion ---------------------------------------------------


class TestTaskSummaryConversion:
    def _to_summary(self, task: Task):
        service = WorkloadAnalysisService()
        return service._to_summary(task)

    def _make_task(
        self, *, duration_minutes: int | None = None
    ) -> Task:
        task = Task(
            task_name="Summary Task",
            description=None,
            deadline=datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc),
            priority=TaskPriority.HIGH.value,
            status=TaskStatus.TODO.value,
            duration_minutes=duration_minutes,
        )
        task.id = TASK_ID
        return task

    def test_summary_includes_duration(self) -> None:
        summary = self._to_summary(self._make_task(duration_minutes=90))
        assert summary.duration_minutes == 90

    def test_summary_none_duration(self) -> None:
        summary = self._to_summary(self._make_task(duration_minutes=None))
        assert summary.duration_minutes is None