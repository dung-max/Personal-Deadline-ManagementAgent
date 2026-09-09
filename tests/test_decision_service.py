"""Unit tests for the authorization + safety decision boundary.

The combined decision applies authorization first, then the shared safety
policy only if authorization is actually established.  It does not execute
actions, mutate repositories, call the LLM, or start a confirmation workflow.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from personal_deadline_management_agent.guardrails import (
    DecisionService,
    DecisionStatus,
    SafetyPolicy,
)
from personal_deadline_management_agent.schemas import ActionType
from personal_deadline_management_agent.services.action_validator import (
    ValidatedAction,
)
from personal_deadline_management_agent.services.authorization_service import (
    AuthorizationContext,
    AuthorizationResult,
    AuthorizationService,
    AuthorizationStatus,
)


class StubAuthorizationService:
    def __init__(self, status: AuthorizationStatus, reason: str) -> None:
        self._status = status
        self._reason = reason

    def authorize(self, action, context):
        return AuthorizationResult(
            status=self._status,
            reason=self._reason,
            action=action,
        )


class ExplodingSafetyPolicy:
    def evaluate(self, action):
        raise AssertionError("SafetyPolicy must not run when authorization stops")


@pytest.fixture
def actor_id():
    return uuid4()


@pytest.fixture
def decision_service() -> DecisionService:
    return DecisionService(
        authorization_service=AuthorizationService(),
        safety_policy=SafetyPolicy(),
    )


def _create_task_action() -> ValidatedAction:
    return ValidatedAction(
        action_type=ActionType.CREATE_TASK,
        parameters={"taskName": "Report", "deadline": "2026-10-01T12:00:00Z"},
    )


def _delete_task_action() -> ValidatedAction:
    return ValidatedAction(action_type=ActionType.DELETE_TASK, resource_id=uuid4())


# --- Combined decision --------------------------------------------------------


def test_authorization_not_configured_returns_not_configured(
    decision_service: DecisionService, actor_id
):
    result = decision_service.decide(
        _create_task_action(), AuthorizationContext(actor_id=actor_id)
    )
    assert result.status == DecisionStatus.NOT_CONFIGURED
    assert "ownership" in result.reason.lower()
    assert "cannot be verified" in result.reason.lower()


def test_authorization_not_configured_stops_before_safety_policy(actor_id):
    service = DecisionService(
        authorization_service=AuthorizationService(),
        safety_policy=ExplodingSafetyPolicy(),
    )
    result = service.decide(
        _create_task_action(), AuthorizationContext(actor_id=actor_id)
    )
    assert result.status == DecisionStatus.NOT_CONFIGURED


def test_authorization_denied_returns_denied(actor_id):
    service = DecisionService(
        authorization_service=StubAuthorizationService(
            AuthorizationStatus.DENIED,
            "Actor is denied.",
        ),
        safety_policy=ExplodingSafetyPolicy(),
    )
    result = service.decide(
        _create_task_action(), AuthorizationContext(actor_id=actor_id)
    )
    assert result.status == DecisionStatus.DENIED
    assert "denied" in result.reason.lower()


def test_configured_authorization_allows_create_task_safety_decision(actor_id):
    service = DecisionService(
        authorization_service=StubAuthorizationService(
            AuthorizationStatus.AUTHORIZED,
            "Authorized by test stub.",
        ),
        safety_policy=SafetyPolicy(),
    )
    action = _create_task_action()
    result = service.decide(action, AuthorizationContext(actor_id=actor_id))
    assert result.status == DecisionStatus.AUTHORIZED
    assert result.action == action


def test_configured_authorization_allows_delete_task_safety_decision(actor_id):
    service = DecisionService(
        authorization_service=StubAuthorizationService(
            AuthorizationStatus.AUTHORIZED,
            "Authorized by test stub.",
        ),
        safety_policy=SafetyPolicy(),
    )
    action = _delete_task_action()
    result = service.decide(action, AuthorizationContext(actor_id=actor_id))
    assert result.status == DecisionStatus.CONFIRMATION_REQUIRED
    assert "confirmation" in result.reason.lower()
    assert result.action == action


# --- Boundary ----------------------------------------------------------------


def test_decision_result_is_not_a_command(
    decision_service: DecisionService, actor_id
):
    result = decision_service.decide(
        _create_task_action(), AuthorizationContext(actor_id=actor_id)
    )
    assert not hasattr(result, "execute")
    assert not hasattr(result, "confirm")


def test_confirmation_workflow_is_not_implemented(actor_id):
    service = DecisionService(
        authorization_service=StubAuthorizationService(
            AuthorizationStatus.AUTHORIZED,
            "Authorized by test stub.",
        ),
        safety_policy=SafetyPolicy(),
    )
    result = service.decide(
        _delete_task_action(), AuthorizationContext(actor_id=actor_id)
    )
    assert result.status == DecisionStatus.CONFIRMATION_REQUIRED
    assert not hasattr(result, "confirmation_token")
    assert not hasattr(result, "confirmation_state")


def test_no_langgraph_is_imported_by_guardrails():
    import sys

    import personal_deadline_management_agent.guardrails as guardrails  # noqa: F401

    assert "langgraph" not in sys.modules


def test_decision_flow_has_no_llm_execution_or_mutation_side_effects(
    monkeypatch, decision_service: DecisionService, actor_id
):
    """Any accidental call into LLM/execution/mutation layers fails this test."""

    from personal_deadline_management_agent.repositories.reminder_repository import (
        ReminderRepository,
    )
    from personal_deadline_management_agent.repositories.task_repository import (
        TaskRepository,
    )
    from personal_deadline_management_agent.services.agent_interpreter import (
        AgentInterpreter,
    )
    from personal_deadline_management_agent.services.reminder_service import (
        ReminderService,
    )
    from personal_deadline_management_agent.services.task_service import TaskService
    from personal_deadline_management_agent.uow import UnitOfWork

    def explode(*args, **kwargs):
        raise AssertionError("decision boundary must not call this layer")

    forbidden_calls = [
        (AgentInterpreter, "interpret"),
        (TaskService, "create_task"),
        (TaskService, "update_task"),
        (TaskService, "delete_task"),
        (ReminderService, "create_reminder"),
        (ReminderService, "update_reminder"),
        (ReminderService, "delete_reminder"),
        (TaskRepository, "create"),
        (TaskRepository, "update"),
        (TaskRepository, "delete"),
        (ReminderRepository, "create"),
        (ReminderRepository, "update"),
        (ReminderRepository, "delete"),
        (UnitOfWork, "commit"),
    ]
    for cls, method_name in forbidden_calls:
        monkeypatch.setattr(cls, method_name, explode)

    result = decision_service.decide(
        _delete_task_action(), AuthorizationContext(actor_id=actor_id)
    )
    assert result.status == DecisionStatus.NOT_CONFIGURED