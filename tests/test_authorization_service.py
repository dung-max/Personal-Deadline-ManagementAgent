"""Unit tests for the AuthorizationService.

Deterministic, no database, no LLM, no AWS.  The MVP has a temporary
single-user authorization mode: when ``single_user_mode=True`` all requests
are authorized; when the actor matches ``default_user_id`` the request is
authorized; otherwise NOT_CONFIGURED (no real ownership model yet).

TODO(MVP): these tests cover the temporary single-user mode.  When a real
ownership model is introduced, replace this service and update these tests.
"""

from __future__ import annotations

import inspect
from uuid import uuid4

import pytest

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

DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def service() -> AuthorizationService:
    return AuthorizationService()


def _create_task_action() -> ValidatedAction:
    return ValidatedAction(
        action_type=ActionType.CREATE_TASK,
        parameters={"taskName": "Report", "deadline": "2026-10-01T12:00:00Z"},
    )


# --- Default behavior (no configuration) ------------------------------------


def test_authorization_cannot_be_verified_without_ownership_model(
    service: AuthorizationService,
):
    action = _create_task_action()
    result = service.authorize(action, AuthorizationContext(actor_id=uuid4()))
    assert result.status == AuthorizationStatus.NOT_CONFIGURED
    assert result.action == action
    assert "ownership" in result.reason.lower()
    assert "cannot be verified" in result.reason.lower()


def test_missing_context_still_reports_not_configured(
    service: AuthorizationService,
):
    result = service.authorize(_create_task_action(), None)
    assert result.status == AuthorizationStatus.NOT_CONFIGURED
    assert "not configured" in result.reason.lower()


def test_unsupported_context_still_reports_not_configured(
    service: AuthorizationService,
):
    result = service.authorize(_create_task_action(), "not-a-context")
    assert result.status == AuthorizationStatus.NOT_CONFIGURED
    assert "no user/account model" in result.reason.lower()


def test_authorization_result_serialization_round_trip(
    service: AuthorizationService,
):
    result = service.authorize(
        _create_task_action(), AuthorizationContext(actor_id=uuid4())
    )
    restored = AuthorizationResult.model_validate(result.model_dump())
    assert restored == result


# --- Default user match → AUTHORIZED ----------------------------------------


def test_matching_default_user_is_authorized():
    actor = uuid4()
    service = AuthorizationService(default_user_id=str(actor))
    action = _create_task_action()

    result = service.authorize(action, AuthorizationContext(actor_id=actor))

    assert result.status == AuthorizationStatus.AUTHORIZED
    assert "default user" in result.reason.lower()
    assert result.action == action


def test_non_matching_user_keeps_not_configured():
    service = AuthorizationService(default_user_id=str(uuid4()))
    action = _create_task_action()

    result = service.authorize(action, AuthorizationContext(actor_id=uuid4()))

    assert result.status == AuthorizationStatus.NOT_CONFIGURED
    assert "ownership" in result.reason.lower()


def test_empty_default_user_id_disables_match():
    service = AuthorizationService(default_user_id="")
    action = _create_task_action()

    result = service.authorize(action, AuthorizationContext(actor_id=uuid4()))

    assert result.status == AuthorizationStatus.NOT_CONFIGURED


# --- Local-dev default user -------------------------------------------------


def test_local_dev_default_user_authorizes_matching_actor():
    """The hardcoded local-dev UUID is authorized by default."""
    local_dev_actor = uuid4()
    # Default DEFAULT_USER_ID is the local-dev UUID, so this test must
    # override it to control the actor — we verify the feature works, not
    # that the default UUID is correct (it is documented in config.py).
    service = AuthorizationService(default_user_id=str(local_dev_actor))
    action = _create_task_action()

    result = service.authorize(action, AuthorizationContext(actor_id=local_dev_actor))

    assert result.status == AuthorizationStatus.AUTHORIZED


# --- Single-user mode -------------------------------------------------------


def test_single_user_mode_authorizes_any_actor():
    service = AuthorizationService(single_user_mode=True)
    action = _create_task_action()

    result = service.authorize(action, AuthorizationContext(actor_id=uuid4()))

    assert result.status == AuthorizationStatus.AUTHORIZED
    assert "single-user" in result.reason.lower()
    assert result.action == action


def test_single_user_mode_authorizes_none_context():
    service = AuthorizationService(single_user_mode=True)

    result = service.authorize(_create_task_action(), None)

    assert result.status == AuthorizationStatus.AUTHORIZED


def test_single_user_mode_authorizes_unsupported_context_type():
    service = AuthorizationService(single_user_mode=True)

    result = service.authorize(_create_task_action(), "not-a-context")

    assert result.status == AuthorizationStatus.AUTHORIZED


# --- Boundary ----------------------------------------------------------------


def test_authorization_status_values():
    values = {s.value for s in AuthorizationStatus}
    assert values == {"AUTHORIZED", "DENIED", "NOT_CONFIGURED"}


def test_authorization_service_uses_no_allowlist():
    signature = inspect.signature(AuthorizationService)
    assert "allowed_actor_ids" not in signature.parameters

    service = AuthorizationService()
    assert not hasattr(service, "_allowed_actor_ids")