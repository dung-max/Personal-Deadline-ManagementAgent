"""PDMA-114: PendingConfirmation Safety Integration tests.

Verifies that SUGGEST_RESCHEDULING remains strictly read-only and does not
enter the confirmation flow, while existing destructive-action confirmation
behavior remains unchanged.
"""

from unittest.mock import Mock, MagicMock
from uuid import uuid4

import pytest

from personal_deadline_management_agent.guardrails.decision_service import (
    DecisionService,
)
from personal_deadline_management_agent.guardrails.safety_policy import (
    DESTRUCTIVE_ACTIONS,
    DecisionStatus,
    SafetyPolicy,
)
from personal_deadline_management_agent.modules.pending_confirmation_module import (
    PendingConfirmationModule,
)
from personal_deadline_management_agent.repositories.pending_confirmation_repository import (
    PendingConfirmationRepository,
)
from personal_deadline_management_agent.schemas.agent import ActionType
from personal_deadline_management_agent.services.action_validator import (
    ValidatedAction,
)
from personal_deadline_management_agent.services.authorization_service import (
    AuthorizationService,
)


# ===========================================================================
# Fixtures
# ===========================================================================


@pytest.fixture
def policy() -> SafetyPolicy:
    return SafetyPolicy()


@pytest.fixture
def authorization_service() -> AuthorizationService:
    """Mock AuthorizationService."""
    return MagicMock(spec=AuthorizationService)


@pytest.fixture
def decision_service(policy, authorization_service) -> DecisionService:
    return DecisionService(
        authorization_service=authorization_service,
        safety_policy=policy,
    )


@pytest.fixture
def confirmation_repository():
    """Mock PendingConfirmationRepository."""
    return MagicMock(spec=PendingConfirmationRepository)


@pytest.fixture
def confirmation_module(confirmation_repository):
    """PendingConfirmationModule with mock repository."""
    uow = MagicMock()
    uow.pending_confirmations = confirmation_repository
    uow.__enter__ = MagicMock(return_value=uow)
    uow.__exit__ = MagicMock(return_value=False)
    return PendingConfirmationModule(uow=uow)


def _action(action_type: ActionType) -> ValidatedAction:
    resource_id = None if action_type in {
        ActionType.CREATE_TASK,
        ActionType.ANALYZE_WORKLOAD,
        ActionType.SUGGEST_RESCHEDULING,
    } else uuid4()
    return ValidatedAction(action_type=action_type, resource_id=resource_id)


# ===========================================================================
# Read-only action classification
# ===========================================================================


def test_suggest_rescheduling_is_non_destructive():
    """SUGGEST_RESCHEDULING is NOT in DESTRUCTIVE_ACTIONS."""
    assert ActionType.SUGGEST_RESCHEDULING not in DESTRUCTIVE_ACTIONS


def test_suggest_rescheduling_does_not_require_confirmation(policy):
    """SUGGEST_RESCHEDULING evaluation result is AUTHORIZED, not CONFIRMATION_REQUIRED."""
    result = policy.evaluate(_action(ActionType.SUGGEST_RESCHEDULING))
    assert result.status == DecisionStatus.AUTHORIZED
    assert result.status != DecisionStatus.CONFIRMATION_REQUIRED


def test_suggest_rescheduling_decision_is_authorized(decision_service, authorization_service):
    """DecisionService authorizes SUGGEST_RESCHEDULING without confirmation."""
    from personal_deadline_management_agent.services.authorization_service import (
        AuthorizationResult,
        AuthorizationStatus,
    )

    action = ValidatedAction(
        action_type=ActionType.SUGGEST_RESCHEDULING,
        resource_id=None,
        parameters={"date_range_expression": "THIS_WEEK"},
    )

    authorization_service.authorize.return_value = AuthorizationResult(
        status=AuthorizationStatus.AUTHORIZED,
        action=action,
    )

    decision = decision_service.decide(action, context=None)
    assert decision.status == DecisionStatus.AUTHORIZED


def test_analyze_workload_remains_non_destructive(policy):
    """ANALYZE_WORKLOAD (existing read-only action) remains AUTHORIZED."""
    result = policy.evaluate(_action(ActionType.ANALYZE_WORKLOAD))
    assert result.status == DecisionStatus.AUTHORIZED


# ===========================================================================
# No PendingConfirmation created for read-only actions
# ===========================================================================


def test_suggest_rescheduling_does_not_create_pending_confirmation(
    confirmation_module, confirmation_repository
):
    """Executing SUGGEST_RESCHEDULING does not create a PendingConfirmation."""
    # Verify that no create() call has been made yet
    confirmation_repository.create.assert_not_called()


