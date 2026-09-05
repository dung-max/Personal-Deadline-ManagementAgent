"""Tests for the ReminderRepository.

Verifies persistence operations (create, get, list_by_task_id, update, delete)
and confirms that the repository does NOT commit transactions.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from genai_core.genai_shared.database import Base
from personal_deadline_management_agent.models import (
    Reminder,
    ReminderStatus,
    Task,
    TaskPriority,
    TaskStatus,
)
from personal_deadline_management_agent.repositories.reminder_repository import (
    ReminderRepository,
)


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
        engine.dispose()


@pytest.fixture
def repository(db_session: Session) -> ReminderRepository:
    return ReminderRepository(db_session)


def _make_task(session: Session, name: str = "Test Task") -> Task:
    task = Task(
        task_name=name,
        description="Test Description",
        deadline=datetime.now(timezone.utc) + timedelta(days=1),
        priority=TaskPriority.MEDIUM.value,
        status=TaskStatus.TODO.value,
    )
    session.add(task)
    session.flush()
    return task


@pytest.fixture
def task(db_session: Session) -> Task:
    return _make_task(db_session)


def _make_reminder(
    task_id: uuid.UUID,
    remind_at: datetime | None = None,
    status: str = ReminderStatus.PENDING.value,
) -> Reminder:
    return Reminder(
        task_id=task_id,
        remind_at=remind_at or (datetime.now(timezone.utc) + timedelta(hours=1)),
        status=status,
    )


# 1. create Reminder
def test_create_reminder(repository: ReminderRepository, task: Task):
    remind_at = datetime.now(timezone.utc) + timedelta(hours=2)
    created = repository.create(_make_reminder(task.id, remind_at))

    assert created.id is not None
    assert isinstance(created.id, uuid.UUID)
    assert created.task_id == task.id
    assert created.status == ReminderStatus.PENDING.value
    assert created.created_at is not None
    assert created.updated_at is not None


def test_create_reminder_persists_values(
    repository: ReminderRepository, db_session: Session, task: Task
):
    remind_at = datetime.now(timezone.utc) + timedelta(hours=3)
    created = repository.create(
        _make_reminder(task.id, remind_at, ReminderStatus.SENT.value)
    )

    db_session.expire_all()
    fetched = repository.get_by_id(created.id)
    assert fetched is not None
    assert fetched.task_id == task.id
    assert fetched.status == ReminderStatus.SENT.value


# 2. get existing Reminder
def test_get_existing_reminder(repository: ReminderRepository, task: Task):
    created = repository.create(_make_reminder(task.id))

    found = repository.get_by_id(created.id)
    assert found is not None
    assert found.id == created.id
    assert found.task_id == task.id


# 3. get non-existing Reminder
def test_get_non_existing_reminder(repository: ReminderRepository):
    found = repository.get_by_id(uuid.uuid4())
    assert found is None


# 4. list_by_task_id
def test_list_by_task_id_empty(repository: ReminderRepository, task: Task):
    assert repository.list_by_task_id(task.id) == []


def test_list_by_task_id_returns_only_matching_task(
    repository: ReminderRepository, db_session: Session, task: Task
):
    other_task = _make_task(db_session, "Other Task")

    mine1 = repository.create(_make_reminder(task.id))
    mine2 = repository.create(_make_reminder(task.id))
    theirs = repository.create(_make_reminder(other_task.id))

    results = repository.list_by_task_id(task.id)
    ids = {r.id for r in results}
    assert ids == {mine1.id, mine2.id}
    assert theirs.id not in ids


def test_list_by_task_id_orders_by_remind_at_ascending(
    repository: ReminderRepository, task: Task
):
    now = datetime.now(timezone.utc)
    later = repository.create(_make_reminder(task.id, now + timedelta(hours=5)))
    earliest = repository.create(_make_reminder(task.id, now + timedelta(hours=1)))
    middle = repository.create(_make_reminder(task.id, now + timedelta(hours=3)))

    results = repository.list_by_task_id(task.id)
    assert [r.id for r in results] == [earliest.id, middle.id, later.id]


def test_list_by_task_id_unknown_task(repository: ReminderRepository):
    assert repository.list_by_task_id(uuid.uuid4()) == []


# 5. update Reminder
def test_update_reminder(repository: ReminderRepository, task: Task):
    reminder = repository.create(_make_reminder(task.id))
    new_remind_at = datetime.now(timezone.utc) + timedelta(days=2)
    reminder.remind_at = new_remind_at
    reminder.status = ReminderStatus.SENT.value

    updated = repository.update(reminder)
    assert updated.status == ReminderStatus.SENT.value

    fetched = repository.get_by_id(reminder.id)
    assert fetched is not None
    assert fetched.status == ReminderStatus.SENT.value


def test_update_reminder_to_cancelled(repository: ReminderRepository, task: Task):
    reminder = repository.create(_make_reminder(task.id))
    reminder.status = ReminderStatus.CANCELLED.value

    updated = repository.update(reminder)
    assert updated.status == ReminderStatus.CANCELLED.value


# 6. delete existing Reminder
def test_delete_existing_reminder(repository: ReminderRepository, task: Task):
    reminder = repository.create(_make_reminder(task.id))
    deleted = repository.delete(reminder.id)

    assert deleted is True
    assert repository.get_by_id(reminder.id) is None


def test_delete_reminder_does_not_delete_task(
    repository: ReminderRepository, db_session: Session, task: Task
):
    reminder = repository.create(_make_reminder(task.id))
    assert repository.delete(reminder.id) is True
    assert db_session.get(Task, task.id) is not None


# 7. delete non-existing Reminder
def test_delete_non_existing_reminder(repository: ReminderRepository):
    assert repository.delete(uuid.uuid4()) is False


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
        bind=engine,
        class_=CommitSpySession,
        autoflush=False,
        autocommit=False,
        future=True,
    )
    spy_session = session_factory()

    task = _make_task(spy_session, "No Commit Task")
    assert not committed, "test setup must NOT commit"

    repo = ReminderRepository(spy_session)

    # 1. create should not commit
    reminder = repo.create(_make_reminder(task.id))
    assert not committed, "create() must NOT commit"

    # 2. list should not commit
    repo.list_by_task_id(task.id)
    assert not committed, "list_by_task_id() must NOT commit"

    # 3. update should not commit
    reminder.status = ReminderStatus.SENT.value
    repo.update(reminder)
    assert not committed, "update() must NOT commit"

    # 4. delete should not commit
    repo.delete(reminder.id)
    assert not committed, "delete() must NOT commit"

    spy_session.close()
    engine.dispose()


# 9. find_by_task_name (natural-language resolution support)
def test_find_by_task_name_matches_parent_task(
    repository: ReminderRepository, db_session: Session, task: Task
):
    report_task = _make_task(db_session, "Prepare the report")
    report_reminder = repository.create(_make_reminder(report_task.id))
    repository.create(_make_reminder(task.id))  # parent task name "Test Task"

    results = repository.find_by_task_name("report")
    assert [r.id for r in results] == [report_reminder.id]


def test_find_by_task_name_no_match(repository: ReminderRepository, task: Task):
    repository.create(_make_reminder(task.id))
    assert repository.find_by_task_name("nonexistent") == []


# 10. UnitOfWork integration
def test_uow_has_reminders_repository(db_session: Session):
    from personal_deadline_management_agent.uow import UnitOfWork

    uow = UnitOfWork(db_session)
    assert hasattr(uow, "reminders")
    assert isinstance(uow.reminders, ReminderRepository)


def test_uow_reminders_shares_session(db_session: Session, task: Task):
    from personal_deadline_management_agent.uow import UnitOfWork

    uow = UnitOfWork(db_session)
    created = uow.reminders.create(_make_reminder(task.id))

    assert db_session.get(Reminder, created.id) is not None
    assert uow.reminders.list_by_task_id(task.id) == [created]
