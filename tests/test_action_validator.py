"""Unit tests for the ActionValidator and ValidatedAction.

All tests are deterministic and use no database, no LLM, no AWS calls.
They exercise structural validation of ActionProposal only — resource
resolution (the mapping of natural-language references to canonical IDs)
is covered in :mod:`test_resource_resolver`.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from personal_deadline_management_agent.schemas import (
    ActionProposal,
    ActionType,
    ProposalStatus,
    ResourceReference,
)
from personal_deadline_management_agent.services.action_validator import (
    ActionValidator,
    ValidatedAction,
    ValidationResult,
    ValidationStatus,
)


@pytest.fixture
def validator() -> ActionValidator:
    return ActionValidator()


# --- ValidatedAction ---------------------------------------------------------


class TestValidatedAction:
    def test_canonical_resource_id(self):
        rid = uuid4()
        va = ValidatedAction(action_type=ActionType.UPDATE_TASK, resource_id=rid)
        assert va.resource_id == rid
        assert va.action_type == ActionType.UPDATE_TASK

    def test_parameters_preserved(self):
        params = {"taskName": "Report", "deadline": "2026-10-01T12:00:00Z"}
        va = ValidatedAction(
            action_type=ActionType.CREATE_TASK,
            parameters=params,
        )
        assert va.parameters == params

    def test_create_task_no_resource(self):
        va = ValidatedAction(action_type=ActionType.CREATE_TASK)
        assert va.resource_id is None

    def test_default_parameters_empty(self):
        va = ValidatedAction(action_type=ActionType.DELETE_REMINDER)
        assert va.parameters == {}

    def test_serialization_round_trip(self):
        rid = uuid4()
        va = ValidatedAction(
            action_type=ActionType.UPDATE_TASK,
            resource_id=rid,
            parameters={"status": "COMPLETED"},
        )
        restored = ValidatedAction.model_validate(va.model_dump())
        assert restored == va


# --- ValidationStatus --------------------------------------------------------


def test_validation_status_values():
    values = {s.value for s in ValidationStatus}
    assert values == {"VALID", "CLARIFICATION_REQUIRED", "REJECTED"}


# --- CREATE_TASK -------------------------------------------------------------


def test_create_task_without_resource_valid(validator: ActionValidator):
    proposal = ActionProposal(
        action_type=ActionType.CREATE_TASK,
        parameters={
            "taskName": "Prepare report",
            "deadline": "2026-10-01T12:00:00Z",
        },
    )
    result = validator.validate(proposal)
    assert result.status == ValidationStatus.VALID


def test_create_task_with_resource_clarification(validator: ActionValidator):
    proposal = ActionProposal(
        action_type=ActionType.CREATE_TASK,
        resource=ResourceReference(id=uuid4()),
    )
    result = validator.validate(proposal)
    assert result.status == ValidationStatus.CLARIFICATION_REQUIRED
    assert "does not target" in result.message.lower()


def test_create_task_with_natural_language_resource_clarification(
    validator: ActionValidator,
):
    proposal = ActionProposal(
        action_type=ActionType.CREATE_TASK,
        resource=ResourceReference(natural_language="existing task"),
    )
    result = validator.validate(proposal)
    assert result.status == ValidationStatus.CLARIFICATION_REQUIRED
    assert "does not target" in result.message.lower()


def test_create_task_missing_task_name_clarification(validator: ActionValidator):
    proposal = ActionProposal(
        action_type=ActionType.CREATE_TASK,
        parameters={"deadline": "2026-10-01T12:00:00Z"},
    )
    result = validator.validate(proposal)
    assert result.status == ValidationStatus.CLARIFICATION_REQUIRED
    assert "taskName" in result.message


def test_create_task_missing_deadline_clarification(validator: ActionValidator):
    proposal = ActionProposal(
        action_type=ActionType.CREATE_TASK,
        parameters={"taskName": "Report"},
    )
    result = validator.validate(proposal)
    assert result.status == ValidationStatus.CLARIFICATION_REQUIRED
    assert "deadline" in result.message


def test_create_task_missing_all_params_clarification(validator: ActionValidator):
    proposal = ActionProposal(
        action_type=ActionType.CREATE_TASK,
        parameters={},
    )
    result = validator.validate(proposal)
    assert result.status == ValidationStatus.CLARIFICATION_REQUIRED


# --- UPDATE_TASK -------------------------------------------------------------


def test_update_task_valid_uuid(validator: ActionValidator):
    proposal = ActionProposal(
        action_type=ActionType.UPDATE_TASK,
        resource=ResourceReference(id=uuid4()),
        parameters={"priority": "HIGH"},
    )
    result = validator.validate(proposal)
    assert result.status == ValidationStatus.VALID


def test_update_task_valid_natural_language(validator: ActionValidator):
    proposal = ActionProposal(
        action_type=ActionType.UPDATE_TASK,
        resource=ResourceReference(natural_language="report task"),
        parameters={"status": "COMPLETED"},
    )
    result = validator.validate(proposal)
    assert result.status == ValidationStatus.VALID


def test_update_task_missing_resource(validator: ActionValidator):
    proposal = ActionProposal(
        action_type=ActionType.UPDATE_TASK,
        resource=None,
        parameters={"priority": "HIGH"},
    )
    result = validator.validate(proposal)
    assert result.status == ValidationStatus.CLARIFICATION_REQUIRED
    assert "required" in result.message.lower()


def test_update_task_empty_parameters(validator: ActionValidator):
    proposal = ActionProposal(
        action_type=ActionType.UPDATE_TASK,
        resource=ResourceReference(id=uuid4()),
        parameters={},
    )
    result = validator.validate(proposal)
    assert result.status == ValidationStatus.CLARIFICATION_REQUIRED
    assert "at least one" in result.message.lower()


# --- DELETE_TASK -------------------------------------------------------------


def test_delete_task_valid_uuid(validator: ActionValidator):
    proposal = ActionProposal(
        action_type=ActionType.DELETE_TASK,
        resource=ResourceReference(id=uuid4()),
    )
    result = validator.validate(proposal)
    assert result.status == ValidationStatus.VALID


def test_delete_task_valid_natural_language(validator: ActionValidator):
    proposal = ActionProposal(
        action_type=ActionType.DELETE_TASK,
        resource=ResourceReference(natural_language="old report"),
    )
    result = validator.validate(proposal)
    assert result.status == ValidationStatus.VALID


def test_delete_task_missing_resource(validator: ActionValidator):
    proposal = ActionProposal(action_type=ActionType.DELETE_TASK, resource=None)
    result = validator.validate(proposal)
    assert result.status == ValidationStatus.CLARIFICATION_REQUIRED


# --- CREATE_REMINDER ---------------------------------------------------------


def test_create_reminder_valid_uuid(validator: ActionValidator):
    proposal = ActionProposal(
        action_type=ActionType.CREATE_REMINDER,
        resource=ResourceReference(id=uuid4()),
        parameters={"remindAt": "2026-10-01T09:00:00Z"},
    )
    result = validator.validate(proposal)
    assert result.status == ValidationStatus.VALID


def test_create_reminder_valid_natural_language(validator: ActionValidator):
    proposal = ActionProposal(
        action_type=ActionType.CREATE_REMINDER,
        resource=ResourceReference(natural_language="math task"),
        parameters={"remindAt": "2026-10-01T09:00:00Z"},
    )
    result = validator.validate(proposal)
    assert result.status == ValidationStatus.VALID


def test_create_reminder_missing_resource(validator: ActionValidator):
    proposal = ActionProposal(
        action_type=ActionType.CREATE_REMINDER,
        parameters={"remindAt": "2026-10-01T09:00:00Z"},
    )
    result = validator.validate(proposal)
    assert result.status == ValidationStatus.CLARIFICATION_REQUIRED


def test_create_reminder_missing_remind_at(validator: ActionValidator):
    proposal = ActionProposal(
        action_type=ActionType.CREATE_REMINDER,
        resource=ResourceReference(id=uuid4()),
    )
    result = validator.validate(proposal)
    assert result.status == ValidationStatus.CLARIFICATION_REQUIRED
    assert "remindAt" in result.message


# --- UPDATE_REMINDER ---------------------------------------------------------


def test_update_reminder_valid_uuid(validator: ActionValidator):
    proposal = ActionProposal(
        action_type=ActionType.UPDATE_REMINDER,
        resource=ResourceReference(id=uuid4()),
        parameters={"remindAt": "2026-10-02T10:00:00Z"},
    )
    result = validator.validate(proposal)
    assert result.status == ValidationStatus.VALID


def test_update_reminder_missing_resource(validator: ActionValidator):
    proposal = ActionProposal(
        action_type=ActionType.UPDATE_REMINDER,
        resource=None,
        parameters={"remindAt": "2026-10-02T10:00:00Z"},
    )
    result = validator.validate(proposal)
    assert result.status == ValidationStatus.CLARIFICATION_REQUIRED


def test_update_reminder_empty_parameters(validator: ActionValidator):
    proposal = ActionProposal(
        action_type=ActionType.UPDATE_REMINDER,
        resource=ResourceReference(id=uuid4()),
    )
    result = validator.validate(proposal)
    assert result.status == ValidationStatus.CLARIFICATION_REQUIRED


# --- DELETE_REMINDER ---------------------------------------------------------


def test_delete_reminder_valid_uuid(validator: ActionValidator):
    proposal = ActionProposal(
        action_type=ActionType.DELETE_REMINDER,
        resource=ResourceReference(id=uuid4()),
    )
    result = validator.validate(proposal)
    assert result.status == ValidationStatus.VALID


def test_delete_reminder_missing_resource(validator: ActionValidator):
    proposal = ActionProposal(action_type=ActionType.DELETE_REMINDER, resource=None)
    result = validator.validate(proposal)
    assert result.status == ValidationStatus.CLARIFICATION_REQUIRED