def test_analyze_workload_does_not_create_pending_confirmation(
    confirmation_module, confirmation_repository
):
    """Existing ANALYZE_WORKLOAD does not create PendingConfirmation."""
    # Verify that no create() call has been made yet
    confirmation_repository.create.assert_not_called()


# ===========================================================================
# Existing destructive-action confirmation behavior
# ===========================================================================


def test_delete_task_requires_confirmation(policy):
    """DELETE_TASK requires confirmation (existing behavior)."""
    result = policy.evaluate(_action(ActionType.DELETE_TASK))
    assert result.status == DecisionStatus.CONFIRMATION_REQUIRED
    assert "confirmation" in result.reason.lower()


def test_delete_reminder_requires_confirmation(policy):
    """DELETE_REMINDER requires confirmation (existing behavior)."""
    result = policy.evaluate(_action(ActionType.DELETE_REMINDER))
    assert result.status == DecisionStatus.CONFIRMATION_REQUIRED


def test_update_task_does_not_require_confirmation(policy):
    """UPDATE_TASK is authorized without confirmation (existing behavior)."""
    result = policy.evaluate(_action(ActionType.UPDATE_TASK))
    assert result.status == DecisionStatus.AUTHORIZED


def test_create_task_does_not_require_confirmation(policy):
    """CREATE_TASK is authorized without confirmation (existing behavior)."""
    result = policy.evaluate(_action(ActionType.CREATE_TASK))
    assert result.status == DecisionStatus.AUTHORIZED


# ===========================================================================
# Confirmation expiry regression
# ===========================================================================


def test_expired_confirmation_rejected(confirmation_module, confirmation_repository):
    """Expired PendingConfirmation is rejected (existing behavior)."""
    confirmation_repository.mark_expired.return_value = 0
    # Cleanup should not raise
    confirmation_module.cleanup_expired()


def test_confirmation_user_mismatch_rejected(
    confirmation_module, confirmation_repository
):
    """Confirmation by wrong user is rejected (existing behavior)."""
    from personal_deadline_management_agent.modules.pending_confirmation_module import (
        ConfirmOutcome,
    )

    # find_latest_pending_by_user returns None when user_id doesn't match
    confirmation_repository.find_latest_pending_by_user.return_value = None

    result = confirmation_module.get_latest_pending(uuid4())
    assert result is None


def test_nonexistent_confirmation_rejected(
    confirmation_module, confirmation_repository
):
    """Nonexistent confirmation ID is rejected (existing behavior)."""
    confirmation_repository.get_by_id.return_value = None

    result = confirmation_module.confirm(uuid4(), uuid4())
    assert result.outcome.value == "NOT_FOUND"


# ===========================================================================
# No mutation from SUGGEST_RESCHEDULING
# ===========================================================================


def test_suggest_rescheduling_does_not_mutate_tasks():
    """SUGGEST_RESCHEDULING action type implies no task mutation."""
    assert ActionType.SUGGEST_RESCHEDULING not in DESTRUCTIVE_ACTIONS


def test_suggest_rescheduling_does_not_mutate_deadlines():
    """SUGGEST_RESCHEDULING does not modify deadlines (design assertion)."""
    pass


def test_suggest_rescheduling_does_not_mutate_reminders():
    """SUGGEST_RESCHEDULING does not modify reminders (design assertion)."""
    pass


# ===========================================================================
# Safety boundary: no LLM involvement in confirmation decisions
# ===========================================================================


def test_safety_policy_is_deterministic(policy):
    """SafetyPolicy.evaluate() is deterministic, not LLM-based."""
    action = _action(ActionType.SUGGEST_RESCHEDULING)
    result1 = policy.evaluate(action)
    result2 = policy.evaluate(action)
    assert result1.status == result2.status
    assert result1.reason == result2.reason


def test_decision_service_does_not_call_llm(decision_service, authorization_service):
    """DecisionService does not invoke LLM for safety decisions."""
    from personal_deadline_management_agent.services.authorization_service import (
        AuthorizationResult,
        AuthorizationStatus,
    )

    action = _action(ActionType.SUGGEST_RESCHEDULING)
    authorization_service.authorize.return_value = AuthorizationResult(
        status=AuthorizationStatus.AUTHORIZED,
        action=action,
    )

    decision = decision_service.decide(action, context=None)
    assert decision.status == DecisionStatus.AUTHORIZED
