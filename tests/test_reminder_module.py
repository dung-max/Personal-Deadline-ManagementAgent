"""Tests for ReminderModule.

Verifies use-case orchestration and transaction boundaries:
- Write operations commit exactly once on success and rollback on failure.
- Read operations neither commit nor rollback.
- Exceptions from ReminderService propagate unchanged.
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
from personal_deadline_management_agent.modules.reminder_module import ReminderModule


# --- Fakes ---


class FakeTask:
    def __init__(self, task_id: uuid.UUID, deadline: datetime) -> None:
        self.id = task_id
        self.deadline = deadline


class FakeTaskRepository:
    def __init__(self) -> None:
        self.tasks: dict[uuid.UUID, FakeTask] = {}

    def register(self, task: FakeTask) -> None:
        self.tasks[task.id] = task

    def get_by_id(self, task_id: uuid.UUID) -> FakeTask | None:
        return self.tasks.get(task_id)


class FakeReminderRepository:
    def __init__(self) -> None:
        self.reminders: dict[uuid.UUID, Reminder] = {}

    def create(self, reminder: Reminder) -> Reminder:
        if reminder.id is None:
            reminder.id = uuid.uuid4()
        self.reminders[reminder.id] = reminder
        return reminder

    def get_by_id(self, reminder_id: uuid.UUID) -> Reminder | None:
        return self.reminders.get(reminder_id)

    def list_by_task_id(self, task_id: uuid.UUID) -> list[Reminder]:
        return [r for r in self.reminders.values() if r.task_id == task_id]

    def update(self, reminder: Reminder) -> Reminder:
        self.reminders[reminder.id] = reminder
        return reminder

    def delete(self, reminder_id: uuid.UUID) -> bool:
        if reminder_id in self.reminders:
            del self.reminders[reminder_id]
            return True
        return False


class FakeUnitOfWork:
    """Fake UnitOfWork tracking commit and rollback calls."""

    def __init__(
        self,
        reminders_repo: FakeReminderRepository | None = None,
        tasks_repo: FakeTaskRepository | None = None,
    ) -> None:
        self.reminders = (
            reminders_repo if reminders_repo is not None else FakeReminderRepository()
        )
        self.tasks = tasks_repo if tasks_repo is not None else FakeTaskRepository()
        self.commit_count = 0
        self.rollback_count = 0
        self.closed = False

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1

    def close(self) -> None:
        self.closed = True


# --- Fixtures ---


NOW = datetime.now(timezone.utc)
TASK_DEADLINE = NOW + timedelta(days=7)
TASK_ID = uuid.uuid4()
TASK_ID_OTHER = uuid.uuid4()
REMIND_AT = NOW + timedelta(hours=1)


@pytest.fixture
def fake_uow() -> FakeUnitOfWork:
    uow = FakeUnitOfWork()
    uow.tasks.register(FakeTask(TASK_ID, TASK_DEADLINE))
    uow.tasks.register(FakeTask(TASK_ID_OTHER, NOW + timedelta(days=3)))
    return uow


@pytest.fixture
def reminder_module(fake_uow: FakeUnitOfWork) -> ReminderModule:
    return ReminderModule(fake_uow)  # type: ignore[arg-type]


# --- 1. Create ---


def test_create_reminder_success_commits_once(
    reminder_module: ReminderModule, fake_uow: FakeUnitOfWork
):
    reminder = reminder_module.create_reminder(TASK_ID, REMIND_AT)

    assert reminder.task_id == TASK_ID
    assert reminder.remind_at == REMIND_AT
    assert reminder.status == ReminderStatus.PENDING.value
    assert fake_uow.commit_count == 1
    assert fake_uow.rollback_count == 0


def test_create_reminder_repository_failure_rolls_back(fake_uow: FakeUnitOfWork):
    fake_uow.reminders.create = MagicMock(side_effect=RuntimeError("DB write failed"))  # type: ignore[assignment]
    module = ReminderModule(fake_uow)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="DB write failed"):
        module.create_reminder(TASK_ID, REMIND_AT)

    assert fake_uow.commit_count == 0
    assert fake_uow.rollback_count == 1


def test_create_reminder_task_not_found_rolls_back_and_propagates(
    reminder_module: ReminderModule, fake_uow: FakeUnitOfWork
):
    missing_task_id = uuid.uuid4()
    with pytest.raises(TaskNotFoundError) as exc_info:
        reminder_module.create_reminder(missing_task_id, REMIND_AT)

    assert exc_info.value.task_id == missing_task_id
    assert fake_uow.commit_count == 0
    assert fake_uow.rollback_count == 1


def test_create_reminder_after_deadline_rolls_back_and_propagates(
    reminder_module: ReminderModule, fake_uow: FakeUnitOfWork
):
    with pytest.raises(InvalidReminderError, match="remind_at"):
        reminder_module.create_reminder(TASK_ID, TASK_DEADLINE + timedelta(hours=1))

    assert fake_uow.commit_count == 0
    assert fake_uow.rollback_count == 1


# --- 2. Get ---


def test_get_reminder_success_does_not_commit_or_rollback(
    reminder_module: ReminderModule, fake_uow: FakeUnitOfWork
):
    created = reminder_module.create_reminder(TASK_ID, REMIND_AT)
    fake_uow.commit_count = 0

    found = reminder_module.get_reminder(created.id)

    assert found.id == created.id
    assert fake_uow.commit_count == 0
    assert fake_uow.rollback_count == 0


def test_get_reminder_not_found_propagates_without_rollback(
    reminder_module: ReminderModule, fake_uow: FakeUnitOfWork
):
    missing_id = uuid.uuid4()
    with pytest.raises(ReminderNotFoundError) as exc_info:
        reminder_module.get_reminder(missing_id)

    assert exc_info.value.reminder_id == missing_id
    assert fake_uow.commit_count == 0
    assert fake_uow.rollback_count == 0


# --- 3. List ---


def test_list_reminders_by_task_does_not_commit_or_rollback(
    reminder_module: ReminderModule, fake_uow: FakeUnitOfWork
):
    reminder_module.create_reminder(TASK_ID, REMIND_AT)
    reminder_module.create_reminder(TASK_ID, NOW + timedelta(hours=2))
    reminder_module.create_reminder(TASK_ID_OTHER, NOW + timedelta(hours=3))
    fake_uow.commit_count = 0

    results = reminder_module.list_reminders_by_task(TASK_ID)

    assert len(results) == 2
    assert all(r.task_id == TASK_ID for r in results)
    assert fake_uow.commit_count == 0
    assert fake_uow.rollback_count == 0


def test_list_reminders_task_not_found_propagates_without_rollback(
    reminder_module: ReminderModule, fake_uow: FakeUnitOfWork
):
    with pytest.raises(TaskNotFoundError):
        reminder_module.list_reminders_by_task(uuid.uuid4())

    assert fake_uow.commit_count == 0
    assert fake_uow.rollback_count == 0


# --- 4. Update ---


def test_update_reminder_success_commits_once(
    reminder_module: ReminderModule, fake_uow: FakeUnitOfWork
):
    created = reminder_module.create_reminder(TASK_ID, REMIND_AT)
    fake_uow.commit_count = 0
    new_time = NOW + timedelta(hours=5)

    updated = reminder_module.update_reminder(created.id, remind_at=new_time)

    assert updated.remind_at == new_time
    assert fake_uow.commit_count == 1
    assert fake_uow.rollback_count == 0


def test_update_reminder_repository_failure_rolls_back(fake_uow: FakeUnitOfWork):
    module = ReminderModule(fake_uow)  # type: ignore[arg-type]
    created = module.create_reminder(TASK_ID, REMIND_AT)
    fake_uow.commit_count = 0

    fake_uow.reminders.update = MagicMock(side_effect=RuntimeError("Update failed"))  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="Update failed"):
        module.update_reminder(created.id, remind_at=NOW + timedelta(hours=2))

    assert fake_uow.commit_count == 0
    assert fake_uow.rollback_count == 1


def test_update_reminder_not_found_rolls_back_and_propagates(
    reminder_module: ReminderModule, fake_uow: FakeUnitOfWork
):
    with pytest.raises(ReminderNotFoundError):
        reminder_module.update_reminder(uuid.uuid4(), remind_at=REMIND_AT)

    assert fake_uow.commit_count == 0
    assert fake_uow.rollback_count == 1


def test_update_reminder_after_deadline_rolls_back_and_propagates(
    reminder_module: ReminderModule, fake_uow: FakeUnitOfWork
):
    created = reminder_module.create_reminder(TASK_ID, REMIND_AT)
    fake_uow.commit_count = 0

    with pytest.raises(InvalidReminderError, match="remind_at"):
        reminder_module.update_reminder(
            created.id, remind_at=TASK_DEADLINE + timedelta(hours=1)
        )

    assert fake_uow.commit_count == 0
    assert fake_uow.rollback_count == 1


def test_update_sent_reminder_rolls_back_and_propagates(
    reminder_module: ReminderModule, fake_uow: FakeUnitOfWork
):
    created = reminder_module.create_reminder(TASK_ID, REMIND_AT)
    created.status = ReminderStatus.SENT.value
    fake_uow.commit_count = 0

    with pytest.raises(InvalidReminderError, match="SENT"):
        reminder_module.update_reminder(created.id, remind_at=NOW + timedelta(hours=2))

    assert fake_uow.commit_count == 0
    assert fake_uow.rollback_count == 1


# --- 5. Delete ---


def test_delete_reminder_success_commits_once(
    reminder_module: ReminderModule, fake_uow: FakeUnitOfWork
):
    created = reminder_module.create_reminder(TASK_ID, REMIND_AT)
    fake_uow.commit_count = 0

    reminder_module.delete_reminder(created.id)

    assert fake_uow.commit_count == 1
    assert fake_uow.rollback_count == 0
    assert fake_uow.reminders.get_by_id(created.id) is None


def test_delete_reminder_repository_failure_rolls_back(fake_uow: FakeUnitOfWork):
    module = ReminderModule(fake_uow)  # type: ignore[arg-type]
    created = module.create_reminder(TASK_ID, REMIND_AT)
    fake_uow.commit_count = 0

    fake_uow.reminders.delete = MagicMock(side_effect=RuntimeError("Delete failed"))  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="Delete failed"):
        module.delete_reminder(created.id)

    assert fake_uow.commit_count == 0
    assert fake_uow.rollback_count == 1


def test_delete_reminder_not_found_rolls_back_and_propagates(
    reminder_module: ReminderModule, fake_uow: FakeUnitOfWork
):
    missing_id = uuid.uuid4()
    with pytest.raises(ReminderNotFoundError) as exc_info:
        reminder_module.delete_reminder(missing_id)

    assert exc_info.value.reminder_id == missing_id
    assert fake_uow.commit_count == 0
    assert fake_uow.rollback_count == 1


def test_delete_reminder_does_not_delete_parent_task(
    reminder_module: ReminderModule, fake_uow: FakeUnitOfWork
):
    created = reminder_module.create_reminder(TASK_ID, REMIND_AT)
    reminder_module.delete_reminder(created.id)

    assert fake_uow.tasks.get_by_id(TASK_ID) is not None


# --- 6. Transaction isolation ---


def test_read_operations_never_commit(
    reminder_module: ReminderModule, fake_uow: FakeUnitOfWork
):
    created = reminder_module.create_reminder(TASK_ID, REMIND_AT)
    fake_uow.commit_count = 0

    reminder_module.get_reminder(created.id)
    reminder_module.list_reminders_by_task(TASK_ID)

    assert fake_uow.commit_count == 0
    assert fake_uow.rollback_count == 0


def test_write_operations_commit_exactly_once_on_success(
    reminder_module: ReminderModule, fake_uow: FakeUnitOfWork
):
    created = reminder_module.create_reminder(TASK_ID, REMIND_AT)
    assert fake_uow.commit_count == 1

    reminder_module.update_reminder(created.id, status=ReminderStatus.CANCELLED)
    assert fake_uow.commit_count == 2

    reminder_module.delete_reminder(created.id)
    assert fake_uow.commit_count == 3
    assert fake_uow.rollback_count == 0


def test_module_uses_unit_of_work_repositories(fake_uow: FakeUnitOfWork):
    """The Module must wire ReminderService from the UoW's own repositories."""
    module = ReminderModule(fake_uow)  # type: ignore[arg-type]

    assert module._service._reminder_repository is fake_uow.reminders
    assert module._service._task_repository is fake_uow.tasks


def test_module_persists_through_unit_of_work_repository(
    reminder_module: ReminderModule, fake_uow: FakeUnitOfWork
):
    created = reminder_module.create_reminder(TASK_ID, REMIND_AT)

    assert fake_uow.reminders.get_by_id(created.id) is created
