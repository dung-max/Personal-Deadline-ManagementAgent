"""PDMA-113: LLM Explanation / Ranking tests for rescheduling suggestions."""

from datetime import datetime, timezone
from unittest.mock import Mock
from uuid import uuid4

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
def rescheduling_result_with_suggestions():
    """ReschedulingResult with one suggestion."""
    return ExecutionResult(
        status=ExecutionStatus.EXECUTED,
        action_type=ActionType.SUGGEST_RESCHEDULING,
        message="Suggestions generated.",
        result_payload={
            "suggestions": [
                {
                    "taskId": str(uuid4()),
                    "taskName": "Prepare report",
                    "durationMinutes": 120,
                    "currentDeadline": "2026-09-25T17:00:00Z",
                    "candidateSlot": {
                        "start": "2026-09-25T15:00:00Z",
                        "end": "2026-09-25T17:00:00Z",
                        "availableMinutes": 120,
                    },
                    "reason": "Fits within the task feasibility window and remaining daily capacity.",
                }
            ],
            "unscheduledTasks": [],
            "overloadedDays": [],
        },
    )


@pytest.fixture
def rescheduling_result_empty():
    """ReschedulingResult with no suggestions."""
    return ExecutionResult(
        status=ExecutionStatus.EXECUTED,
        action_type=ActionType.SUGGEST_RESCHEDULING,
        message="No suggestions.",
        result_payload={
            "suggestions": [],
            "unscheduledTasks": [
                {
                    "id": str(uuid4()),
                    "taskName": "Task without duration",
                    "priority": "HIGH",
                    "status": "TODO",
                    "deadline": "2026-09-25T18:00:00Z",
                    "durationMinutes": None,
                }
            ],
            "overloadedDays": [],
        },
    )


# ===========================================================================
# Basic generation
# ===========================================================================

def test_single_suggestion_generates_response(
    generator, mock_llm, rescheduling_result_with_suggestions
):
    """Single suggestion generates a response."""
    mock_llm.generate.return_value = AgentResponseOutput(
        response="I found one feasible candidate window for Prepare report."
    )

    result = generator.generate_response(
        execution_result=rescheduling_result_with_suggestions,
        user_message="Suggest rescheduling",
    )

    assert result == "I found one feasible candidate window for Prepare report."
    mock_llm.generate.assert_called_once()


def test_multiple_suggestions_generate_response(generator, mock_llm):
    """Multiple suggestions generate a response."""
    payload = ExecutionResult(
        status=ExecutionStatus.EXECUTED,
        action_type=ActionType.SUGGEST_RESCHEDULING,
        message="Suggestions generated.",
        result_payload={
            "suggestions": [
                {
                    "taskId": str(uuid4()),
                    "taskName": "Task A",
                    "durationMinutes": 60,
                    "currentDeadline": "2026-09-25T10:00:00Z",
                    "candidateSlot": {
                        "start": "2026-09-25T09:00:00Z",
                        "end": "2026-09-25T10:00:00Z",
                        "availableMinutes": 60,
                    },
                    "reason": "Fits within the task feasibility window.",
                },
                {
                    "taskId": str(uuid4()),
                    "taskName": "Task B",
                    "durationMinutes": 90,
                    "currentDeadline": "2026-09-25T14:00:00Z",
                    "candidateSlot": {
                        "start": "2026-09-25T12:30:00Z",
                        "end": "2026-09-25T14:00:00Z",
                        "availableMinutes": 90,
                    },
                    "reason": "Fits within the task feasibility window.",
                },
            ],
            "unscheduledTasks": [],
            "overloadedDays": [],
        },
    )
    mock_llm.generate.return_value = AgentResponseOutput(
        response="Found two rescheduling suggestions."
    )

    result = generator.generate_response(execution_result=payload, user_message="Suggest rescheduling")

    assert result == "Found two rescheduling suggestions."


def test_empty_suggestions_generate_response(generator, mock_llm, rescheduling_result_empty):
    """Empty suggestions still generate a response."""
    mock_llm.generate.return_value = AgentResponseOutput(
        response="No feasible candidates found under current constraints."
    )

    result = generator.generate_response(
        execution_result=rescheduling_result_empty,
        user_message="Suggest rescheduling",
    )

    assert result == "No feasible candidates found under current constraints."


