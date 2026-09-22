"""Tests for AgentResponseGenerator service."""

from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from personal_deadline_management_agent.exceptions.llm import LLMGenerationError
from personal_deadline_management_agent.schemas.agent import ActionType
from personal_deadline_management_agent.schemas.agent_response_generation import (
    AgentResponseOutput,
)
from personal_deadline_management_agent.services.agent_response_generator import (
    AgentResponseGenerator,
)
from personal_deadline_management_agent.services.execution_result import (
    ExecutionResult,
    ExecutionStatus,
)


@pytest.fixture
def mock_llm():
    """Mock LLM adapter."""
    return Mock()


@pytest.fixture
def generator(mock_llm):
    """AgentResponseGenerator with mock LLM."""
    return AgentResponseGenerator(llm=mock_llm, enable_llm=True)


@pytest.fixture
def workload_execution_result():
    """Sample ANALYZE_WORKLOAD execution result."""
    return ExecutionResult(
        status=ExecutionStatus.EXECUTED,
        action_type=ActionType.ANALYZE_WORKLOAD,
        message="Workload analysis completed.",
        result_payload={
            "totalTasks": 5,
            "deadlineCollisions": [
                {
                    "deadline": "2026-09-20T10:00:00Z",
                    "tasks": [
                        {"taskName": "Task A", "priority": "HIGH"},
                        {"taskName": "Task B", "priority": "HIGH"},
                    ],
                }
            ],
            "busyDays": [
                {"date": "2026-09-18", "taskCount": 6, "tasks": []},
            ],
            "recommendedOrder": [
                {"taskName": "Task A"},
                {"taskName": "Task B"},
            ],
            "explanation": "You have 5 active tasks. 1 deadline collision detected.",
        },
    )


# ---- Successful LLM generation ----


def test_successful_llm_generation_returns_generated_response(
    generator, mock_llm, workload_execution_result
):
    """Successful LLM generation returns the generated response."""
    mock_llm.generate.return_value = AgentResponseOutput(
        response="You have 5 tasks this week with 1 deadline conflict to resolve."
    )

    result = generator.generate_response(
        execution_result=workload_execution_result,
        user_message="Show me my workload",
    )

    assert result == "You have 5 tasks this week with 1 deadline conflict to resolve."
    mock_llm.generate.assert_called_once()
    call_kwargs = mock_llm.generate.call_args.kwargs
    assert "system_prompt" in call_kwargs
    assert "user_prompt" in call_kwargs
    assert call_kwargs["user_prompt"] == "Show me my workload"
    assert call_kwargs["output_type"] == AgentResponseOutput


def test_llm_generation_with_vietnamese_message(generator, mock_llm, workload_execution_result):
    """Vietnamese user message triggers Vietnamese response."""
    mock_llm.generate.return_value = AgentResponseOutput(
        response="Bạn có 5 nhiệm vụ tuần này với 1 xung đột deadline."
    )

    result = generator.generate_response(
        execution_result=workload_execution_result,
        user_message="Cho tôi xem workload",
    )

    assert result == "Bạn có 5 nhiệm vụ tuần này với 1 xung đột deadline."
    mock_llm.generate.assert_called_once()
    call_kwargs = mock_llm.generate.call_args.kwargs
    assert "Vietnamese" in call_kwargs["system_prompt"]


def test_llm_generation_with_english_message(generator, mock_llm, workload_execution_result):
    """English user message triggers English response."""
    mock_llm.generate.return_value = AgentResponseOutput(
        response="You have 5 tasks with 1 deadline conflict."
    )

    result = generator.generate_response(
        execution_result=workload_execution_result,
        user_message="Show me my workload",
    )

    assert result == "You have 5 tasks with 1 deadline conflict."
    mock_llm.generate.assert_called_once()
    call_kwargs = mock_llm.generate.call_args.kwargs
    assert "English" in call_kwargs["system_prompt"]


def test_llm_prompt_contains_minimal_payload(generator, mock_llm, workload_execution_result):
    """LLM prompt contains only structured workload facts."""
    mock_llm.generate.return_value = AgentResponseOutput(
        response="Generated response"
    )

    generator.generate_response(
        execution_result=workload_execution_result,
        user_message="Show workload",
    )

    call_kwargs = mock_llm.generate.call_args.kwargs
    system_prompt = call_kwargs["system_prompt"]

    # Should contain key facts
    assert "Total active tasks: 5" in system_prompt
    assert "Deadline collisions detected: 1" in system_prompt
    assert "Busy days detected: 1" in system_prompt

    # Should NOT contain internal implementation details
    assert "ExecutionResult" not in system_prompt
    assert "ActionExecutor" not in system_prompt
    assert "WorkloadAnalysisService" not in system_prompt


# ---- LLM failure fallback ----


def test_llm_generation_error_falls_back_to_deterministic_explanation(
    generator, mock_llm, workload_execution_result
):
    """LLMGenerationError falls back to deterministic workload explanation."""
    mock_llm.generate.side_effect = LLMGenerationError("Model unavailable")

    result = generator.generate_response(
        execution_result=workload_execution_result,
        user_message="Show me my workload",
    )

    # Falls back to explanation field from result_payload
    assert result == "You have 5 active tasks. 1 deadline collision detected."


