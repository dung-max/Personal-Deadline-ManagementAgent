"""Tests for ReminderService.

Verifies business logic independently from SQLAlchemy / database Session,
confirming validation rules, error handling, repository delegation,
and absence of commit/rollback calls.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from personal_deadline_management_agent.exceptions.reminder import (
    InvalidReminderError,
    ReminderNotFoundError,
)
from personal_deadline_management_agent.exceptions.task import TaskNotFoundError
from personal_deadline_management_agent.models import Reminder, ReminderStatus
from personal_deadline_management_agent.repositories.reminder_repository import (
    ReminderRepository,
)
from personal_deadline_management_agent.services.reminder_service import (
    ReminderService,
)


# --- Fakes ---


class FakeTaskRepository:
    def __init__(self) -> None:
        self.tasks: dict[uuid.UUID, "FakeTask"] = {}

    def register(self, task: "FakeTask") -> None:
        self.tasks[task.id] = task

    def get_by_id(self, task_id: uuid.UUID) -> "FakeTask | None":
        return self.tasks.get(task_id)


class FakeTask:
    def __init__(self, task_id: uuid.UUID, deadline: datetime):
        self.id = task_id
        self.deadline = deadline


class FakeReminderRepository:
    def __init__(self) -> None:
        self.reminders: dict[uuid.UUID, Reminder] = {}
        self.create_called = False
        self.update_called = False
        self.delete_called = False

    def create(self, reminder: Reminder) -> Reminder:
        self.create_called = True
        if reminder.id is None:
            reminder.id = uuid.uuid4()
        self.reminders[reminder.id] = reminder
        return reminder

    def get_by_id(self, reminder_id: uuid.UUID) -> Reminder | None:
        return self.reminders.get(reminder_id)

    def list_by_task_id(self, task_id: uuid.UUID) -> list[Reminder]:
        return [r for r in self.reminders.values() if r.task_id == task_id]

    def update(self, reminder: Reminder) -> Reminder:
        self.update_called = True
        self.reminders[reminder.id] = reminder
        return reminder

    def delete(self, reminder_id: uuid.UUID) -> bool:
        self.delete_called = True
        if reminder_id in self.reminders:
            del self.reminders[reminder_id]
            return True
        return False


# --- Fixtures ---


NOW = datetime.now(timezone.utc)
TASK_DEADLINE = NOW + timedelta(days=7)
TASK_ID = uuid.uuid4()
TASK_ID_OTHER = uuid.uuid4()


@pytest.fixture
def task_repo() -> FakeTaskRepository:
    repo = FakeTaskRepository()
    repo.register(FakeTask(TASK_ID, TASK_DEADLINE))
    repo.register(FakeTask(TASK_ID_OTHER, NOW + timedelta(days=3)))
    return repo


@pytest.fixture
def reminder_repo() -> FakeReminderRepository:
    return FakeReminderRepository()


@pytest.fixture
def service(
    reminder_repo: FakeReminderRepository,
    task_repo: FakeTaskRepository,
) -> ReminderService:
    return ReminderService(reminder_repo, task_repo)  # type: ignore[arg-type]


# --- Create ---


def test_create_reminder_success(service: ReminderService, reminder_repo: FakeReminderRepository):
    remind_at = NOW + timedelta(hours=12)
    result = service.create_reminder(TASK_ID, remind_at)

    assert reminder_repo.create_called is True
    assert result.task_id == TASK_ID
    assert result.remind_at == remind_at
    assert result.status == ReminderStatus.PENDING.value
    assert result.id is not None


def test_create_reminder_task_not_found(service: ReminderService):
    with pytest.raises(TaskNotFoundError):
        service.create_reminder(uuid.uuid4(), NOW + timedelta(hours=1))


def test_create_reminder_after_deadline_raises(service: ReminderService):
    after_deadline = TASK_DEADLINE + timedelta(hours=1)
    with pytest.raises(InvalidReminderError, match="remind_at"):
        service.create_reminder(TASK_ID, after_deadline)


def test_create_reminder_exactly_at_deadline_allowed(service: ReminderService):
    result = service.create_reminder(TASK_ID, TASK_DEADLINE)
    assert result.remind_at == TASK_DEADLINE
    assert result.status == ReminderStatus.PENDING.value


def test_create_reminder_default_status_pending(
    service: ReminderService, reminder_repo: FakeReminderRepository
):
    result = service.create_reminder(TASK_ID, NOW + timedelta(hours=6))
    assert result.status == ReminderStatus.PENDING.value


# --- Get ---


def test_get_reminder_existing(service: ReminderService):
    created = service.create_reminder(TASK_ID, NOW + timedelta(hours=1))
    found = service.get_reminder(created.id)
    assert found.id == created.id


def test_get_reminder_not_found(service: ReminderService):
    with pytest.raises(ReminderNotFoundError):
        service.get_reminder(uuid.uuid4())


# --- List ---


def test_list_reminders_by_task_existing_task(service: ReminderService):
    service.create_reminder(TASK_ID, NOW + timedelta(hours=1))
    service.create_reminder(TASK_ID, NOW + timedelta(hours=2))
    service.create_reminder(TASK_ID_OTHER, NOW + timedelta(hours=3))

    results = service.list_reminders_by_task(TASK_ID)
    assert len(results) == 2
    assert all(r.task_id == TASK_ID for r in results)


def test_list_reminders_by_task_empty(service: ReminderService):
    results = service.list_reminders_by_task(TASK_ID)
    assert results == []


def test_list_reminders_by_task_not_found(service: ReminderService):
    with pytest.raises(TaskNotFoundError):
        service.list_reminders_by_task(uuid.uuid4())


# --- Update ---


def test_update_pending_reminder_remind_at(
    service: ReminderService,
    reminder_repo: FakeReminderRepository,
):
    created = service.create_reminder(TASK_ID, NOW + timedelta(hours=1))
    new_time = NOW + timedelta(hours=5)
    updated = service.update_reminder(created.id, remind_at=new_time)

    assert reminder_repo.update_called is True
    assert updated.remind_at == new_time


def test_update_pending_reminder_cancel(
    service: ReminderService,
    reminder_repo: FakeReminderRepository,
):
    created = service.create_reminder(TASK_ID, NOW + timedelta(hours=1))
    updated = service.update_reminder(created.id, status=ReminderStatus.CANCELLED)

    assert updated.status == ReminderStatus.CANCELLED.value


def test_update_cancelled_reminder_reschedule(
    service: ReminderService,
):
    created = service.create_reminder(TASK_ID, NOW + timedelta(hours=1))
    service.update_reminder(created.id, status=ReminderStatus.CANCELLED)
    rescheduled = service.update_reminder(created.id, remind_at=NOW + timedelta(hours=2))
    assert rescheduled.remind_at == NOW + timedelta(hours=2)


def test_update_sent_reminder_raises(
    service: ReminderService,
    reminder_repo: FakeReminderRepository,
):
    created = service.create_reminder(TASK_ID, NOW + timedelta(hours=1))
    created.status = ReminderStatus.SENT.value
    reminder_repo.reminders[created.id] = created

    with pytest.raises(InvalidReminderError, match="SENT"):
        service.update_reminder(created.id, remind_at=NOW + timedelta(hours=5))


def test_update_reminder_after_deadline_raises(service: ReminderService):
    created = service.create_reminder(TASK_ID, NOW + timedelta(hours=1))
    after_deadline = TASK_DEADLINE + timedelta(hours=1)
    with pytest.raises(InvalidReminderError, match="remind_at"):
        service.update_reminder(created.id, remind_at=after_deadline)


def test_update_reminder_exactly_at_deadline_allowed(service: ReminderService):
    created = service.create_reminder(TASK_ID, NOW + timedelta(hours=1))
    updated = service.update_reminder(created.id, remind_at=TASK_DEADLINE)
    assert updated.remind_at == TASK_DEADLINE


def test_update_reminder_not_found(service: ReminderService):
    with pytest.raises(ReminderNotFoundError):
        service.update_reminder(uuid.uuid4(), remind_at=NOW + timedelta(hours=1))


def test_update_reminder_rejected_remind_at_leaves_status_unchanged(
    service: ReminderService,
):
    original_time = NOW + timedelta(hours=1)
    created = service.create_reminder(TASK_ID, original_time)

    with pytest.raises(InvalidReminderError, match="remind_at"):
        service.update_reminder(
            created.id,
            remind_at=TASK_DEADLINE + timedelta(hours=1),
            status=ReminderStatus.CANCELLED,
        )

    assert created.status == ReminderStatus.PENDING.value
    assert created.remind_at == original_time


def test_update_reminder_rejected_status_leaves_remind_at_unchanged(
    service: ReminderService,
):
    original_time = NOW + timedelta(hours=1)
    created = service.create_reminder(TASK_ID, original_time)

    with pytest.raises(InvalidReminderError, match="cannot be set"):
        service.update_reminder(
            created.id,
            remind_at=NOW + timedelta(hours=5),
            status="IN_PROGRESS",
        )

    assert created.remind_at == original_time
    assert created.status == ReminderStatus.PENDING.value


def test_update_reminder_missing_task_raises_task_not_found(
    service: ReminderService,
    task_repo: FakeTaskRepository,
):
    created = service.create_reminder(TASK_ID, NOW + timedelta(hours=1))
    del task_repo.tasks[TASK_ID]

    with pytest.raises(TaskNotFoundError):
        service.update_reminder(created.id, remind_at=NOW + timedelta(hours=2))


def test_update_reminder_invalid_transition(service: ReminderService):
    created = service.create_reminder(TASK_ID, NOW + timedelta(hours=1))
    with pytest.raises(InvalidReminderError, match="cannot be set"):
        service.update_reminder(created.id, status="IN_PROGRESS")


def test_update_reminder_pending_to_pending_allowed(service: ReminderService):
    created = service.create_reminder(TASK_ID, NOW + timedelta(hours=1))
    updated = service.update_reminder(created.id, status=ReminderStatus.PENDING)
    assert updated.status == ReminderStatus.PENDING.value


# --- Delete ---


def test_delete_reminder_existing(
    service: ReminderService,
    reminder_repo: FakeReminderRepository,
):
    created = service.create_reminder(TASK_ID, NOW + timedelta(hours=1))
    service.delete_reminder(created.id)

    assert reminder_repo.delete_called is True
    assert reminder_repo.get_by_id(created.id) is None


def test_delete_reminder_not_found(service: ReminderService):
    with pytest.raises(ReminderNotFoundError):
        service.delete_reminder(uuid.uuid4())


# --- Transaction ownership ---


def test_service_does_not_call_commit_or_rollback():
    """Verify that ReminderService never attempts to call commit or rollback."""
    mock_reminder_repo = MagicMock(spec=ReminderRepository)
    mock_task_repo = MagicMock()
    mock_task = MagicMock()
    mock_task.deadline = NOW + timedelta(days=7)
    mock_task_repo.get_by_id.return_value = mock_task

    mock_reminder = MagicMock(spec=Reminder)
    mock_reminder.id = uuid.uuid4()
    mock_reminder.task_id = mock_task.id
    mock_reminder.status = ReminderStatus.PENDING.value
    mock_reminder.remind_at = NOW + timedelta(hours=1)

    mock_reminder_repo.create.return_value = mock_reminder
    mock_reminder_repo.get_by_id.return_value = mock_reminder
    mock_reminder_repo.list_by_task_id.return_value = [mock_reminder]
    mock_reminder_repo.update.return_value = mock_reminder
    mock_reminder_repo.delete.return_value = True

    service = ReminderService(mock_reminder_repo, mock_task_repo)

    service.create_reminder(mock_task.id, NOW + timedelta(hours=1))
    service.get_reminder(mock_reminder.id)
    service.list_reminders_by_task(mock_task.id)
    service.update_reminder(mock_reminder.id, remind_at=NOW + timedelta(hours=2))
    service.delete_reminder(mock_reminder.id)

    assert not hasattr(service, "commit")
    assert not hasattr(service, "rollback")
    for call in mock_reminder_repo.mock_calls:
        assert "commit" not in call[0]
        assert "rollback" not in call[0]
    for call in mock_task_repo.mock_calls:
        assert "commit" not in call[0]
        assert "rollback" not in call[0]
