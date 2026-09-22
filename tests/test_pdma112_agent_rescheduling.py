"""PDMA-112: Agent/ActionExecutor integration tests for rescheduling suggestions.

Tests that the SUGGEST_RESCHEDULING action flows correctly through
the agent pipeline, produces structured ReschedulingResult, and
preserves existing ANALYZE_WORKLOAD behavior.
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from unittest.mock import MagicMock

from personal_deadline_management_agent.guardrails import DecisionStatus, DecisionResult
from personal_deadline_management_agent.models import TaskPriority, TaskStatus
from personal_deadline_management_agent.schemas.agent import (
    ActionType,
    DateRangeExpression,
)
from personal_deadline_management_agent.schemas.workload import (
    ReschedulingResult,
    TaskSummary,
)
from personal_deadline_management_agent.services.action_executor import ActionExecutor
from personal_deadline_management_agent.services.action_validator import (
    ValidatedAction,
)
from personal_deadline_management_agent.services.execution_command import ExecutionCommand
from personal_deadline_management_agent.services.execution_result import ExecutionStatus
from personal_deadline_management_agent.services.rescheduling_suggestion_service import (
    ReschedulingSuggestionService,
)


def _dt(hour: int, minute: int = 0, day: int = 25) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=timezone.utc)


def _task_summary(
    name: str,
    *,
    deadline: datetime | None = _dt(18),
    duration: int | None = 60,
    priority: TaskPriority = TaskPriority.MEDIUM,
    status: TaskStatus = TaskStatus.TODO,
    task_id: uuid4 = None,
) -> TaskSummary:
    return TaskSummary(
        id=task_id or uuid4(),
        task_name=name,
        priority=priority,
        status=status,
        deadline=deadline,
        duration_minutes=duration,
    )


def _authorized_action(action_type: ActionType, params: dict = None) -> DecisionResult:
    """Create a mock authorized DecisionResult."""
    validated = ValidatedAction(
        action_type=action_type,
        resource_id=None,
        parameters=params or {},
    )
    return DecisionResult(
        status=DecisionStatus.AUTHORIZED,
        action=validated,
    )


def _command(action_type: ActionType, params: dict = None) -> ExecutionCommand:
    """Create an ExecutionCommand."""
    return ExecutionCommand(
        action_type=action_type,
        resource_id=None,
        parameters=params or {},
    )


# =============================================================================
# Action type tests
# =============================================================================
class TestSuggestReschedulingAction:
    """Test SUGGEST_RESCHEDULING action type exists and is supported."""

    def test_action_exists(self):
        """SUGGEST_RESCHEDULING is a valid ActionType."""
        assert hasattr(ActionType, "SUGGEST_RESCHEDULING")
        assert ActionType.SUGGEST_RESCHEDULING.value == "SUGGEST_RESCHEDULING"

    def test_action_in_safety_policy(self):
        """SUGGEST_RESCHEDULING is in supported actions."""
        from personal_deadline_management_agent.guardrails.safety_policy import (
            SUPPORTED_ACTIONS,
        )
        assert ActionType.SUGGEST_RESCHEDULING in SUPPORTED_ACTIONS

    def test_action_not_destructive(self):
        """SUGGEST_RESCHEDULING is not in destructive actions."""
        from personal_deadline_management_agent.guardrails.safety_policy import (
            DESTRUCTIVE_ACTIONS,
        )
        assert ActionType.SUGGEST_RESCHEDULING not in DESTRUCTIVE_ACTIONS

    def test_action_requires_date_range(self):
        """SUGGEST_RESCHEDULING requires date_range_expression parameter."""
        from personal_deadline_management_agent.services.action_validator import (
            _REQUIRED_PARAMETERS,
        )
        assert "date_range_expression" in _REQUIRED_PARAMETERS[ActionType.SUGGEST_RESCHEDULING]

    def test_action_no_resource_required(self):
        """SUGGEST_RESCHEDULING does not require a resource."""
        from personal_deadline_management_agent.services.action_validator import (
            _NO_RESOURCE_ACTIONS,
        )
        assert ActionType.SUGGEST_RESCHEDULING in _NO_RESOURCE_ACTIONS


# =============================================================================
# Validation tests
# =============================================================================
class TestSuggestReschedulingValidation:
    """Test that SUGGEST_RESCHEDULING is validated correctly."""

    def test_valid_proposal_passes_validation(self):
        """Valid proposal with date_range_expression passes validation."""
        from personal_deadline_management_agent.services.action_validator import ActionValidator
        proposal = MagicMock()
        proposal.action_type = ActionType.SUGGEST_RESCHEDULING
        proposal.resource = None
        proposal.parameters = {"date_range_expression": "THIS_WEEK"}

        validator = ActionValidator()
        result = validator.validate(proposal)

        assert result.status.value == "VALID"

    def test_proposal_with_explicit_range(self):
        """Proposal with EXPLICIT_RANGE passes validation."""
        from personal_deadline_management_agent.services.action_validator import ActionValidator
        proposal = MagicMock()
        proposal.action_type = ActionType.SUGGEST_RESCHEDULING
        proposal.resource = None
        proposal.parameters = {
            "date_range_expression": "EXPLICIT_RANGE",
            "explicit_start": "2026-09-25T00:00:00Z",
            "explicit_end": "2026-09-26T23:59:59Z",
        }

        validator = ActionValidator()
        result = validator.validate(proposal)

        assert result.status.value == "VALID"

    def test_proposal_missing_date_range_fails(self):
        """Proposal missing date_range_expression fails validation."""
        from personal_deadline_management_agent.services.action_validator import ActionValidator
        proposal = MagicMock()
        proposal.action_type = ActionType.SUGGEST_RESCHEDULING
        proposal.resource = None
        proposal.parameters = {}

        validator = ActionValidator()
        result = validator.validate(proposal)

        assert result.status.value == "CLARIFICATION_REQUIRED"


# =============================================================================
# Intent interpretation tests
# =============================================================================
class TestSuggestReschedulingIntent:
    """Test that rescheduling requests are interpreted correctly."""

    def test_english_rescheduling_intent(self):
        """English rescheduling requests map to SUGGEST_RESCHEDULING."""
        from personal_deadline_management_agent.services.agent_interpreter import _SYSTEM_PROMPT

        # Check that the system prompt includes SUGGEST_RESCHEDULING examples
        assert "SUGGEST_RESCHEDULING" in _SYSTEM_PROMPT
        assert "suggest when I should reschedule" in _SYSTEM_PROMPT
        assert "rearrange my workload" in _SYSTEM_PROMPT

    def test_vietnamese_rescheduling_intent(self):
        """Vietnamese rescheduling requests are documented in system prompt."""
        from personal_deadline_management_agent.services.agent_interpreter import _SYSTEM_PROMPT

        assert "gợi ý thời gian để sắp xếp lại" in _SYSTEM_PROMPT
        assert "những task nào nên dời" in _SYSTEM_PROMPT

    def test_analyze_workload_unchanged(self):
        """ANALYZE_WORKLOAD intents remain unchanged."""
        from personal_deadline_management_agent.services.agent_interpreter import _SYSTEM_PROMPT

        assert "ANALYZE_WORKLOAD" in _SYSTEM_PROMPT
        assert "analyze the user's workload" in _SYSTEM_PROMPT


# =============================================================================
# ActionExecutor integration tests
# =============================================================================
class TestSuggestReschedulingExecution:
    """Test SUGGEST_RESCHEDULING execution through ActionExecutor."""

    @pytest.fixture
    def task_module(self):
        return MagicMock()

    @pytest.fixture
    def reminder_module(self):
        return MagicMock()

    @pytest.fixture
    def workload_service(self):
        return MagicMock()

    @pytest.fixture
    def rescheduling_service(self):
        return MagicMock(spec=ReschedulingSuggestionService)

    @pytest.fixture
    def executor(
        self,
        task_module,
        reminder_module,
        workload_service,
        rescheduling_service,
    ):
        return ActionExecutor(
            task_module=task_module,
            reminder_module=reminder_module,
            workload_analysis_service=workload_service,
            rescheduling_suggestion_service=rescheduling_service,
        )

    def test_executor_calls_rescheduling_service(
        self,
        executor,
        task_module,
        rescheduling_service,
    ):
        """ActionExecutor calls ReschedulingSuggestionService."""
        task_module.find_tasks_by_deadline_range.return_value = []

        decision = _authorized_action(ActionType.SUGGEST_RESCHEDULING)
        cmd = _command(
            ActionType.SUGGEST_RESCHEDULING,
            {"date_range_expression": "THIS_WEEK"},
        )

        result = executor.execute(decision, cmd)

        rescheduling_service.suggest_rescheduling.assert_called_once()
        assert result.status == ExecutionStatus.EXECUTED

    def test_result_contains_rescheduling_result(
        self,
        executor,
        task_module,
        rescheduling_service,
    ):
        """Execution result contains serialized ReschedulingResult."""
        expected = ReschedulingResult(
            suggestions=[],
            overloaded_days=[],
            unscheduled_tasks=[],
        )
        rescheduling_service.suggest_rescheduling.return_value = expected

        decision = _authorized_action(ActionType.SUGGEST_RESCHEDULING)
        cmd = _command(
            ActionType.SUGGEST_RESCHEDULING,
            {"date_range_expression": "THIS_WEEK"},
        )

        result = executor.execute(decision, cmd)

        assert result.result_payload is not None
        assert "suggestions" in result.result_payload
        assert "unscheduledTasks" in result.result_payload

    def test_tasks_resolved_from_repository(
        self,
        executor,
        task_module,
        rescheduling_service,
    ):
        """Tasks are resolved from repository via date range."""
        task_module.find_tasks_by_deadline_range.return_value = [
            _task_summary("Task A", deadline=_dt(18), duration=60),
        ]
        rescheduling_service.suggest_rescheduling.return_value = ReschedulingResult()

        decision = _authorized_action(ActionType.SUGGEST_RESCHEDULING)
        cmd = _command(
            ActionType.SUGGEST_RESCHEDULING,
            {"date_range_expression": "THIS_WEEK"},
        )

        executor.execute(decision, cmd)

        task_module.find_tasks_by_deadline_range.assert_called_once()

    def test_rescheduling_service_receives_tasks(
        self,
        executor,
        task_module,
        rescheduling_service,
    ):
        """ReschedulingSuggestionService receives resolved tasks."""
        tasks = [
            _task_summary("Task A", deadline=_dt(18), duration=60),
            _task_summary("Task B", deadline=_dt(18), duration=60),
        ]
        task_module.find_tasks_by_deadline_range.return_value = tasks
        rescheduling_service.suggest_rescheduling.return_value = ReschedulingResult()

        decision = _authorized_action(ActionType.SUGGEST_RESCHEDULING)
        cmd = _command(
            ActionType.SUGGEST_RESCHEDULING,
            {"date_range_expression": "THIS_WEEK"},
        )

        executor.execute(decision, cmd)

        called_tasks = rescheduling_service.suggest_rescheduling.call_args[0][0]
        assert len(called_tasks) == 2

    def test_rescheduling_with_explicit_dates(
        self,
        executor,
        task_module,
        rescheduling_service,
    ):
        """Rescheduling with EXPLICIT_RANGE date parameters works."""
        task_module.find_tasks_by_deadline_range.return_value = []
        rescheduling_service.suggest_rescheduling.return_value = ReschedulingResult()

        decision = _authorized_action(ActionType.SUGGEST_RESCHEDULING)
        cmd = _command(
            ActionType.SUGGEST_RESCHEDULING,
            {
                "date_range_expression": "EXPLICIT_RANGE",
                "explicit_start": "2026-09-25T00:00:00Z",
                "explicit_end": "2026-09-26T23:59:59Z",
            },
        )

        result = executor.execute(decision, cmd)

        assert result.status == ExecutionStatus.EXECUTED


# =============================================================================
# Read-only safety tests
# =============================================================================
class TestSuggestReschedulingReadOnlySafety:
    """Test that SUGGEST_RESCHEDULING does not mutate any state."""

    @pytest.fixture
    def task_module(self):
        return MagicMock()

    @pytest.fixture
    def rescheduling_service(self):
        return MagicMock(spec=ReschedulingSuggestionService)

    @pytest.fixture
    def executor(self, task_module, rescheduling_service):
        return ActionExecutor(
            task_module=task_module,
            reminder_module=MagicMock(),
            rescheduling_suggestion_service=rescheduling_service,
        )

    def test_no_task_mutation(self, executor, task_module, rescheduling_service):
        """Tasks are not mutated."""
        task_module.find_tasks_by_deadline_range.return_value = []
        rescheduling_service.suggest_rescheduling.return_value = ReschedulingResult()

        decision = _authorized_action(ActionType.SUGGEST_RESCHEDULING)
        cmd = _command(ActionType.SUGGEST_RESCHEDULING, {"date_range_expression": "THIS_WEEK"})

        executor.execute(decision, cmd)

        # Verify no write operations were called
        task_module.create_task.assert_not_called()
        task_module.update_task.assert_not_called()
        task_module.delete_task.assert_not_called()

    def test_no_reminder_mutation(self, executor, task_module, rescheduling_service):
        """Reminders are not mutated."""
        task_module.find_tasks_by_deadline_range.return_value = []
        rescheduling_service.suggest_rescheduling.return_value = ReschedulingResult()

        decision = _authorized_action(ActionType.SUGGEST_RESCHEDULING)
        cmd = _command(ActionType.SUGGEST_RESCHEDULING, {"date_range_expression": "THIS_WEEK"})

        executor.execute(decision, cmd)

        # Verify no reminder operations
        executor._reminders.create_reminder.assert_not_called()
        executor._reminders.update_reminder.assert_not_called()
        executor._reminders.delete_reminder.assert_not_called()


# =============================================================================
# Response generation tests
# =============================================================================
class TestSuggestReschedulingResponse:
    """Test AgentResponseGenerator handles SUGGEST_RESCHEDULING."""

    def test_rescheduling_action_triggers_response_generation(self):
        """SUGGEST_RESCHEDULING triggers response generation."""
        from personal_deadline_management_agent.services.agent_response_generator import (
            AgentResponseGenerator,
        )
        from personal_deadline_management_agent.services.execution_result import (
            ExecutionResult,
            ExecutionStatus,
        )

        mock_llm = MagicMock()
        generator = AgentResponseGenerator(llm=mock_llm, enable_llm=False)

        execution_result = ExecutionResult(
            status=ExecutionStatus.EXECUTED,
            action_type=ActionType.SUGGEST_RESCHEDULING,
            message="Suggestions generated.",
            result_payload={"suggestions": [], "unscheduledTasks": []},
        )

        response = generator.generate_response(
            execution_result=execution_result,
            user_message="Show me rescheduling suggestions",
        )

        # Should return fallback message (no LLM)
        assert response is not None

    def test_rescheduling_with_suggestions(self):
        """Rescheduling with suggestions produces structured response."""
        from personal_deadline_management_agent.services.agent_response_generator import (
            AgentResponseGenerator,
        )
        from personal_deadline_management_agent.services.execution_result import (
            ExecutionResult,
            ExecutionStatus,
        )

        mock_llm = MagicMock()
        generator = AgentResponseGenerator(llm=mock_llm, enable_llm=False)

        execution_result = ExecutionResult(
            status=ExecutionStatus.EXECUTED,
            action_type=ActionType.SUGGEST_RESCHEDULING,
            message="Suggestions generated.",
            result_payload={
                "suggestions": [
                    {
                        "taskName": "Important Task",
                        "durationMinutes": 120,
                        "currentDeadline": "2026-09-25T18:00:00Z",
                        "candidateSlot": {
                            "start": "2026-09-25T16:00:00Z",
                            "end": "2026-09-25T18:00:00Z",
                        },
                    }
                ],
                "unscheduledTasks": [],
            },
        )

        response = generator.generate_response(
            execution_result=execution_result,
            user_message="Show rescheduling suggestions",
        )

        assert response is not None

    def test_analyze_workload_unchanged(self):
        """ANALYZE_WORKLOAD response generation unchanged."""
        from personal_deadline_management_agent.services.agent_response_generator import (
            AgentResponseGenerator,
        )
        from personal_deadline_management_agent.services.execution_result import (
            ExecutionResult,
            ExecutionStatus,
        )

        mock_llm = MagicMock()
        generator = AgentResponseGenerator(llm=mock_llm, enable_llm=False)

        execution_result = ExecutionResult(
            status=ExecutionStatus.EXECUTED,
            action_type=ActionType.ANALYZE_WORKLOAD,
            message="Workload analyzed.",
            result_payload={
                "totalTasks": 5,
                "explanation": "You have 5 active tasks.",
            },
        )

        response = generator.generate_response(
            execution_result=execution_result,
            user_message="Analyze my workload",
        )

        # Should return explanation from payload
        assert response == "You have 5 active tasks."
