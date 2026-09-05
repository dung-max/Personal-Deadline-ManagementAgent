"""Unit tests for the ResourceResolver.

The resolver is tested with fake repositories — no database, no LLM, no AWS.
It maps a validated ActionProposal's resource reference to a canonical
resource ID (ValidatedAction), or returns CLARIFICATION_REQUIRED when a
natural-language reference resolves to zero or many resources.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from personal_deadline_management_agent.schemas import (
    ActionProposal,
    ActionType,
    ResourceReference,
)
from personal_deadline_management_agent.services.action_validator import (
    ValidationStatus,
)
from personal_deadline_management_agent.services.resource_resolver import (
    ResolutionResult,
    ResourceResolver,
)


class FakeTaskRepository:
    def __init__(self, tasks: list) -> None:
        self._tasks = list(tasks)

    def find_by_name(self, phrase: str) -> list:
        lower_phrase = phrase.lower()
        return [t for t in self._tasks if lower_phrase in t.task_name.lower()]


class FakeReminderRepository:
    def __init__(self, reminders: list) -> None:
        self._reminders = list(reminders)

    def find_by_task_name(self, phrase: str) -> list:
        lower_phrase = phrase.lower()
        return [r for r in self._reminders if lower_phrase in r.task_name.lower()]


def _task(task_name: str):
    return SimpleNamespace(id=uuid4(), task_name=task_name)


def _reminder(task_name: str):
    return SimpleNamespace(id=uuid4(), task_name=task_name)


@pytest.fixture
def resolver():
    return lambda tasks=None, reminders=None: ResourceResolver(
        task_repository=FakeTaskRepository(tasks or []),
        reminder_repository=FakeReminderRepository(reminders or []),
    )


def _valid(resolved: ResolutionResult):
    assert resolved.status == ValidationStatus.VALID
    assert resolved.validated_action is not None
    return resolved.validated_action


# --- CREATE_TASK -------------------------------------------------------------


def test_create_task_resolves_to_no_resource(resolver):
    r = resolver()
    proposal = ActionProposal(
        action_type=ActionType.CREATE_TASK,
        parameters={"taskName": "Report", "deadline": "2026-10-01T12:00:00Z"},
    )
    va = _valid(r.resolve(proposal))
    assert va.action_type == ActionType.CREATE_TASK
    assert va.resource_id is None
    assert va.parameters["taskName"] == "Report"


def test_create_task_with_resource_requires_clarification(resolver):
    r = resolver(tasks=[_task("report")])
    proposal = ActionProposal(
        action_type=ActionType.CREATE_TASK,
        resource=ResourceReference(id=uuid4()),
        parameters={"taskName": "Report", "deadline": "2026-10-01T12:00:00Z"},
    )
    result = r.resolve(proposal)
    assert result.status == ValidationStatus.CLARIFICATION_REQUIRED
    assert result.validated_action is None


# --- UPDATE_TASK -------------------------------------------------------------


def test_update_task_with_uuid(resolver):
    rid = uuid4()
    r = resolver()
    proposal = ActionProposal(
        action_type=ActionType.UPDATE_TASK,
        resource=ResourceReference(id=rid),
        parameters={"priority": "HIGH"},
    )
    va = _valid(r.resolve(proposal))
    assert va.resource_id == rid
    assert va.action_type == ActionType.UPDATE_TASK


def test_update_task_with_natural_language(resolver):
    task = _task("Prepare the report")
    r = resolver(tasks=[task])
    proposal = ActionProposal(
        action_type=ActionType.UPDATE_TASK,
        resource=ResourceReference(natural_language="report"),
        parameters={"status": "COMPLETED"},
    )
    va = _valid(r.resolve(proposal))
    assert va.resource_id == task.id


def test_update_task_unresolved_natural_language(resolver):
    r = resolver(tasks=[])
    proposal = ActionProposal(
        action_type=ActionType.UPDATE_TASK,
        resource=ResourceReference(natural_language="nonexistent"),
        parameters={"priority": "HIGH"},
    )
    result = r.resolve(proposal)
    assert result.status == ValidationStatus.CLARIFICATION_REQUIRED
    assert result.validated_action is None


def test_update_task_ambiguous_natural_language(resolver):
    r = resolver(tasks=[_task("report one"), _task("report two")])
    proposal = ActionProposal(
        action_type=ActionType.UPDATE_TASK,
        resource=ResourceReference(natural_language="report"),
        parameters={"priority": "HIGH"},
    )
    result = r.resolve(proposal)
    assert result.status == ValidationStatus.CLARIFICATION_REQUIRED
    assert "more than one" in result.message.lower()
    assert result.validated_action is None


# --- DELETE_TASK -------------------------------------------------------------


def test_delete_task_with_uuid(resolver):
    rid = uuid4()
    r = resolver()
    proposal = ActionProposal(
        action_type=ActionType.DELETE_TASK,
        resource=ResourceReference(id=rid),
    )
    va = _valid(r.resolve(proposal))
    assert va.resource_id == rid


def test_delete_task_unresolved_natural_language(resolver):
    r = resolver(tasks=[])
    proposal = ActionProposal(
        action_type=ActionType.DELETE_TASK,
        resource=ResourceReference(natural_language="ghost"),
    )
    result = r.resolve(proposal)
    assert result.status == ValidationStatus.CLARIFICATION_REQUIRED
    assert result.validated_action is None


def test_delete_task_ambiguous_natural_language(resolver):
    r = resolver(tasks=[_task("old report"), _task("new report")])
    proposal = ActionProposal(
        action_type=ActionType.DELETE_TASK,
        resource=ResourceReference(natural_language="report"),
    )
    result = r.resolve(proposal)
    assert result.status == ValidationStatus.CLARIFICATION_REQUIRED
    assert result.validated_action is None


# --- CREATE_REMINDER ---------------------------------------------------------


def test_create_reminder_with_task_uuid(resolver):
    task_id = uuid4()
    r = resolver()
    proposal = ActionProposal(
        action_type=ActionType.CREATE_REMINDER,
        resource=ResourceReference(id=task_id),
        parameters={"remindAt": "2026-10-01T09:00:00Z"},
    )
    va = _valid(r.resolve(proposal))
    assert va.resource_id == task_id


def test_create_reminder_with_task_natural_language(resolver):
    task = _task("Math homework")
    r = resolver(tasks=[task])
    proposal = ActionProposal(
        action_type=ActionType.CREATE_REMINDER,
        resource=ResourceReference(natural_language="math"),
        parameters={"remindAt": "2026-10-01T09:00:00Z"},
    )
    va = _valid(r.resolve(proposal))
    assert va.resource_id == task.id


def test_create_reminder_missing_task(resolver):
    r = resolver()
    proposal = ActionProposal(
        action_type=ActionType.CREATE_REMINDER,
        resource=None,
        parameters={"remindAt": "2026-10-01T09:00:00Z"},
    )
    result = r.resolve(proposal)
    assert result.status == ValidationStatus.CLARIFICATION_REQUIRED
    assert result.validated_action is None


# --- UPDATE_REMINDER ---------------------------------------------------------


def test_update_reminder_with_uuid(resolver):
    rid = uuid4()
    r = resolver()
    proposal = ActionProposal(
        action_type=ActionType.UPDATE_REMINDER,
        resource=ResourceReference(id=rid),
        parameters={"remindAt": "2026-10-02T10:00:00Z"},
    )
    va = _valid(r.resolve(proposal))
    assert va.resource_id == rid


def test_update_reminder_with_natural_language(resolver):
    reminder = _reminder("Dentist")
    r = resolver(reminders=[reminder])
    proposal = ActionProposal(
        action_type=ActionType.UPDATE_REMINDER,
        resource=ResourceReference(natural_language="dentist"),
        parameters={"remindAt": "2026-10-02T10:00:00Z"},
    )
    va = _valid(r.resolve(proposal))
    assert va.resource_id == reminder.id


def test_update_reminder_missing(resolver):
    r = resolver()
    proposal = ActionProposal(
        action_type=ActionType.UPDATE_REMINDER,
        resource=None,
        parameters={"remindAt": "2026-10-02T10:00:00Z"},
    )
    result = r.resolve(proposal)
    assert result.status == ValidationStatus.CLARIFICATION_REQUIRED
    assert result.validated_action is None


# --- DELETE_REMINDER ---------------------------------------------------------


def test_delete_reminder_with_uuid(resolver):
    rid = uuid4()
    r = resolver()
    proposal = ActionProposal(
        action_type=ActionType.DELETE_REMINDER,
        resource=ResourceReference(id=rid),
    )
    va = _valid(r.resolve(proposal))
    assert va.resource_id == rid


def test_delete_reminder_unresolved(resolver):
    r = resolver(reminders=[])
    proposal = ActionProposal(
        action_type=ActionType.DELETE_REMINDER,
        resource=ResourceReference(natural_language="ghost"),
    )
    result = r.resolve(proposal)
    assert result.status == ValidationStatus.CLARIFICATION_REQUIRED
    assert result.validated_action is None
