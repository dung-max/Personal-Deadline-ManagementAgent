"""Unit tests for the action execution boundary (Phase 4.6).

Verifies:
- Every supported action routes to the correct existing Module boundary.
- Non-AUTHORIZED decisions (DENIED, NOT_CONFIGURED, CONFIRMATION_REQUIRED)
  are never executed.
- The executor never bypasses the application layer (no direct repository
  access, no sessions, no commit/rollback, no LLM/AWS).
- Underlying application failures become controlled ExecutionResults.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from personal_deadline_management_agent.exceptions.reminder import (
    InvalidReminderError,
    ReminderNotFoundError,
)
from personal_deadline_management_agent.exceptions.task import (
    InvalidTaskError,
    TaskNotFoundError,
)
from personal_deadline_management_agent.guardrails import DecisionResult, DecisionStatus
from personal_deadline_management_agent.models import TaskPriority
from personal_deadline_management_agent.schemas import ActionType
from personal_deadline_management_agent.services.action_executor import ActionExecutor
from personal_deadline_management_agent.services.action_validator import ValidatedAction
from personal_deadline_management_agent.services.execution_command import ExecutionCommand
from personal_deadline_management_agent.services.execution_result import (
    ExecutionErrorCode,
    ExecutionResult,
    ExecutionStatus,
)

NOW = datetime.now(timezone.utc)
TASK_ID = uuid4()
REMINDER_ID = uuid4()

CREATE_TASK_PARAMS = {
    "taskName": "Quarterly Report",
    "description": "Draft the quarterly report",
    "deadline": "2026-10-01T12:00:00Z",
    "priority": "HIGH",
}
UPDATE_TASK_PARAMS = {"taskName": "Renamed", "status": "IN_PROGRESS"}
CREATE_REMINDER_PARAMS = {"remindAt": "2026-09-20T09:00:00Z"}
UPDATE_REMINDER_PARAMS = {"remindAt": "2026-09-21T09:00:00Z"}


def _validated(
    action_type: ActionType,
    resource_id=None,
    parameters=None,
) -> ValidatedAction:
    return ValidatedAction(
        action_type=action_type,
        resource_id=resource_id,
        parameters=parameters or {},
    )


def _authorized(action: ValidatedAction) -> DecisionResult:
    return DecisionResult(status=DecisionStatus.AUTHORIZED, action=action)


def _command(
    action_type: ActionType,
    resource_id=None,
    parameters=None,
) -> ExecutionCommand:
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


# --- Successful execution ----------------------------------------------------


def test_create_task_routes_to_task_module(executor, task_module):
    action = _validated(ActionType.CREATE_TASK, parameters=CREATE_TASK_PARAMS)
    command = _command(ActionType.CREATE_TASK, parameters=CREATE_TASK_PARAMS)

    result = executor.execute(_authorized(action), command)

    assert result.status == ExecutionStatus.EXECUTED
    assert result.action_type == ActionType.CREATE_TASK
    task_module.create_task.assert_called_once_with(
        task_name="Quarterly Report",
        description="Draft the quarterly report",
        deadline=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
        priority=TaskPriority.HIGH,
        duration_minutes=None,
    )


def test_create_task_defaults_priority_to_medium(executor, task_module):
    params = {"taskName": "Report", "deadline": "2026-10-01T12:00:00Z"}
    action = _validated(ActionType.CREATE_TASK, parameters=params)
    command = _command(ActionType.CREATE_TASK, parameters=params)

    result = executor.execute(_authorized(action), command)

    assert result.status == ExecutionStatus.EXECUTED
    task_module.create_task.assert_called_once()
    assert task_module.create_task.call_args.kwargs["priority"] == TaskPriority.MEDIUM


def test_update_task_routes_to_task_module(executor, task_module):
    action = _validated(
        ActionType.UPDATE_TASK,
        resource_id=TASK_ID,
        parameters=UPDATE_TASK_PARAMS,
    )
    command = _command(
        ActionType.UPDATE_TASK,
        resource_id=TASK_ID,
        parameters=UPDATE_TASK_PARAMS,
    )

    result = executor.execute(_authorized(action), command)

    assert result.status == ExecutionStatus.EXECUTED
    task_module.update_task.assert_called_once_with(
        task_id=TASK_ID,
        task_name="Renamed",
        description=None,
        deadline=None,
        priority=None,
        status="IN_PROGRESS",
    )


def test_delete_task_routes_to_task_module(executor, task_module):
    action = _validated(ActionType.DELETE_TASK, resource_id=TASK_ID)
    command = _command(ActionType.DELETE_TASK, resource_id=TASK_ID)

    result = executor.execute(_authorized(action), command)

    assert result.status == ExecutionStatus.EXECUTED
    task_module.delete_task.assert_called_once_with(task_id=TASK_ID)


def test_create_reminder_routes_to_reminder_module(executor, reminder_module):
    action = _validated(
        ActionType.CREATE_REMINDER,
        resource_id=TASK_ID,
        parameters=CREATE_REMINDER_PARAMS,
    )
    command = _command(
        ActionType.CREATE_REMINDER,
        resource_id=TASK_ID,
        parameters=CREATE_REMINDER_PARAMS,
    )

    result = executor.execute(_authorized(action), command)

    assert result.status == ExecutionStatus.EXECUTED
    reminder_module.create_reminder.assert_called_once_with(
        task_id=TASK_ID,
        remind_at=datetime(2026, 9, 20, 9, 0, tzinfo=timezone.utc),
    )


def test_update_reminder_routes_to_reminder_module(executor, reminder_module):
    action = _validated(
        ActionType.UPDATE_REMINDER,
        resource_id=REMINDER_ID,
        parameters=UPDATE_REMINDER_PARAMS,
    )
    command = _command(
        ActionType.UPDATE_REMINDER,
        resource_id=REMINDER_ID,
        parameters=UPDATE_REMINDER_PARAMS,
    )

    result = executor.execute(_authorized(action), command)

    assert result.status == ExecutionStatus.EXECUTED
    reminder_module.update_reminder.assert_called_once_with(
        reminder_id=REMINDER_ID,
        remind_at=datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc),
        status=None,
    )


def test_delete_reminder_routes_to_reminder_module(executor, reminder_module):
    action = _validated(ActionType.DELETE_REMINDER, resource_id=REMINDER_ID)
    command = _command(ActionType.DELETE_REMINDER, resource_id=REMINDER_ID)

    result = executor.execute(_authorized(action), command)

    assert result.status == ExecutionStatus.EXECUTED
    reminder_module.delete_reminder.assert_called_once_with(reminder_id=REMINDER_ID)


def test_naive_datetime_string_is_rejected(executor, task_module):
    """Naive datetime strings must be rejected, not silently treated as UTC."""
    params = {"taskName": "Report", "deadline": "2026-10-01T12:00:00"}
    action = _validated(ActionType.CREATE_TASK, parameters=params)
    command = _command(ActionType.CREATE_TASK, parameters=params)

    result = executor.execute(_authorized(action), command)

    assert result.status == ExecutionStatus.EXECUTION_FAILED
    assert result.error_code == ExecutionErrorCode.INVALID_INPUT
    task_module.create_task.assert_not_called()


def test_naive_datetime_object_is_rejected(executor, task_module):
    """Naive datetime objects must be rejected in parameters."""
    params = {"taskName": "Report", "deadline": datetime(2026, 10, 1, 12, 0)}
    action = _validated(ActionType.CREATE_TASK, parameters=params)
    command = _command(ActionType.CREATE_TASK, parameters=params)

    result = executor.execute(_authorized(action), command)

    assert result.status == ExecutionStatus.EXECUTION_FAILED
    assert result.error_code == ExecutionErrorCode.INVALID_INPUT
    task_module.create_task.assert_not_called()


def test_aware_plus_0700_deadline_is_normalized_to_utc(executor, task_module):
    """An aware deadline with +07:00 offset is converted to UTC before passing to module."""
    params = {"taskName": "Report", "deadline": "2026-10-01T19:00:00+07:00"}
    action = _validated(ActionType.CREATE_TASK, parameters=params)
    command = _command(ActionType.CREATE_TASK, parameters=params)

    result = executor.execute(_authorized(action), command)

    assert result.status == ExecutionStatus.EXECUTED
    # 2026-10-01T19:00:00+07:00 == 2026-10-01T12:00:00+00:00
    assert task_module.create_task.call_args.kwargs["deadline"] == datetime(
        2026, 10, 1, 12, 0, tzinfo=timezone.utc
    )


def test_naive_remind_at_is_rejected(executor, reminder_module):
    """Naive datetime strings in remindAt must be rejected."""
    params = {"remindAt": "2026-09-20T09:00:00"}
    action = _validated(
        ActionType.CREATE_REMINDER,
        resource_id=TASK_ID,
        parameters=params,
    )
    command = _command(
        ActionType.CREATE_REMINDER,
        resource_id=TASK_ID,
        parameters=params,
    )

    result = executor.execute(_authorized(action), command)

    assert result.status == ExecutionStatus.EXECUTION_FAILED
    assert result.error_code == ExecutionErrorCode.INVALID_INPUT
    reminder_module.create_reminder.assert_not_called()


# --- Safety boundary ---------------------------------------------------------


@pytest.mark.parametrize(
    "status",
    [
        DecisionStatus.DENIED,
        DecisionStatus.NOT_CONFIGURED,
        DecisionStatus.CONFIRMATION_REQUIRED,
    ],
)
def test_non_authorized_decisions_are_never_executed(
    executor, task_module, reminder_module, status
):
    action = _validated(ActionType.CREATE_TASK, parameters=CREATE_TASK_PARAMS)
    command = _command(ActionType.CREATE_TASK, parameters=CREATE_TASK_PARAMS)
    decision = DecisionResult(status=status, action=action)

    result = executor.execute(decision, command)

    assert result.status == ExecutionStatus.NOT_EXECUTABLE
    assert "AUTHORIZED" in result.message
    task_module.create_task.assert_not_called()
    reminder_module.create_reminder.assert_not_called()


def test_delete_task_confirmation_required_does_not_execute(executor, task_module):
    action = _validated(ActionType.DELETE_TASK, resource_id=TASK_ID)
    command = _command(ActionType.DELETE_TASK, resource_id=TASK_ID)
    decision = DecisionResult(status=DecisionStatus.CONFIRMATION_REQUIRED, action=action)

    result = executor.execute(decision, command)

    assert result.status == ExecutionStatus.NOT_EXECUTABLE
    task_module.delete_task.assert_not_called()


def test_delete_reminder_confirmation_required_does_not_execute(
    executor, reminder_module
):
    action = _validated(ActionType.DELETE_REMINDER, resource_id=REMINDER_ID)
    command = _command(ActionType.DELETE_REMINDER, resource_id=REMINDER_ID)
    decision = DecisionResult(status=DecisionStatus.CONFIRMATION_REQUIRED, action=action)

    result = executor.execute(decision, command)

    assert result.status == ExecutionStatus.NOT_EXECUTABLE
    reminder_module.delete_reminder.assert_not_called()


def test_command_mismatching_decision_is_not_executed(executor, task_module):
    action = _validated(ActionType.CREATE_TASK, parameters=CREATE_TASK_PARAMS)
    command = _command(ActionType.DELETE_TASK, resource_id=TASK_ID)

    result = executor.execute(_authorized(action), command)

    assert result.status == ExecutionStatus.NOT_EXECUTABLE
    task_module.delete_task.assert_not_called()
    task_module.create_task.assert_not_called()


# --- Boundary ----------------------------------------------------------------


def test_execution_command_has_no_execute_method():
    command = _command(ActionType.CREATE_TASK, parameters=CREATE_TASK_PARAMS)
    assert isinstance(command, ExecutionCommand)
    assert not hasattr(command, "execute")
    assert not hasattr(command, "confirm")


def test_executor_does_not_access_repositories_directly(
    executor, task_module, monkeypatch
):
    from personal_deadline_management_agent.repositories.reminder_repository import (
        ReminderRepository,
    )
    from personal_deadline_management_agent.repositories.task_repository import (
        TaskRepository,
    )

    def explode(*args, **kwargs):
        raise AssertionError("executor must not access repositories directly")

    for method in ("create", "update", "delete", "get_by_id", "find_by_name", "list"):
        monkeypatch.setattr(TaskRepository, method, explode)
    for method in (
        "create",
        "update",
        "delete",
        "get_by_id",
        "list_by_task_id",
        "find_by_task_name",
    ):
        monkeypatch.setattr(ReminderRepository, method, explode)

    action = _validated(ActionType.CREATE_TASK, parameters=CREATE_TASK_PARAMS)
    command = _command(ActionType.CREATE_TASK, parameters=CREATE_TASK_PARAMS)

    result = executor.execute(_authorized(action), command)

    assert result.status == ExecutionStatus.EXECUTED
    task_module.create_task.assert_called_once()


def test_executor_does_not_create_database_sessions(executor, task_module, monkeypatch):
    from sqlalchemy.orm import Session

    def explode(*args, **kwargs):
        raise AssertionError("executor must not create database sessions")

    monkeypatch.setattr(Session, "__init__", explode)

    action = _validated(ActionType.CREATE_TASK, parameters=CREATE_TASK_PARAMS)
    command = _command(ActionType.CREATE_TASK, parameters=CREATE_TASK_PARAMS)

    result = executor.execute(_authorized(action), command)

    assert result.status == ExecutionStatus.EXECUTED
    task_module.create_task.assert_called_once()


def test_executor_does_not_commit_or_rollback(executor, task_module, monkeypatch):
    from personal_deadline_management_agent.uow import UnitOfWork

    def explode(*args, **kwargs):
        raise AssertionError("executor must not commit or rollback")

    monkeypatch.setattr(UnitOfWork, "commit", explode)
    monkeypatch.setattr(UnitOfWork, "rollback", explode)

    action = _validated(ActionType.CREATE_TASK, parameters=CREATE_TASK_PARAMS)
    command = _command(ActionType.CREATE_TASK, parameters=CREATE_TASK_PARAMS)

    result = executor.execute(_authorized(action), command)

    assert result.status == ExecutionStatus.EXECUTED
    task_module.create_task.assert_called_once()


def test_executor_does_not_call_llm_or_aws(executor, task_module, monkeypatch):
    from personal_deadline_management_agent.adapters.structured_generation import (
        GenaiCoreBedrockAdapter,
    )
    from personal_deadline_management_agent.services.agent_interpreter import (
        AgentInterpreter,
    )

    def explode(*args, **kwargs):
        raise AssertionError("executor must not call LLM/AWS")

    monkeypatch.setattr(AgentInterpreter, "interpret", explode)
    monkeypatch.setattr(GenaiCoreBedrockAdapter, "generate", explode)

    action = _validated(ActionType.CREATE_TASK, parameters=CREATE_TASK_PARAMS)
    command = _command(ActionType.CREATE_TASK, parameters=CREATE_TASK_PARAMS)

    result = executor.execute(_authorized(action), command)

    assert result.status == ExecutionStatus.EXECUTED
    task_module.create_task.assert_called_once()


def test_no_confirmation_workflow_is_created(executor, task_module):
    action = _validated(ActionType.DELETE_TASK, resource_id=TASK_ID)
    command = _command(ActionType.DELETE_TASK, resource_id=TASK_ID)
    decision = DecisionResult(status=DecisionStatus.CONFIRMATION_REQUIRED, action=action)

    result = executor.execute(decision, command)

    assert result.status == ExecutionStatus.NOT_EXECUTABLE
    assert not hasattr(result, "confirmation_token")
    assert not hasattr(result, "confirmation_state")
    task_module.delete_task.assert_not_called()


def test_no_langgraph_is_imported_by_execution_boundary():
    import sys

    import personal_deadline_management_agent.services.action_executor  # noqa: F401

    assert "langgraph" not in sys.modules


# --- Failure handling --------------------------------------------------------


def test_module_failure_becomes_controlled_execution_result(executor, task_module):
    task_module.create_task.side_effect = RuntimeError(
        "connection to server at db.internal:5432 failed: password authentication failed"
    )
    action = _validated(ActionType.CREATE_TASK, parameters=CREATE_TASK_PARAMS)
    command = _command(ActionType.CREATE_TASK, parameters=CREATE_TASK_PARAMS)

    result = executor.execute(_authorized(action), command)

    assert result.status == ExecutionStatus.EXECUTION_FAILED
    assert result.message == "The application operation failed."
    assert result.error_code == ExecutionErrorCode.INFRASTRUCTURE
    assert "db.internal" not in result.message
    assert "password" not in result.message
    assert "Traceback" not in result.message


def test_malformed_command_fails_safely(executor, task_module):
    # Missing required "deadline" parameter — must not reach the module.
    params = {"taskName": "Report"}
    action = _validated(ActionType.CREATE_TASK, parameters=params)
    command = _command(ActionType.CREATE_TASK, parameters=params)

    result = executor.execute(_authorized(action), command)

    assert result.status == ExecutionStatus.EXECUTION_FAILED
    assert result.message == "The application operation failed."
    task_module.create_task.assert_not_called()


def test_invalid_datetime_fails_safely(executor, task_module):
    params = {"taskName": "Report", "deadline": "not-a-date"}
    action = _validated(ActionType.CREATE_TASK, parameters=params)
    command = _command(ActionType.CREATE_TASK, parameters=params)

    result = executor.execute(_authorized(action), command)

    assert result.status == ExecutionStatus.EXECUTION_FAILED
    assert result.message == "The application operation failed."
    task_module.create_task.assert_not_called()


def test_unsupported_action_type_fails_safely(executor, task_module, reminder_module):
    # Bypass Pydantic validation to simulate a future/unknown action type.
    action = ValidatedAction.model_construct(
        action_type="SEND_EMAIL",
        resource_id=None,
        parameters={},
    )
    command = ExecutionCommand.model_construct(
        action_type="SEND_EMAIL",
        resource_id=None,
        parameters={},
    )

    result = executor.execute(_authorized(action), command)

    assert result.status == ExecutionStatus.EXECUTION_FAILED
    assert result.message == "The application operation failed."
    task_module.assert_not_called()
    reminder_module.assert_not_called()


# --- ExecutionCommand.from_decision -----------------------------------------


def test_from_decision_creates_command_from_decision_action():
    action = _validated(
        ActionType.UPDATE_TASK,
        resource_id=TASK_ID,
        parameters=UPDATE_TASK_PARAMS,
    )
    command = ExecutionCommand.from_decision(_authorized(action))

    assert command.action_type == ActionType.UPDATE_TASK
    assert command.resource_id == TASK_ID
    assert command.parameters == UPDATE_TASK_PARAMS


def test_from_decision_create_task_has_no_resource_id():
    action = _validated(ActionType.CREATE_TASK, parameters=CREATE_TASK_PARAMS)
    command = ExecutionCommand.from_decision(_authorized(action))

    assert command.action_type == ActionType.CREATE_TASK
    assert command.resource_id is None
    assert command.parameters == CREATE_TASK_PARAMS


def test_from_decision_is_classmethod():
    import inspect

    assert isinstance(
        inspect.getattr_static(ExecutionCommand, "from_decision"), classmethod
    )


# --- Error codes -------------------------------------------------------------


def test_task_not_found_maps_to_not_found(executor, task_module):
    task_module.create_task.side_effect = TaskNotFoundError(TASK_ID)
    action = _validated(ActionType.CREATE_TASK, parameters=CREATE_TASK_PARAMS)
    command = _command(ActionType.CREATE_TASK, parameters=CREATE_TASK_PARAMS)

    result = executor.execute(_authorized(action), command)

    assert result.status == ExecutionStatus.EXECUTION_FAILED
    assert result.error_code == ExecutionErrorCode.NOT_FOUND


def test_reminder_not_found_maps_to_not_found(executor, reminder_module):
    reminder_module.create_reminder.side_effect = ReminderNotFoundError(REMINDER_ID)
    action = _validated(
        ActionType.CREATE_REMINDER,
        resource_id=TASK_ID,
        parameters=CREATE_REMINDER_PARAMS,
    )
    command = _command(
        ActionType.CREATE_REMINDER,
        resource_id=TASK_ID,
        parameters=CREATE_REMINDER_PARAMS,
    )

    result = executor.execute(_authorized(action), command)

    assert result.status == ExecutionStatus.EXECUTION_FAILED
    assert result.error_code == ExecutionErrorCode.NOT_FOUND


def test_invalid_reminder_maps_to_invalid_input(executor, reminder_module):
    reminder_module.create_reminder.side_effect = InvalidReminderError(
        "remind_at must be before or at the task deadline"
    )
    action = _validated(
        ActionType.CREATE_REMINDER,
        resource_id=TASK_ID,
        parameters=CREATE_REMINDER_PARAMS,
    )
    command = _command(
        ActionType.CREATE_REMINDER,
        resource_id=TASK_ID,
        parameters=CREATE_REMINDER_PARAMS,
    )

    result = executor.execute(_authorized(action), command)

    assert result.status == ExecutionStatus.EXECUTION_FAILED
    assert result.error_code == ExecutionErrorCode.INVALID_INPUT


def test_invalid_task_maps_to_invalid_input(executor, task_module):
    task_module.create_task.side_effect = InvalidTaskError(
        "deadline (2025-01-01) must not be in the past"
    )
    action = _validated(ActionType.CREATE_TASK, parameters=CREATE_TASK_PARAMS)
    command = _command(ActionType.CREATE_TASK, parameters=CREATE_TASK_PARAMS)

    result = executor.execute(_authorized(action), command)

    assert result.status == ExecutionStatus.EXECUTION_FAILED
    assert result.error_code == ExecutionErrorCode.INVALID_INPUT


def test_other_exception_maps_to_infrastructure(executor, task_module):
    task_module.create_task.side_effect = RuntimeError("boom")
    action = _validated(ActionType.CREATE_TASK, parameters=CREATE_TASK_PARAMS)
    command = _command(ActionType.CREATE_TASK, parameters=CREATE_TASK_PARAMS)

    result = executor.execute(_authorized(action), command)

    assert result.status == ExecutionStatus.EXECUTION_FAILED
    assert result.error_code == ExecutionErrorCode.INFRASTRUCTURE


# --- Logging -----------------------------------------------------------------


def test_exception_is_logged_with_safe_context(executor, task_module, caplog):
    task_module.update_task.side_effect = RuntimeError("boom")
    action = _validated(
        ActionType.UPDATE_TASK,
        resource_id=TASK_ID,
        parameters=UPDATE_TASK_PARAMS,
    )
    command = _command(
        ActionType.UPDATE_TASK,
        resource_id=TASK_ID,
        parameters=UPDATE_TASK_PARAMS,
    )

    with caplog.at_level(
        logging.ERROR,
        logger="personal_deadline_management_agent.services.action_executor",
    ):
        result = executor.execute(_authorized(action), command)

    assert result.status == ExecutionStatus.EXECUTION_FAILED
    assert "Action execution failed" in caplog.text
    assert str(TASK_ID) in caplog.text
    # Parameters must NOT be logged (may contain sensitive data).
    assert "Renamed" not in caplog.text


# --- Result payload ----------------------------------------------------------


def test_create_task_result_contains_id_and_name(executor, task_module):
    task_module.create_task.return_value = SimpleNamespace(
        id=TASK_ID, task_name="Quarterly Report"
    )
    action = _validated(ActionType.CREATE_TASK, parameters=CREATE_TASK_PARAMS)
    command = _command(ActionType.CREATE_TASK, parameters=CREATE_TASK_PARAMS)

    result = executor.execute(_authorized(action), command)

    assert result.status == ExecutionStatus.EXECUTED
    assert result.result_id == TASK_ID
    assert result.result_name == "Quarterly Report"


def test_update_task_result_contains_id_and_name(executor, task_module):
    task_module.update_task.return_value = SimpleNamespace(
        id=TASK_ID, task_name="Renamed"
    )
    action = _validated(
        ActionType.UPDATE_TASK,
        resource_id=TASK_ID,
        parameters=UPDATE_TASK_PARAMS,
    )
    command = _command(
        ActionType.UPDATE_TASK,
        resource_id=TASK_ID,
        parameters=UPDATE_TASK_PARAMS,
    )

    result = executor.execute(_authorized(action), command)

    assert result.status == ExecutionStatus.EXECUTED
    assert result.result_id == TASK_ID
    assert result.result_name == "Renamed"


def test_create_reminder_result_contains_id_only(executor, reminder_module):
    reminder_module.create_reminder.return_value = SimpleNamespace(id=REMINDER_ID)
    action = _validated(
        ActionType.CREATE_REMINDER,
        resource_id=TASK_ID,
        parameters=CREATE_REMINDER_PARAMS,
    )
    command = _command(
        ActionType.CREATE_REMINDER,
        resource_id=TASK_ID,
        parameters=CREATE_REMINDER_PARAMS,
    )

    result = executor.execute(_authorized(action), command)

    assert result.status == ExecutionStatus.EXECUTED
    assert result.result_id == REMINDER_ID
    assert result.result_name is None


def test_delete_result_has_no_payload(executor, task_module):
    task_module.delete_task.return_value = None
    action = _validated(ActionType.DELETE_TASK, resource_id=TASK_ID)
    command = _command(ActionType.DELETE_TASK, resource_id=TASK_ID)

    result = executor.execute(_authorized(action), command)

    assert result.status == ExecutionStatus.EXECUTED
    assert result.result_id is None
    assert result.result_name is None


# --- Error code values -------------------------------------------------------


def test_execution_error_code_values():
    values = {s.value for s in ExecutionErrorCode}
    assert values == {"NOT_FOUND", "INVALID_INPUT", "INFRASTRUCTURE"}