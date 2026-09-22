"""Tests for PDMA-107: AgentResponseGenerator integration with schedulingPressure."""

from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

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
def workload_result_with_scheduling_pressure():
    """Sample workload result with schedulingPressure field."""
    return ExecutionResult(
        status=ExecutionStatus.EXECUTED,
        action_type=ActionType.ANALYZE_WORKLOAD,
        message="Workload analysis completed.",
        result_payload={
            "totalTasks": 3,
            "deadlineCollisions": [],
            "busyDays": [],
            "recommendedOrder": [],
            "schedulingPressure": [
                {
                    "taskAId": "uuid-a",
                    "taskAName": "Implement feature X",
                    "taskBId": "uuid-b",
                    "taskBName": "Fix bug Y",
                    "overlapStart": "2026-09-23T10:00:00Z",
                    "overlapEnd": "2026-09-25T18:00:00Z",
                },
                {
                    "taskAId": "uuid-c",
                    "taskAName": "Write tests",
                    "taskBId": "uuid-d",
                    "taskBName": "Update docs",
                    "overlapStart": "2026-09-24T08:00:00Z",
                    "overlapEnd": "2026-09-26T12:00:00Z",
                },
            ],
            "explanation": "You have 3 active tasks. 2 scheduling pressure pairs detected.",
        },
    )


def test_scheduling_pressure_included_in_system_prompt(
    generator, mock_llm, workload_result_with_scheduling_pressure
):
    """System prompt should include schedulingPressure facts when present."""
    mock_llm.generate.return_value = AgentResponseOutput(
        response="You have scheduling conflicts to resolve."
    )

    generator.generate_response(
        execution_result=workload_result_with_scheduling_pressure,
        user_message="Show me my workload",
    )

    # Verify LLM was called
    assert mock_llm.generate.called
    call_kwargs = mock_llm.generate.call_args.kwargs
    system_prompt = call_kwargs["system_prompt"]

    # Verify scheduling pressure facts are present
    assert "Scheduling pressure detected: 2 overlapping feasibility window pair(s)" in system_prompt
    assert "Implement feature X and Fix bug Y" in system_prompt
    assert "overlap 2026-09-23T10:00:00Z — 2026-09-25T18:00:00Z" in system_prompt
    assert "Write tests and Update docs" in system_prompt
    assert "overlap 2026-09-24T08:00:00Z — 2026-09-26T12:00:00Z" in system_prompt


def test_empty_scheduling_pressure_not_included(generator, mock_llm):
    """Empty schedulingPressure should not appear in system prompt."""
    result = ExecutionResult(
        status=ExecutionStatus.EXECUTED,
        action_type=ActionType.ANALYZE_WORKLOAD,
        message="Workload analysis completed.",
        result_payload={
            "totalTasks": 2,
            "deadlineCollisions": [],
            "busyDays": [],
            "recommendedOrder": [],
            "schedulingPressure": [],
            "explanation": "You have 2 active tasks.",
        },
    )

    mock_llm.generate.return_value = AgentResponseOutput(
        response="Your workload looks clear."
    )

    generator.generate_response(
        execution_result=result,
        user_message="Show me my workload",
    )

    call_kwargs = mock_llm.generate.call_args.kwargs
    system_prompt = call_kwargs["system_prompt"]

    # Verify scheduling pressure section is absent
    assert "Scheduling pressure" not in system_prompt
    assert "overlapping feasibility window" not in system_prompt


def test_missing_scheduling_pressure_field_handled_gracefully(generator, mock_llm):
    """Missing schedulingPressure field should be handled gracefully."""
    result = ExecutionResult(
        status=ExecutionStatus.EXECUTED,
        action_type=ActionType.ANALYZE_WORKLOAD,
        message="Workload analysis completed.",
        result_payload={
            "totalTasks": 1,
            "deadlineCollisions": [],
            "busyDays": [],
            "recommendedOrder": [],
            # schedulingPressure field omitted (backward compatibility)
            "explanation": "You have 1 active task.",
        },
    )

    mock_llm.generate.return_value = AgentResponseOutput(
        response="You have one task."
    )

    # Should not raise
    response = generator.generate_response(
        execution_result=result,
        user_message="Show me my workload",
    )

    assert response == "You have one task."
    call_kwargs = mock_llm.generate.call_args.kwargs
    system_prompt = call_kwargs["system_prompt"]

    # Verify scheduling pressure section is absent
    assert "Scheduling pressure" not in system_prompt


def test_scheduling_pressure_shows_up_to_three_pairs(generator, mock_llm):
    """System prompt should show up to 3 scheduling pressure pairs."""
    result = ExecutionResult(
        status=ExecutionStatus.EXECUTED,
        action_type=ActionType.ANALYZE_WORKLOAD,
        message="Workload analysis completed.",
        result_payload={
            "totalTasks": 8,
            "deadlineCollisions": [],
            "busyDays": [],
            "recommendedOrder": [],
            "schedulingPressure": [
                {
                    "taskAId": "uuid-1",
                    "taskAName": "Task 1",
                    "taskBId": "uuid-2",
                    "taskBName": "Task 2",
                    "overlapStart": "2026-09-23T10:00:00Z",
                    "overlapEnd": "2026-09-25T18:00:00Z",
                },
                {
                    "taskAId": "uuid-3",
                    "taskAName": "Task 3",
                    "taskBId": "uuid-4",
                    "taskBName": "Task 4",
                    "overlapStart": "2026-09-24T08:00:00Z",
                    "overlapEnd": "2026-09-26T12:00:00Z",
                },
                {
                    "taskAId": "uuid-5",
                    "taskAName": "Task 5",
                    "taskBId": "uuid-6",
                    "taskBName": "Task 6",
                    "overlapStart": "2026-09-25T09:00:00Z",
                    "overlapEnd": "2026-09-27T15:00:00Z",
                },
                {
                    "taskAId": "uuid-7",
                    "taskAName": "Task 7",
                    "taskBId": "uuid-8",
                    "taskBName": "Task 8",
                    "overlapStart": "2026-09-26T10:00:00Z",
                    "overlapEnd": "2026-09-28T16:00:00Z",
                },
            ],
            "explanation": "You have 8 active tasks. 4 scheduling pressure pairs detected.",
        },
    )

    mock_llm.generate.return_value = AgentResponseOutput(
        response="You have multiple scheduling conflicts."
    )

    generator.generate_response(
        execution_result=result,
        user_message="Show me my workload",
    )

    call_kwargs = mock_llm.generate.call_args.kwargs
    system_prompt = call_kwargs["system_prompt"]

    # Verify count is correct
    assert "Scheduling pressure detected: 4 overlapping feasibility window pair(s)" in system_prompt

    # Verify first 3 pairs are shown
    assert "Task 1 and Task 2" in system_prompt
    assert "Task 3 and Task 4" in system_prompt
    assert "Task 5 and Task 6" in system_prompt

    # Verify 4th pair is NOT shown (limit is 3)
    assert "Task 7 and Task 8" not in system_prompt
