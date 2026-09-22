"""Unit tests for ANALYZE_WORKLOAD integration (PDMA-78).

Verifies:
- ActionExecutor correctly orchestrates ANALYZE_WORKLOAD execution
- Date range resolution is delegated to DateRangeResolver
- Task queries are delegated to TaskRepository via TaskModule
- Workload analysis is delegated to WorkloadAnalysisService
- Read-only behavior (no writes to database)
- Result handling and error propagation
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from personal_deadline_management_agent.guardrails import DecisionResult, DecisionStatus
from personal_deadline_management_agent.models import Task, TaskPriority, TaskStatus
from personal_deadline_management_agent.schemas import ActionType
from personal_deadline_management_agent.schemas.agent import DateRangeExpression
from personal_deadline_management_agent.schemas.workload import (
    BusyDayWarning,
    DeadlineCollision,
    TaskSummary,
    WorkloadAnalysisResult,
)
from personal_deadline_management_agent.services.action_executor import ActionExecutor
from personal_deadline_management_agent.services.action_validator import ValidatedAction
from personal_deadline_management_agent.services.date_range_resolver import (
    DateRangeResolver,
)
from personal_deadline_management_agent.services.execution_command import ExecutionCommand
from personal_deadline_management_agent.services.execution_result import ExecutionStatus
from personal_deadline_management_agent.services.workload_analysis_service import (
    WorkloadAnalysisService,
)

# Test constants
NOW = datetime.now(timezone.utc)
TASK_ID_1 = uuid4()
TASK_ID_2 = uuid4()

ANALYZE_PARAMS = {
    "date_range_expression": "THIS_WEEK",
    "explicit_start": None,
    "explicit_end": None,
}


def _validated(
    action_type: ActionType,
    resource_id=None,
    parameters=None,
) -> ValidatedAction:
    """Helper to create ValidatedAction."""
    return ValidatedAction(
        action_type=action_type,
        resource_id=resource_id,
        parameters=parameters or {},
    )


def _authorized(action: ValidatedAction) -> DecisionResult:
    """Helper to create authorized DecisionResult."""
    return DecisionResult(status=DecisionStatus.AUTHORIZED, action=action)


def _command(
    action_type: ActionType,
    resource_id=None,
    parameters=None,
) -> ExecutionCommand:
    """Helper to create ExecutionCommand."""
    return ExecutionCommand(
        action_type=action_type,
        resource_id=resource_id,
        parameters=parameters or {},
    )


@pytest.fixture
def task_module() -> MagicMock:
    """Mock TaskModule for testing."""
    return MagicMock()


@pytest.fixture
def reminder_module() -> MagicMock:
    """Mock ReminderModule for testing."""
    return MagicMock()


@pytest.fixture
def date_range_resolver() -> MagicMock:
    """Mock DateRangeResolver for testing."""
    mock = MagicMock(spec=DateRangeResolver)
    # Default: resolve to a week range
    mock.resolve.return_value = (
        datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 9, 21, 23, 59, 59, tzinfo=timezone.utc),
    )
    return mock


@pytest.fixture
def workload_service() -> MagicMock:
    """Mock WorkloadAnalysisService for testing."""
    return MagicMock(spec=WorkloadAnalysisService)


@pytest.fixture
def executor(
    task_module: MagicMock,
    reminder_module: MagicMock,
    date_range_resolver: MagicMock,
    workload_service: MagicMock,
) -> ActionExecutor:
    """Create ActionExecutor with mocked dependencies."""
    return ActionExecutor(
        task_module=task_module,
        reminder_module=reminder_module,
        date_range_resolver=date_range_resolver,
        workload_analysis_service=workload_service,
    )


# --- Test A: ANALYZE_WORKLOAD executes successfully -------------------------


def test_analyze_workload_executes_successfully(
    executor, task_module, date_range_resolver, workload_service
):
    """Verify ANALYZE_WORKLOAD orchestration: resolver → repository → service → result."""
    # Arrange: mock task data
    task1 = Task(
        id=TASK_ID_1,
        task_name="Task 1",
        deadline=datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc),
        priority=TaskPriority.HIGH,
        status=TaskStatus.TODO,
    )
    task2 = Task(
        id=TASK_ID_2,
        task_name="Task 2",
        deadline=datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc),
        priority=TaskPriority.MEDIUM,
        status=TaskStatus.IN_PROGRESS,
    )
    task_module.find_tasks_by_deadline_range.return_value = [task1, task2]

    # Arrange: mock workload analysis result
    expected_result = WorkloadAnalysisResult(
        total_tasks=2,
        deadline_collisions=[],
        busy_days=[],
        recommended_order=[
            TaskSummary(
                id=TASK_ID_1,
                task_name="Task 1",
                priority=TaskPriority.HIGH,
                status=TaskStatus.TODO,
                deadline=datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc),
            ),
            TaskSummary(
                id=TASK_ID_2,
                task_name="Task 2",
                priority=TaskPriority.MEDIUM,
                status=TaskStatus.IN_PROGRESS,
                deadline=datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc),
            ),
        ],
        explanation="2 active tasks found. No collisions or busy days detected.",
    )
    workload_service.analyze.return_value = expected_result

    # Act
    action = _validated(ActionType.ANALYZE_WORKLOAD, parameters=ANALYZE_PARAMS)
    command = _command(ActionType.ANALYZE_WORKLOAD, parameters=ANALYZE_PARAMS)
    result = executor.execute(_authorized(action), command)

    # Assert: execution succeeded
    assert result.status == ExecutionStatus.EXECUTED
    assert result.action_type == ActionType.ANALYZE_WORKLOAD
    assert result.message == "Workload analysis completed successfully."

    # Assert: date range resolver was called
    date_range_resolver.resolve.assert_called_once_with(
        expression=DateRangeExpression.THIS_WEEK,
        explicit_start=None,
        explicit_end=None,
    )

    # Assert: repository query was called with resolved range
    task_module.find_tasks_by_deadline_range.assert_called_once_with(
        datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 9, 21, 23, 59, 59, tzinfo=timezone.utc),
    )

    # Assert: workload service was called with repository result
    workload_service.analyze.assert_called_once()
    analyzed_tasks = workload_service.analyze.call_args[0][0]
    assert len(analyzed_tasks) == 2
    assert analyzed_tasks[0].id == TASK_ID_1
    assert analyzed_tasks[1].id == TASK_ID_2

    # Assert: result payload contains WorkloadAnalysisResult (camelCase keys)
    assert result.result_payload is not None
    assert result.result_payload["totalTasks"] == 2
    assert len(result.result_payload["recommendedOrder"]) == 2


# --- Test B: Date range is passed correctly ----------------------------------


def test_analyze_workload_passes_date_range_correctly(
    executor, task_module, date_range_resolver, workload_service
):
    """Verify exact start/end values are passed to find_by_deadline_range."""
    # Arrange: custom date range
    custom_start = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)
    custom_end = datetime(2026, 10, 31, 23, 59, 59, tzinfo=timezone.utc)
    date_range_resolver.resolve.return_value = (custom_start, custom_end)
    task_module.find_tasks_by_deadline_range.return_value = []
    workload_service.analyze.return_value = WorkloadAnalysisResult(
        total_tasks=0,
        deadline_collisions=[],
        busy_days=[],
        recommended_order=[],
        explanation="No active tasks found in the specified range.",
    )

    # Act
    action = _validated(ActionType.ANALYZE_WORKLOAD, parameters=ANALYZE_PARAMS)
    command = _command(ActionType.ANALYZE_WORKLOAD, parameters=ANALYZE_PARAMS)
    executor.execute(_authorized(action), command)

    # Assert: repository received exact resolved range
    task_module.find_tasks_by_deadline_range.assert_called_once_with(
        custom_start, custom_end
    )


# --- Test C: Empty result ----------------------------------------------------


def test_analyze_workload_empty_result(
    executor, task_module, date_range_resolver, workload_service
):
    """Verify empty repository result produces valid WorkloadAnalysisResult."""
    # Arrange: repository returns empty list
    task_module.find_tasks_by_deadline_range.return_value = []
    empty_result = WorkloadAnalysisResult(
        total_tasks=0,
        deadline_collisions=[],
        busy_days=[],
        recommended_order=[],
        explanation="No active tasks found in the specified range.",
    )
    workload_service.analyze.return_value = empty_result

    # Act
    action = _validated(ActionType.ANALYZE_WORKLOAD, parameters=ANALYZE_PARAMS)
    command = _command(ActionType.ANALYZE_WORKLOAD, parameters=ANALYZE_PARAMS)
    result = executor.execute(_authorized(action), command)

    # Assert: service was called with empty list
    workload_service.analyze.assert_called_once_with([])

    # Assert: result is valid
    assert result.status == ExecutionStatus.EXECUTED
    assert result.result_payload["totalTasks"] == 0
    assert result.result_payload["deadlineCollisions"] == []
    assert result.result_payload["busyDays"] == []
    assert result.result_payload["recommendedOrder"] == []


# --- Test D: Read-only behavior ----------------------------------------------


def test_analyze_workload_read_only_behavior(
    executor, task_module, reminder_module, workload_service
):
    """Verify ANALYZE_WORKLOAD does not call create/update/delete operations."""
    # Arrange
    task_module.find_tasks_by_deadline_range.return_value = []
    workload_service.analyze.return_value = WorkloadAnalysisResult(
        total_tasks=0,
        deadline_collisions=[],
        busy_days=[],
        recommended_order=[],
        explanation="",
    )

    # Act
    action = _validated(ActionType.ANALYZE_WORKLOAD, parameters=ANALYZE_PARAMS)
    command = _command(ActionType.ANALYZE_WORKLOAD, parameters=ANALYZE_PARAMS)
    executor.execute(_authorized(action), command)

    # Assert: no write operations called
    task_module.create_task.assert_not_called()
    task_module.update_task.assert_not_called()
    task_module.delete_task.assert_not_called()
    reminder_module.create_reminder.assert_not_called()
    reminder_module.update_reminder.assert_not_called()
    reminder_module.delete_reminder.assert_not_called()


# --- Test E: Completed tasks not filtered ------------------------------------


def test_analyze_workload_passes_completed_tasks_to_service(
    executor, task_module, workload_service
):
    """Verify repository result is passed unchanged to service (including COMPLETED)."""
    # Arrange: repository returns mix of statuses
    completed_task = Task(
        id=TASK_ID_1,
        task_name="Completed Task",
        deadline=datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc),
        priority=TaskPriority.LOW,
        status=TaskStatus.COMPLETED,
    )
    active_task = Task(
        id=TASK_ID_2,
        task_name="Active Task",
        deadline=datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc),
        priority=TaskPriority.HIGH,
        status=TaskStatus.IN_PROGRESS,
    )
    task_module.find_tasks_by_deadline_range.return_value = [
        completed_task,
        active_task,
    ]
    workload_service.analyze.return_value = WorkloadAnalysisResult(
        total_tasks=1,  # Service filters out completed
        deadline_collisions=[],
        busy_days=[],
        recommended_order=[],
        explanation="",
    )

    # Act
    action = _validated(ActionType.ANALYZE_WORKLOAD, parameters=ANALYZE_PARAMS)
    command = _command(ActionType.ANALYZE_WORKLOAD, parameters=ANALYZE_PARAMS)
    executor.execute(_authorized(action), command)

    # Assert: service received both tasks (filtering is service responsibility)
    workload_service.analyze.assert_called_once()
    analyzed_tasks = workload_service.analyze.call_args[0][0]
    assert len(analyzed_tasks) == 2
    assert analyzed_tasks[0].status == TaskStatus.COMPLETED
    assert analyzed_tasks[1].status == TaskStatus.IN_PROGRESS


# --- Test F: Service delegation ----------------------------------------------


def test_analyze_workload_delegates_to_service(
    executor, task_module, workload_service
):
    """Verify ActionExecutor delegates workload logic to WorkloadAnalysisService."""
    # Arrange
    task_module.find_tasks_by_deadline_range.return_value = []
    mock_result = MagicMock(spec=WorkloadAnalysisResult)
    mock_result.model_dump.return_value = {
        "totalTasks": 0,
        "deadlineCollisions": [],
        "busyDays": [],
        "recommendedOrder": [],
        "explanation": "Mock result",
    }
    workload_service.analyze.return_value = mock_result

    # Act
    action = _validated(ActionType.ANALYZE_WORKLOAD, parameters=ANALYZE_PARAMS)
    command = _command(ActionType.ANALYZE_WORKLOAD, parameters=ANALYZE_PARAMS)
    result = executor.execute(_authorized(action), command)

    # Assert: service analyze was called
    workload_service.analyze.assert_called_once()

    # Assert: result comes from service (not reimplemented in executor)
    assert result.status == ExecutionStatus.EXECUTED
    assert result.result_payload["explanation"] == "Mock result"


# --- Test G: Error propagation -----------------------------------------------


def test_analyze_workload_error_propagation(executor, task_module, workload_service):
    """Verify exceptions from repository/service are propagated correctly."""
    # Arrange: repository raises exception
    task_module.find_tasks_by_deadline_range.side_effect = ValueError(
        "Invalid date range"
    )

    # Act
    action = _validated(ActionType.ANALYZE_WORKLOAD, parameters=ANALYZE_PARAMS)
    command = _command(ActionType.ANALYZE_WORKLOAD, parameters=ANALYZE_PARAMS)
    result = executor.execute(_authorized(action), command)

    # Assert: exception converted to EXECUTION_FAILED
    assert result.status == ExecutionStatus.EXECUTION_FAILED
    assert result.action_type == ActionType.ANALYZE_WORKLOAD
    assert "application operation failed" in result.message.lower()


# --- Test H: Existing action regression --------------------------------------


def test_analyze_workload_does_not_break_crud_actions(executor, task_module):
    """Verify adding ANALYZE_WORKLOAD does not affect existing CRUD actions."""
    # Arrange: CREATE_TASK action (existing action)
    create_params = {
        "taskName": "Test Task",
        "description": "Test description",
        "deadline": "2026-10-01T12:00:00Z",
        "priority": "HIGH",
    }
    mock_task = Task(
        id=TASK_ID_1,
        task_name="Test Task",
        deadline=datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc),
        priority=TaskPriority.HIGH,
        status=TaskStatus.TODO,
    )
    task_module.create_task.return_value = mock_task

    # Act
    action = _validated(ActionType.CREATE_TASK, parameters=create_params)
    command = _command(ActionType.CREATE_TASK, parameters=create_params)
    result = executor.execute(_authorized(action), command)

    # Assert: CREATE_TASK still works correctly
    assert result.status == ExecutionStatus.EXECUTED
    assert result.action_type == ActionType.CREATE_TASK
    task_module.create_task.assert_called_once()
