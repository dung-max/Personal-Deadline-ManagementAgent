"""Action execution boundary.

Translates an already validated and authorized :class:`ExecutionCommand` into
the corresponding existing application use case.  The executor itself performs
no business validation, no direct repository access, no SQL, and no
commit/rollback — transaction ownership stays with the Module/UoW.

```text
DecisionResult (AUTHORIZED)
  ↓
ExecutionCommand
  ↓
ActionExecutor
  ↓
TaskModule / ReminderModule
  ↓
TaskService / ReminderService
  ↓
Repository
  ↓
UoW
  ↓
Database
```
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from ..exceptions.reminder import InvalidReminderError, ReminderNotFoundError
from ..exceptions.task import InvalidTaskError, TaskNotFoundError
from ..guardrails.safety_policy import DecisionResult, DecisionStatus
from ..models import TaskPriority
from ..modules.reminder_module import ReminderModule
from ..modules.task_module import TaskModule
from ..schemas.agent import ActionType
from ..schemas.workload import WorkloadAnalysisResult
from ..utils.datetime_utils import (
    NaiveDateTimeError,
    parse_iso_datetime,
    require_aware_utc,
)
from .date_range_resolver import DateRangeResolver
from .execution_command import ExecutionCommand
from .execution_result import ExecutionErrorCode, ExecutionResult, ExecutionStatus
from .workload_analysis_service import WorkloadAnalysisService

logger = logging.getLogger(__name__)


# --- datetime coercion -------------------------------------------------------


def _to_datetime(value: Any) -> datetime:
    """Convert a parameter value to a timezone-aware UTC datetime.

    Accepts a ``datetime`` (pass-through), or an ISO-8601 string.  Naive
    datetimes are rejected — never silently assumed to be UTC.  Raises
    ``TypeError``/``ValueError``/``NaiveDateTimeError`` on bad input so the
    caller can catch and produce a deterministic result.
    """
    if isinstance(value, datetime):
        return require_aware_utc(value)
    if isinstance(value, str):
        return parse_iso_datetime(value)
    raise TypeError(f"Expected datetime or ISO-8601 string, got {type(value).__name__}")


def _opt_datetime(value: Any) -> datetime | None:
    """Like ``_to_datetime`` but returns ``None`` for ``None``."""
    if value is None:
        return None
    return _to_datetime(value)


# --- Result helpers ----------------------------------------------------------


def _error_code_for(exc: Exception) -> ExecutionErrorCode:
    """Map an application exception to a safe, deterministic error category."""
    if isinstance(exc, (TaskNotFoundError, ReminderNotFoundError)):
        return ExecutionErrorCode.NOT_FOUND
    if isinstance(
        exc,
        (InvalidTaskError, InvalidReminderError, NaiveDateTimeError, ValueError),
    ):
        return ExecutionErrorCode.INVALID_INPUT
    return ExecutionErrorCode.INFRASTRUCTURE


def _entity_payload(entity: Any) -> tuple[UUID | None, str | None]:
    """Extract the minimal safe payload (id, name) from a domain entity.

    Only the fields needed by the Agent contract are returned — never the
    full ORM object.  Values that are not a UUID/str are dropped so a
    malformed entity cannot leak into the result.
    """
    if entity is None:
        return None, None
    entity_id = getattr(entity, "id", None)
    entity_name = getattr(entity, "task_name", None)
    if not isinstance(entity_id, UUID):
        entity_id = None
    if not isinstance(entity_name, str):
        entity_name = None
    return entity_id, entity_name


# --- Executor ----------------------------------------------------------------


class ActionExecutor:
    """Executes an authorized execution command through the application layer.

    Dependencies are injected explicitly — no global state, no DI framework.
    The executor routes to the existing Module/Service/UoW architecture and
    never bypasses it.
    """

    def __init__(
        self,
        *,
        task_module: TaskModule,
        reminder_module: ReminderModule,
        workload_analysis_service: WorkloadAnalysisService | None = None,
        date_range_resolver: DateRangeResolver | None = None,
    ) -> None:
        self._tasks = task_module
        self._reminders = reminder_module
        self._workload_service = workload_analysis_service or WorkloadAnalysisService()
        self._date_resolver = date_range_resolver or DateRangeResolver()

    def execute(
        self,
        decision: DecisionResult,
        command: ExecutionCommand,
    ) -> ExecutionResult:
        """Attempt to execute an authorized execution command.

        The *decision* must have ``status == AUTHORIZED``; any other status
        results in ``NOT_EXECUTABLE``.  Application-layer exceptions are
        caught, logged with safe context, and converted to
        ``EXECUTION_FAILED`` with a deterministic, safe message and an
        ``error_code`` category.
        """
        if decision.status is not DecisionStatus.AUTHORIZED:
            return ExecutionResult(
                status=ExecutionStatus.NOT_EXECUTABLE,
                action_type=command.action_type,
                resource_id=command.resource_id,
                message=(
                    "Execution requires an AUTHORIZED decision; "
                    f"got {decision.status.value}."
                ),
            )

        # --- Consistency guard: command must match the decision's action -----
        # Insurance only — ExecutionCommand.from_decision() is the supported
        # way to build a command, so this should never trigger in practice.
        if command.action_type != decision.action.action_type:
            return ExecutionResult(
                status=ExecutionStatus.NOT_EXECUTABLE,
                action_type=command.action_type,
                resource_id=command.resource_id,
                message=(
                    "Execution command does not match the authorized action type."
                ),
            )

        try:
            entity = self._dispatch(command)
        except Exception as exc:
            # Log safe context only — never parameters (may contain
            # sensitive data) and never the raw exception in the result.
            logger.exception(
                "Action execution failed: action_type=%s resource_id=%s",
                command.action_type,
                command.resource_id,
            )
            return ExecutionResult(
                status=ExecutionStatus.EXECUTION_FAILED,
                action_type=command.action_type,
                resource_id=command.resource_id,
                message="The application operation failed.",
                error_code=_error_code_for(exc),
            )
        return self._executed(command, entity)

    # --- Routing ------------------------------------------------------------

    def _dispatch(self, command: ExecutionCommand) -> Any:
        """Route the command to the correct module use case.

        Returns the domain entity produced by the use case (``None`` for
        deletes).  The executor never touches repositories, sessions, or
        transactions directly.
        """
        action = command.action_type
        params = command.parameters

        if action is ActionType.CREATE_TASK:
            return self._tasks.create_task(
                task_name=params["taskName"],
                description=params.get("description"),
                deadline=_to_datetime(params["deadline"]),
                priority=params.get("priority", TaskPriority.MEDIUM),
            )
        if action is ActionType.UPDATE_TASK:
            return self._tasks.update_task(
                task_id=command.resource_id,  # type: ignore[arg-type]
                task_name=params.get("taskName"),
                description=params.get("description"),
                deadline=_opt_datetime(params.get("deadline")),
                priority=params.get("priority"),
                status=params.get("status"),
            )
        if action is ActionType.DELETE_TASK:
            self._tasks.delete_task(task_id=command.resource_id)  # type: ignore[arg-type]
            return None
        if action is ActionType.CREATE_REMINDER:
            return self._reminders.create_reminder(
                task_id=command.resource_id,  # type: ignore[arg-type]
                remind_at=_to_datetime(params["remindAt"]),
            )
        if action is ActionType.UPDATE_REMINDER:
            return self._reminders.update_reminder(
                reminder_id=command.resource_id,  # type: ignore[arg-type]
                remind_at=_opt_datetime(params.get("remindAt")),
                status=params.get("status"),
            )
        if action is ActionType.DELETE_REMINDER:
            self._reminders.delete_reminder(
                reminder_id=command.resource_id,  # type: ignore[arg-type]
            )
            return None
        if action is ActionType.ANALYZE_WORKLOAD:
            # Resolve date range from parameters
            from ..schemas.agent import DateRangeExpression

            # Convert string expression to enum
            expr_str = params["date_range_expression"]
            expression = DateRangeExpression(expr_str) if isinstance(expr_str, str) else expr_str

            # Convert ISO string dates to datetime if present
            explicit_start = _opt_datetime(params.get("explicit_start"))
            explicit_end = _opt_datetime(params.get("explicit_end"))

            start, end = self._date_resolver.resolve(
                expression=expression,
                explicit_start=explicit_start,
                explicit_end=explicit_end,
            )
            # Query tasks in the resolved range
            tasks = self._tasks.find_tasks_by_deadline_range(start, end)
            # Delegate analysis to the workload service
            return self._workload_service.analyze(tasks)
        # Unsupported action type — should never reach here when gated by
        # SafetyPolicy, but fail safely if it does.
        raise ValueError(f"Unsupported action type: {action}")

    # --- Result helpers -----------------------------------------------------

    def _executed(self, command: ExecutionCommand, entity: Any) -> ExecutionResult:
        """Build the EXECUTED result with the minimal safe payload."""
        # Special handling for ANALYZE_WORKLOAD result
        if isinstance(entity, WorkloadAnalysisResult):
            return ExecutionResult(
                status=ExecutionStatus.EXECUTED,
                action_type=command.action_type,
                resource_id=command.resource_id,
                message="Workload analysis completed successfully.",
                result_payload=entity.model_dump(),
            )

        result_id, result_name = _entity_payload(entity)
        return ExecutionResult(
            status=ExecutionStatus.EXECUTED,
            action_type=command.action_type,
            resource_id=command.resource_id,
            message="Action executed successfully.",
            result_id=result_id,
            result_name=result_name,
        )