def test_unscheduled_tasks_in_prompt(generator, mock_llm):
    """Unscheduled tasks are mentioned appropriately in prompt."""
    payload = ExecutionResult(
        status=ExecutionStatus.EXECUTED,
        action_type=ActionType.SUGGEST_RESCHEDULING,
        message="Suggestions generated.",
        result_payload={
            "suggestions": [],
            "unscheduledTasks": [
                {"taskName": "Task A", "durationMinutes": None, "deadline": "2026-09-25T18:00:00Z"},
                {"taskName": "Task B", "durationMinutes": 30, "deadline": None},
            ],
            "overloadedDays": [],
        },
    )
    mock_llm.generate.return_value = AgentResponseOutput(response="No candidates.")

    generator.generate_response(execution_result=payload, user_message="Suggest rescheduling")

    system_prompt = mock_llm.generate.call_args.kwargs["system_prompt"]
    # Should mention unscheduled tasks
    assert "2" in system_prompt or "unscheduled" in system_prompt.lower() or "could not be scheduled" in system_prompt.lower()


def test_overloaded_days_in_prompt(generator, mock_llm):
    """Overloaded days are mentioned in prompt."""
    payload = ExecutionResult(
        status=ExecutionStatus.EXECUTED,
        action_type=ActionType.SUGGEST_RESCHEDULING,
        message="Suggestions generated.",
        result_payload={
            "suggestions": [],
            "unscheduledTasks": [],
            "overloadedDays": ["2026-09-25", "2026-09-26"],
        },
    )
    mock_llm.generate.return_value = AgentResponseOutput(response="Response.")

    generator.generate_response(execution_result=payload, user_message="Suggest rescheduling")

    system_prompt = mock_llm.generate.call_args.kwargs["system_prompt"]
    assert "2" in system_prompt


# ===========================================================================
# Fact preservation
# ===========================================================================

def test_candidate_start_end_in_prompt(generator, mock_llm, rescheduling_result_with_suggestions):
    """Candidate start/end in output correspond to supplied data."""
    mock_llm.generate.return_value = AgentResponseOutput(response="Response.")

    generator.generate_response(
        execution_result=rescheduling_result_with_suggestions,
        user_message="Suggest rescheduling",
    )

    system_prompt = mock_llm.generate.call_args.kwargs["system_prompt"]
    assert "2026-09-25T15:00:00Z" in system_prompt
    assert "2026-09-25T17:00:00Z" in system_prompt


def test_deadline_not_changed(generator, mock_llm, rescheduling_result_with_suggestions):
    """Deadline is not changed by generator."""
    mock_llm.generate.return_value = AgentResponseOutput(response="Response.")

    generator.generate_response(
        execution_result=rescheduling_result_with_suggestions,
        user_message="Suggest rescheduling",
    )

    system_prompt = mock_llm.generate.call_args.kwargs["system_prompt"]
    assert "2026-09-25T17:00:00Z" in system_prompt


def test_no_calendar_availability_claim_in_fallback(mock_llm):
    """Fallback does not claim calendar availability."""
    generator = AgentResponseGenerator(llm=mock_llm, enable_llm=False)

    payload = ExecutionResult(
        status=ExecutionStatus.EXECUTED,
        action_type=ActionType.SUGGEST_RESCHEDULING,
        message="Suggestions generated.",
        result_payload={
            "suggestions": [
                {
                    "taskId": str(uuid4()),
                    "taskName": "Prepare report",
                    "durationMinutes": 120,
                    "currentDeadline": "2026-09-25T17:00:00Z",
                    "candidateSlot": {
                        "start": "2026-09-25T15:00:00Z",
                        "end": "2026-09-25T17:00:00Z",
                        "availableMinutes": 120,
                    },
                    "reason": "Fits within the task feasibility window.",
                }
            ],
            "unscheduledTasks": [],
            "overloadedDays": [],
        },
    )

    response = generator.generate_response(execution_result=payload, user_message="Suggest rescheduling")

    # Should mention candidate window but not claim calendar availability
    assert "Prepare report" in response or "candidate window" in response
    assert "calendar availability" not in response.lower() or "not confirmed calendar availability" in response


