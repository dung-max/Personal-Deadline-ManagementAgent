"""Tests for TaskModule.

Verifies use-case orchestration and transaction boundaries:
- Write operations commit on success and rollback on failure.
- Read operations do not commit.
- Exceptions propagate without modification.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from personal_deadline_management_agent.exceptions.task import TaskNotFoundError
from personal_deadline_management_agent.models import Task, TaskPriority, TaskStatus
from personal_deadline_management_agent.modules.task_module import TaskModule
from personal_deadline_management_agent.repositories.task_repository import TaskRepository
from personal_deadline_management_agent.uow import UnitOfWork


class FakeTaskRepository:
    """In-memory fake repository for TaskModule tests."""

    def __init__(self) -> None:
        self.tasks: dict[uuid.UUID, Task] = {}

    def create(self, task: Task) -> Task:
        if task.id is None:
            task.id = uuid.uuid4()
        self.tasks[task.id] = task
        return task

    def get_by_id(self, task_id: uuid.UUID) -> Task | None:
        return self.tasks.get(task_id)

    def list(self) -> list[Task]:
        return list(self.tasks.values())

    def update(self, task: Task) -> Task:
        self.tasks[task.id] = task
        return task

    def delete(self, task_id: uuid.UUID) -> bool:
        if task_id in self.tasks:
            del self.tasks[task_id]
            return True
        return False


class FakeUnitOfWork:
    """Fake UnitOfWork tracking commit and rollback calls."""

    def __init__(self, tasks_repo: FakeTaskRepository | None = None) -> None:
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


@pytest.fixture
def fake_uow() -> FakeUnitOfWork:
    return FakeUnitOfWork()


@pytest.fixture
def task_module(fake_uow: FakeUnitOfWork) -> TaskModule:
    return TaskModule(fake_uow)  # type: ignore[arg-type]


# --- 1. Create Tests ---


def test_create_task_success(task_module: TaskModule, fake_uow: FakeUnitOfWork):
    deadline = datetime.now(timezone.utc)
    task = task_module.create_task(
        task_name="Module Create",
        description="Desc",
        deadline=deadline,
        priority=TaskPriority.HIGH,
    )

    assert task.task_name == "Module Create"
    assert task.status == TaskStatus.TODO.value
    assert fake_uow.commit_count == 1
    assert fake_uow.rollback_count == 0


def test_create_task_failure_triggers_rollback(fake_uow: FakeUnitOfWork):
    # Make repository.create raise an error
    fake_uow.tasks.create = MagicMock(side_effect=RuntimeError("DB write failed"))  # type: ignore[assignment]
    module = TaskModule(fake_uow)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="DB write failed"):
        module.create_task(
            task_name="Fail Task",
            description=None,
            deadline=datetime.now(timezone.utc),
            priority=TaskPriority.LOW,
        )

    assert fake_uow.commit_count == 0
    assert fake_uow.rollback_count == 1


# --- 2. Get Tests ---


def test_get_task_success(task_module: TaskModule, fake_uow: FakeUnitOfWork):
    created = task_module.create_task(
        task_name="Get Me",
        description=None,
        deadline=datetime.now(timezone.utc),
        priority=TaskPriority.LOW,
    )
    fake_uow.commit_count = 0  # reset after create

    found = task_module.get_task(created.id)
    assert found.id == created.id
    assert found.task_name == "Get Me"
    assert fake_uow.commit_count == 0
    assert fake_uow.rollback_count == 0


def test_get_task_not_found_propagates(task_module: TaskModule, fake_uow: FakeUnitOfWork):
    missing_id = uuid.uuid4()
    with pytest.raises(TaskNotFoundError) as exc_info:
        task_module.get_task(missing_id)

    assert exc_info.value.task_id == missing_id
    assert fake_uow.commit_count == 0
    assert fake_uow.rollback_count == 0


# --- 3. List Tests ---


def test_list_tasks_does_not_commit(task_module: TaskModule, fake_uow: FakeUnitOfWork):
    task_module.create_task("T1", None, datetime.now(timezone.utc), TaskPriority.LOW)
    task_module.create_task("T2", None, datetime.now(timezone.utc), TaskPriority.HIGH)
    fake_uow.commit_count = 0

    tasks = task_module.list_tasks()
    assert len(tasks) == 2
    assert fake_uow.commit_count == 0
    assert fake_uow.rollback_count == 0


# --- 4. Update Tests ---


def test_update_task_success(task_module: TaskModule, fake_uow: FakeUnitOfWork):
    created = task_module.create_task("Original", None, datetime.now(timezone.utc), TaskPriority.LOW)
    fake_uow.commit_count = 0

    updated = task_module.update_task(created.id, task_name="Updated", status=TaskStatus.IN_PROGRESS)
    assert updated.task_name == "Updated"
    assert updated.status == TaskStatus.IN_PROGRESS.value
    assert fake_uow.commit_count == 1
    assert fake_uow.rollback_count == 0


def test_update_task_failure_triggers_rollback(fake_uow: FakeUnitOfWork):
    module = TaskModule(fake_uow)  # type: ignore[arg-type]
    created = module.create_task("To Update", None, datetime.now(timezone.utc), TaskPriority.LOW)
    fake_uow.commit_count = 0

    # Simulate failure on repository.update
    fake_uow.tasks.update = MagicMock(side_effect=RuntimeError("Update failed"))  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="Update failed"):
        module.update_task(created.id, task_name="Crash")

    assert fake_uow.commit_count == 0
    assert fake_uow.rollback_count == 1


def test_update_task_not_found_rolls_back_and_propagates(task_module: TaskModule, fake_uow: FakeUnitOfWork):
    missing_id = uuid.uuid4()
    with pytest.raises(TaskNotFoundError):
        task_module.update_task(missing_id, task_name="Ghost")

    assert fake_uow.commit_count == 0
    assert fake_uow.rollback_count == 1


# --- 5. Delete Tests ---


def test_delete_task_success(task_module: TaskModule, fake_uow: FakeUnitOfWork):
    created = task_module.create_task("To Delete", None, datetime.now(timezone.utc), TaskPriority.LOW)
    fake_uow.commit_count = 0

    task_module.delete_task(created.id)
    assert fake_uow.commit_count == 1
    assert fake_uow.rollback_count == 0
    assert fake_uow.tasks.get_by_id(created.id) is None


def test_delete_task_not_found_rolls_back_and_propagates(task_module: TaskModule, fake_uow: FakeUnitOfWork):
    missing_id = uuid.uuid4()
    with pytest.raises(TaskNotFoundError):
        task_module.delete_task(missing_id)

    assert fake_uow.commit_count == 0
    assert fake_uow.rollback_count == 1


# --- 6. Transaction Isolation Verification ---


def test_read_operations_never_commit(task_module: TaskModule, fake_uow: FakeUnitOfWork):
    fake_uow.commit_count = 0
    task_module.list_tasks()
    assert fake_uow.commit_count == 0

    missing_id = uuid.uuid4()
    with pytest.raises(TaskNotFoundError):
        task_module.get_task(missing_id)
    assert fake_uow.commit_count == 0


def test_write_operations_commit_exactly_once_on_success(task_module: TaskModule, fake_uow: FakeUnitOfWork):
    fake_uow.commit_count = 0
    task = task_module.create_task("W1", None, datetime.now(timezone.utc), TaskPriority.LOW)
    assert fake_uow.commit_count == 1

    task_module.update_task(task.id, task_name="W2")
    assert fake_uow.commit_count == 2

    task_module.delete_task(task.id)
    assert fake_uow.commit_count == 3
    assert fake_uow.rollback_count == 0


def test_write_failures_never_commit_and_always_rollback(fake_uow: FakeUnitOfWork):
    fake_uow.tasks.create = MagicMock(side_effect=ValueError("Boom"))  # type: ignore[assignment]
    module = TaskModule(fake_uow)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Boom"):
        module.create_task("X", None, datetime.now(timezone.utc), TaskPriority.LOW)

    assert fake_uow.commit_count == 0
    assert fake_uow.rollback_count == 1