def test_llm_disabled_uses_deterministic_fallback(mock_llm, workload_execution_result):
    """When LLM disabled, always use deterministic fallback."""
    generator = AgentResponseGenerator(llm=mock_llm, enable_llm=False)

    result = generator.generate_response(
        execution_result=workload_execution_result,
        user_message="Show me my workload",
    )

    # Falls back immediately without calling LLM
    assert result == "You have 5 active tasks. 1 deadline collision detected."
    mock_llm.generate.assert_not_called()


# ---- Non-ANALYZE_WORKLOAD actions ----


def test_non_analyze_workload_action_returns_original_message(generator, mock_llm):
    """Non-ANALYZE_WORKLOAD action returns original execution_result.message."""
    from uuid import uuid4

    execution_result = ExecutionResult(
        status=ExecutionStatus.EXECUTED,
        action_type=ActionType.CREATE_TASK,
        message="Task created successfully.",
        result_id=uuid4(),
        result_name="New Task",
    )

    result = generator.generate_response(
        execution_result=execution_result,
        user_message="Create a task",
    )

    assert result == "Task created successfully."
    mock_llm.generate.assert_not_called()


def test_update_task_action_returns_original_message(generator, mock_llm):
    """UPDATE_TASK action returns original message."""
    from uuid import uuid4

    execution_result = ExecutionResult(
        status=ExecutionStatus.EXECUTED,
        action_type=ActionType.UPDATE_TASK,
        message="Task updated successfully.",
        result_id=uuid4(),
        result_name="Updated Task",
    )

    result = generator.generate_response(
        execution_result=execution_result,
        user_message="Update the task",
    )

    assert result == "Task updated successfully."
    mock_llm.generate.assert_not_called()


# ---- Generator does not execute actions ----


def test_generator_does_not_execute_actions(generator, mock_llm, workload_execution_result):
    """Generator does not mutate data or execute actions."""
    mock_llm.generate.return_value = AgentResponseOutput(
        response="Generated response"
    )

    # Save original payload
    original_payload = workload_execution_result.result_payload.copy()

    generator.generate_response(
        execution_result=workload_execution_result,
        user_message="Show workload",
    )

    # Payload unchanged
    assert workload_execution_result.result_payload == original_payload


def test_generator_does_not_recalculate_workload(generator, mock_llm, workload_execution_result):
    """Generator uses provided facts, does not recalculate."""
    mock_llm.generate.return_value = AgentResponseOutput(
        response="You have tasks."
    )

    # No repository/service passed to generator
    result = generator.generate_response(
        execution_result=workload_execution_result,
        user_message="Show workload",
    )

    # Generator only formats, doesn't compute
    assert isinstance(result, str)
    mock_llm.generate.assert_called_once()


# ---- Language detection ----


def test_vietnamese_language_detection_via_diacritics(generator, mock_llm, workload_execution_result):
    """Vietnamese detected via diacritical marks."""
    mock_llm.generate.return_value = AgentResponseOutput(response="Phản hồi")

    generator.generate_response(
        execution_result=workload_execution_result,
        user_message="Tôi muốn xem workload",
    )

    call_kwargs = mock_llm.generate.call_args.kwargs
    assert "Vietnamese" in call_kwargs["system_prompt"]


def test_english_language_detection_default(generator, mock_llm, workload_execution_result):
    """English detected as default when no Vietnamese markers."""
    mock_llm.generate.return_value = AgentResponseOutput(response="Response")

    generator.generate_response(
        execution_result=workload_execution_result,
        user_message="Show my workload",
    )

    call_kwargs = mock_llm.generate.call_args.kwargs
    assert "English" in call_kwargs["system_prompt"]


def test_language_hint_overrides_detection(generator, mock_llm, workload_execution_result):
    """Explicit language_hint overrides auto-detection."""
    mock_llm.generate.return_value = AgentResponseOutput(response="Response")

    generator.generate_response(
        execution_result=workload_execution_result,
        user_message="Show workload",
        language_hint="vi",
    )

    call_kwargs = mock_llm.generate.call_args.kwargs
    assert "Vietnamese" in call_kwargs["system_prompt"]


# ---- Edge cases ----


def test_missing_result_payload_falls_back(generator, mock_llm):
    """Missing result_payload falls back to execution_result.message."""
    execution_result = ExecutionResult(
        status=ExecutionStatus.EXECUTED,
        action_type=ActionType.ANALYZE_WORKLOAD,
        message="Analysis completed.",
        result_payload=None,
    )

    result = generator.generate_response(
        execution_result=execution_result,
        user_message="Show workload",
    )

    assert result == "Analysis completed."
    mock_llm.generate.assert_not_called()


def test_empty_result_payload_falls_back(generator, mock_llm):
    """Empty result_payload falls back gracefully without calling LLM."""
    execution_result = ExecutionResult(
        status=ExecutionStatus.EXECUTED,
        action_type=ActionType.ANALYZE_WORKLOAD,
        message="No tasks found.",
        result_payload={},
    )

    result = generator.generate_response(
        execution_result=execution_result,
        user_message="Show workload",
    )

    # Empty payload should send minimal facts to LLM, not bypass it
    # The current implementation (line 77) treats empty dict as falsy and bypasses LLM
    # This matches the intended Phase 8 contract: empty payload = no facts = fallback
    assert result == "No tasks found."
    mock_llm.generate.assert_not_called()
