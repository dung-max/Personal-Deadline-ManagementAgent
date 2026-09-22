"""Unit tests for the shared SafetyPolicy.

The policy is deterministic application policy: no DB, no LLM, no AWS, no
execution, and no confirmation workflow.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from personal_deadline_management_agent.guardrails.safety_policy import (
    DESTRUCTIVE_ACTIONS,
    SUPPORTED_ACTIONS,
    DecisionResult,
    DecisionStatus,
    SafetyPolicy,
)
from personal_deadline_management_agent.schemas import ActionType
from personal_deadline_management_agent.services.action_validator import (
    ValidatedAction,
)


@pytest.fixture
def policy() -> SafetyPolicy:
    return SafetyPolicy()


_NO_RESOURCE_ACTIONS = {ActionType.CREATE_TASK, ActionType.ANALYZE_WORKLOAD}


def _action(action_type: ActionType) -> ValidatedAction:
    resource_id = None if action_type in _NO_RESOURCE_ACTIONS else uuid4()
    return ValidatedAction(action_type=action_type, resource_id=resource_id)


# --- Contract ----------------------------------------------------------------


def test_decision_status_values():
    values = {s.value for s in DecisionStatus}
    assert values == {
        "AUTHORIZED",
        "DENIED",
        "NOT_CONFIGURED",
        "CONFIRMATION_REQUIRED",
    }


def test_supported_actions_are_the_mvp_actions():
    assert SUPPORTED_ACTIONS == {
        ActionType.CREATE_TASK,
        ActionType.UPDATE_TASK,
        ActionType.DELETE_TASK,
        ActionType.CREATE_REMINDER,
        ActionType.UPDATE_REMINDER,
        ActionType.DELETE_REMINDER,
        ActionType.ANALYZE_WORKLOAD,
        ActionType.SUGGEST_RESCHEDULING,
    }


def test_destructive_actions_are_delete_actions_only():
    assert DESTRUCTIVE_ACTIONS == {
        ActionType.DELETE_TASK,
        ActionType.DELETE_REMINDER,
    }


def test_decision_result_has_no_execute_method(policy: SafetyPolicy):
    result = policy.evaluate(_action(ActionType.CREATE_TASK))
    assert isinstance(result, DecisionResult)
    assert not hasattr(result, "execute")


# --- Allowed non-destructive actions -----------------------------------------


@pytest.mark.parametrize(
    "action_type",
    [
        ActionType.CREATE_TASK,
        ActionType.UPDATE_TASK,
        ActionType.CREATE_REMINDER,
        ActionType.UPDATE_REMINDER,
        ActionType.ANALYZE_WORKLOAD,
        ActionType.SUGGEST_RESCHEDULING,
    ],
)
def test_non_destructive_supported_actions_are_authorized(
    policy: SafetyPolicy, action_type: ActionType
):
    result = policy.evaluate(_action(action_type))
    assert result.status == DecisionStatus.AUTHORIZED
    assert result.action.action_type == action_type


# --- Destructive actions ------------------------------------------------------


@pytest.mark.parametrize(
    "action_type",
    [ActionType.DELETE_TASK, ActionType.DELETE_REMINDER],
)
def test_destructive_actions_require_confirmation(
    policy: SafetyPolicy, action_type: ActionType
):
    result = policy.evaluate(_action(action_type))
    assert result.status == DecisionStatus.CONFIRMATION_REQUIRED
    assert "confirmation" in result.reason.lower()
    assert result.action.action_type == action_type


# --- Unsupported actions ------------------------------------------------------


def test_unsupported_action_is_denied(policy: SafetyPolicy):
    # Bypass Pydantic validation to simulate a future/unknown ValidatedAction
    # reaching the shared policy boundary.
    unsupported = ValidatedAction.model_construct(
        action_type="SEND_EMAIL",
        resource_id=None,
        parameters={},
    )
    result = policy.evaluate(unsupported)
    assert result.status == DecisionStatus.DENIED
    assert "not supported" in result.reason.lower()
    assert result.action == unsupported