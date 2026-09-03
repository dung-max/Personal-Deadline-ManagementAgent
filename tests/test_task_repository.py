"""Tests for the TaskRepository.

Verifies persistence operations (create, get, list, update, delete)
and confirms that the repository does NOT commit transactions.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from genai_core.genai_shared.database import Base
from personal_deadline_management_agent.models import Task, TaskPriority, TaskStatus
from personal_deadline_management_agent.repositories.task_repository import TaskRepository


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, future=True
    )
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def repository(db_session: Session) -> TaskRepository:
    return TaskRepository(db_session)


def _make_task(
    name: str = "Test Task",
    desc: str | None = "Test Description",
    priority: str = TaskPriority.MEDIUM.value,
    status: str = TaskStatus.TODO.value,
) -> Task:
    return Task(
        task_name=name,
        description=desc,
        deadline=datetime.now(timezone.utc),
        priority=priority,
        status=status,
    )


# 1. create Task
def test_create_task(repository: TaskRepository, db_session: Session):
    task = _make_task("Create Test")
    created = repository.create(task)

    assert created.id is not None
    assert created.task_name == "Create Test"
    assert created.created_at is not None
    assert created.updated_at is not None


# 2. get existing Task
def test_get_existing_task(repository: TaskRepository):
    task = _make_task("Existing Task")
    created = repository.create(task)

    found = repository.get_by_id(created.id)
    assert found is not None
    assert found.id == created.id
    assert found.task_name == "Existing Task"


# 3. get non-existing Task
def test_get_non_existing_task(repository: TaskRepository):
    random_id = uuid.uuid4()
    found = repository.get_by_id(random_id)
    assert found is None


# 4. list Tasks
def test_list_tasks(repository: TaskRepository):
    assert repository.list() == []

    task1 = repository.create(_make_task("Task 1"))
    task2 = repository.create(_make_task("Task 2"))

    all_tasks = repository.list()
    assert len(all_tasks) == 2
    ids = {t.id for t in all_tasks}
    assert task1.id in ids
    assert task2.id in ids


# 5. update Task
def test_update_task(repository: TaskRepository):
    task = repository.create(_make_task("Original Name"))
    task.task_name = "Updated Name"
    task.status = TaskStatus.IN_PROGRESS.value

    updated = repository.update(task)
    assert updated.task_name == "Updated Name"
    assert updated.status == TaskStatus.IN_PROGRESS.value

    # verify persistence in session
    fetched = repository.get_by_id(task.id)
    assert fetched is not None
    assert fetched.task_name == "Updated Name"
    assert fetched.status == TaskStatus.IN_PROGRESS.value


# 6. delete existing Task
def test_delete_existing_task(repository: TaskRepository):
    task = repository.create(_make_task("To Delete"))
    deleted = repository.delete(task.id)

    assert deleted is True
    assert repository.get_by_id(task.id) is None


# 7. delete non-existing Task
def test_delete_non_existing_task(repository: TaskRepository):
    random_id = uuid.uuid4()
    deleted = repository.delete(random_id)

    assert deleted is False


# 8. Verify repository does NOT commit
def test_repository_does_not_commit():
    """Verify that create, update, and delete do not call session.commit()."""
    committed = False

    class CommitSpySession(Session):
        def commit(self):
            nonlocal committed
            committed = True
            super().commit()

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(
        bind=engine, class_=CommitSpySession, autoflush=False, autocommit=False, future=True
    )
    spy_session = session_factory()

    repo = TaskRepository(spy_session)

    # 1. create should not commit
    task = repo.create(_make_task("No Commit Create"))
    assert not committed, "create() must NOT commit"

    # 2. update should not commit
    task.task_name = "No Commit Update"
    repo.update(task)
    assert not committed, "update() must NOT commit"

    # 3. delete should not commit
    repo.delete(task.id)
    assert not committed, "delete() must NOT commit"

    spy_session.close()


def test_uow_has_tasks_repository(db_session: Session):
    from personal_deadline_management_agent.uow import UnitOfWork

    uow = UnitOfWork(db_session)
    assert hasattr(uow, "tasks")
    assert isinstance(uow.tasks, TaskRepository)