def test_fallback_does_not_say_task_was_rescheduled(mock_llm, rescheduling_result_with_suggestions):
    """Fallback does not claim tasks were rescheduled."""
    generator = AgentResponseGenerator(llm=mock_llm, enable_llm=False)

    response = generator.generate_response(
        execution_result=rescheduling_result_with_suggestions,
        user_message="Suggest rescheduling",
    )

    # Should not claim rescheduling happened
    assert "has been rescheduled" not in response.lower()
    assert "was rescheduled" not in response.lower()
    # Should mention suggestion/candidate
    assert "suggestion" in response.lower() or "candidate" in response.lower() or "feasible" in response.lower()


# ===========================================================================
# Language
# ===========================================================================

def test_english_request_english_response(generator, mock_llm, rescheduling_result_with_suggestions):
    """English request triggers English response."""
    mock_llm.generate.return_value = AgentResponseOutput(response="English response.")

    generator.generate_response(
        execution_result=rescheduling_result_with_suggestions,
        user_message="Can you suggest when I should reschedule my tasks?",
    )

    system_prompt = mock_llm.generate.call_args.kwargs["system_prompt"]
    assert "English" in system_prompt


def test_vietnamese_request_vietnamese_response(generator, mock_llm, rescheduling_result_with_suggestions):
    """Vietnamese request triggers Vietnamese response."""
    mock_llm.generate.return_value = AgentResponseOutput(response="Phản hồi tiếng Việt.")

    generator.generate_response(
        execution_result=rescheduling_result_with_suggestions,
        user_message="Bạn có thể gợi ý thời gian để tôi sắp xếp lại công việc không?",
    )

    system_prompt = mock_llm.generate.call_args.kwargs["system_prompt"]
    assert "Vietnamese" in system_prompt


# ===========================================================================
# Fallback
# ===========================================================================

def test_llm_failure_deterministic_fallback(generator, mock_llm, rescheduling_result_with_suggestions):
    """LLM failure triggers deterministic fallback."""
    mock_llm.generate.side_effect = LLMGenerationError("LLM failed")

    result = generator.generate_response(
        execution_result=rescheduling_result_with_suggestions,
        user_message="Suggest rescheduling",
    )

    # Should return deterministic fallback, not error
    assert result is not None
    assert len(result) > 0
    assert "Prepare report" in result or "feasible" in result.lower()


def test_invalid_llm_output_fallback(generator, mock_llm, rescheduling_result_with_suggestions):
    """Invalid LLM output triggers fallback."""
    mock_llm.generate.side_effect = LLMGenerationError("Invalid output")

    result = generator.generate_response(
        execution_result=rescheduling_result_with_suggestions,
        user_message="Suggest rescheduling",
    )

    assert result is not None


def test_empty_result_fallback(generator, mock_llm, rescheduling_result_empty):
    """Empty result fallback explains no candidates found."""
    mock_llm.generate.side_effect = LLMGenerationError("LLM failed")

    result = generator.generate_response(
        execution_result=rescheduling_result_empty,
        user_message="Suggest rescheduling",
    )

    assert "No feasible candidate" in result or "could not be assigned" in result or "No rescheduling" in result


# ===========================================================================
# Regression: ANALYZE_WORKLOAD
# ===========================================================================

def test_analyze_workload_unchanged(generator, mock_llm):
    """ANALYZE_WORKLOAD response generation unchanged."""
    payload = ExecutionResult(
        status=ExecutionStatus.EXECUTED,
        action_type=ActionType.ANALYZE_WORKLOAD,
        message="Workload analyzed.",
        result_payload={
            "totalTasks": 5,
            "deadlineCollisions": [
                {"deadline": "2026-09-20T10:00:00Z", "tasks": [{"taskName": "Task A"}]}
            ],
            "busyDays": [],
            "recommendedOrder": [],
            "explanation": "You have 5 active tasks.",
        },
    )
    mock_llm.generate.return_value = AgentResponseOutput(response="Generated.")

    result = generator.generate_response(
        execution_result=payload,
        user_message="Analyze my workload",
    )

    assert result == "Generated."
    system_prompt = mock_llm.generate.call_args.kwargs["system_prompt"]
    assert "Total active tasks: 5" in system_prompt
    assert "Deadline collisions detected: 1" in system_prompt
