"""Tests for TaskService.

Verifies business logic independently from SQLAlchemy / database Session,
confirming repository delegation, field update rules, error handling,
and absence of commit/rollback calls.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from personal_deadline_management_agent.exceptions.task import TaskNotFoundError
from personal_deadline_management_agent.models import Task, TaskPriority, TaskStatus
from personal_deadline_management_agent.repositories.task_repository import TaskRepository
from personal_deadline_management_agent.services.task_service import TaskService


class FakeTaskRepository:
    """In-memory fake repository to test TaskService in complete isolation."""

    def __init__(self) -> None:
        self.tasks: dict[uuid.UUID, Task] = {}
        self.create_called = False
        self.update_called = False
        self.delete_called = False

    def create(self, task: Task) -> Task:
        self.create_called = True
        if task.id is None:
            task.id = uuid.uuid4()
        self.tasks[task.id] = task
        return task

    def get_by_id(self, task_id: uuid.UUID) -> Task | None:
        return self.tasks.get(task_id)

    def list(self) -> list[Task]:
        return list(self.tasks.values())

    def update(self, task: Task) -> Task:
        self.update_called = True
        self.tasks[task.id] = task
        return task

    def delete(self, task_id: uuid.UUID) -> bool:
        self.delete_called = True
        if task_id in self.tasks:
            del self.tasks[task_id]
            return True
        return False


@pytest.fixture
def fake_repo() -> FakeTaskRepository:
    return FakeTaskRepository()


@pytest.fixture
def task_service(fake_repo: FakeTaskRepository) -> TaskService:
    return TaskService(fake_repo)  # type: ignore[arg-type]


# 1. create_task creates Task, status defaults to TODO, repository.create is called
def test_create_task_defaults_to_todo(task_service: TaskService, fake_repo: FakeTaskRepository):
    deadline = datetime.now(timezone.utc)
    task = task_service.create_task(
        task_name="Finish Report",
        description="Quarterly summary",
        deadline=deadline,
        priority=TaskPriority.HIGH,
    )

    assert fake_repo.create_called is True
    assert task.task_name == "Finish Report"
    assert task.description == "Quarterly summary"
    assert task.deadline == deadline
    assert task.priority == TaskPriority.HIGH.value
    assert task.status == TaskStatus.TODO.value


# 2. get_task returns existing Task
def test_get_task_success(task_service: TaskService):
    created = task_service.create_task(
        task_name="Existing Task",
        description=None,
        deadline=datetime.now(timezone.utc),
        priority=TaskPriority.LOW,
    )

    retrieved = task_service.get_task(created.id)
    assert retrieved.id == created.id
    assert retrieved.task_name == "Existing Task"


# 3. get_task raises TaskNotFoundError for missing Task
def test_get_task_not_found(task_service: TaskService):
    missing_id = uuid.uuid4()
    with pytest.raises(TaskNotFoundError) as exc_info:
        task_service.get_task(missing_id)
    assert exc_info.value.task_id == missing_id


# 4. list_tasks returns repository results
def test_list_tasks(task_service: TaskService):
    assert task_service.list_tasks() == []

    t1 = task_service.create_task("Task 1", None, datetime.now(timezone.utc), TaskPriority.LOW)
    t2 = task_service.create_task("Task 2", None, datetime.now(timezone.utc), TaskPriority.MEDIUM)

    all_tasks = task_service.list_tasks()
    assert len(all_tasks) == 2
    ids = {t.id for t in all_tasks}
    assert t1.id in ids
    assert t2.id in ids


# 5. update_task updates only supplied fields
def test_update_task_supplied_fields(task_service: TaskService, fake_repo: FakeTaskRepository):
    initial_deadline = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    created = task_service.create_task(
        task_name="Old Name",
        description="Old Description",
        deadline=initial_deadline,
        priority=TaskPriority.LOW,
    )

    new_deadline = datetime(2026, 9, 10, 15, 0, tzinfo=timezone.utc)
    updated = task_service.update_task(
        task_id=created.id,
        task_name="New Name",
        deadline=new_deadline,
        status=TaskStatus.IN_PROGRESS,
    )

    assert fake_repo.update_called is True
    assert updated.task_name == "New Name"
    assert updated.deadline == new_deadline
    assert updated.status == TaskStatus.IN_PROGRESS.value


# 6. update_task preserves fields whose values were not supplied
def test_update_task_preserves_unsupplied_fields(task_service: TaskService):
    initial_deadline = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    created = task_service.create_task(
        task_name="Original Name",
        description="Original Description",
        deadline=initial_deadline,
        priority=TaskPriority.MEDIUM,
    )

    # Only update priority, leave name, description, deadline, status None
    updated = task_service.update_task(
        task_id=created.id,
        priority=TaskPriority.HIGH,
    )

    assert updated.priority == TaskPriority.HIGH.value
    assert updated.task_name == "Original Name"
    assert updated.description == "Original Description"
    assert updated.deadline == initial_deadline
    assert updated.status == TaskStatus.TODO.value


# 7. update_task raises TaskNotFoundError for missing Task
def test_update_task_not_found(task_service: TaskService):
    missing_id = uuid.uuid4()
    with pytest.raises(TaskNotFoundError) as exc_info:
        task_service.update_task(task_id=missing_id, task_name="New Name")
    assert exc_info.value.task_id == missing_id


# 8. delete_task deletes existing Task
def test_delete_task_success(task_service: TaskService, fake_repo: FakeTaskRepository):
    created = task_service.create_task("To Delete", None, datetime.now(timezone.utc), TaskPriority.LOW)
    task_service.delete_task(created.id)

    assert fake_repo.delete_called is True
    assert fake_repo.get_by_id(created.id) is None


# 9. delete_task raises TaskNotFoundError for missing Task
def test_delete_task_not_found(task_service: TaskService):
    missing_id = uuid.uuid4()
    with pytest.raises(TaskNotFoundError) as exc_info:
        task_service.delete_task(missing_id)
    assert exc_info.value.task_id == missing_id


# 10. Verify Service does not call commit/rollback
def test_service_does_not_call_commit_or_rollback():
    """Verify that TaskService never attempts to call commit or rollback on anything."""
    mock_repo = MagicMock(spec=TaskRepository)
    mock_task = Task(
        task_name="Mock Task",
        description=None,
        deadline=datetime.now(timezone.utc),
        priority=TaskPriority.LOW.value,
        status=TaskStatus.TODO.value,
    )
    mock_task.id = uuid.uuid4()
    mock_repo.create.return_value = mock_task
    mock_repo.get_by_id.return_value = mock_task
    mock_repo.update.return_value = mock_task
    mock_repo.delete.return_value = True

    service = TaskService(mock_repo)

    # 1. create_task
    service.create_task("T", None, datetime.now(timezone.utc), TaskPriority.LOW)
    # 2. get_task
    service.get_task(mock_task.id)
    # 3. list_tasks
    service.list_tasks()
    # 4. update_task
    service.update_task(mock_task.id, task_name="Updated")
    # 5. delete_task
    service.delete_task(mock_task.id)

    # Verify no commit or rollback attribute was ever accessed or called
    assert not hasattr(service, "commit")
    assert not hasattr(service, "rollback")
    for mock_call in mock_repo.mock_calls:
        assert "commit" not in mock_call[0]
        assert "rollback" not in mock_call[0]
