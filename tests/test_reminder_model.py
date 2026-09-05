"""Tests for the Reminder domain model.

Verifies the enum, table structure, columns, foreign key, indexes, and DDL
validity using in-memory SQLite (no live PostgreSQL required).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from genai_core.genai_shared.database import Base
from personal_deadline_management_agent.models import (
    Reminder,
    ReminderStatus,
    Task,
    TaskPriority,
    TaskStatus,
)


# --- Enum tests ---


def test_reminder_status_enum_values():
    assert ReminderStatus.PENDING.value == "PENDING"
    assert ReminderStatus.SENT.value == "SENT"
    assert ReminderStatus.CANCELLED.value == "CANCELLED"


def test_reminder_status_members():
    assert {m.name for m in ReminderStatus} == {"PENDING", "SENT", "CANCELLED"}


def test_reminder_status_is_str_subclass():
    assert issubclass(ReminderStatus, str)


# --- Table metadata tests ---


def test_reminder_table_exists_in_metadata():
    assert "reminders" in Base.metadata.tables


def test_metadata_contains_tasks_and_reminders():
    assert {"tasks", "reminders"} <= set(Base.metadata.tables)


def test_reminder_model_tablename():
    assert Reminder.__tablename__ == "reminders"


def test_reminder_columns():
    columns = {c.name for c in Reminder.__table__.columns}
    expected = {
        "id",
        "task_id",
        "remind_at",
        "status",
        "created_at",
        "updated_at",
    }
    assert columns == expected


def test_reminder_primary_key():
    pk_cols = [c.name for c in Reminder.__table__.primary_key.columns]
    assert pk_cols == ["id"]


def test_reminder_required_columns_not_nullable():
    for name in ("id", "task_id", "remind_at", "status", "created_at", "updated_at"):
        assert Reminder.__table__.c[name].nullable is False, name


# --- Column type tests ---


def test_reminder_id_is_uuid():
    assert isinstance(Reminder.__table__.c.id.type, sqlalchemy.UUID)


def test_reminder_task_id_is_uuid():
    assert isinstance(Reminder.__table__.c.task_id.type, sqlalchemy.UUID)


def test_reminder_datetime_columns_are_timezone_aware():
    for name in ("remind_at", "created_at", "updated_at"):
        assert Reminder.__table__.c[name].type.timezone is True, name


def test_reminder_status_type_is_string():
    assert isinstance(Reminder.__table__.c.status.type, sqlalchemy.String)


def test_reminder_status_is_not_native_enum():
    assert not isinstance(Reminder.__table__.c.status.type, sqlalchemy.Enum)


def test_reminder_status_default_is_pending():
    default = Reminder.__table__.c.status.default
    assert default is not None
    assert default.arg == ReminderStatus.PENDING.value


# --- Foreign key tests ---


def test_reminder_task_id_foreign_key_target():
    fks = list(Reminder.__table__.c.task_id.foreign_keys)
    assert len(fks) == 1
    assert fks[0].target_fullname == "tasks.id"


def test_reminder_task_id_foreign_key_on_delete_cascade():
    fk = next(iter(Reminder.__table__.c.task_id.foreign_keys))
    assert fk.ondelete == "CASCADE"


def test_reminder_has_no_unique_constraint_on_task_id_remind_at():
    for constraint in Reminder.__table__.constraints:
        if isinstance(constraint, sqlalchemy.UniqueConstraint):
            cols = {c.name for c in constraint.columns}
            assert cols != {"task_id", "remind_at"}


# --- Index tests ---


def test_reminder_indexes():
    indexes = {idx.name for idx in Reminder.__table__.indexes}
    assert "ix_reminders_task_id" in indexes
    assert "ix_reminders_remind_at" in indexes


def test_reminder_index_columns():
    by_name = {idx.name: idx for idx in Reminder.__table__.indexes}
    assert [c.name for c in by_name["ix_reminders_task_id"].columns] == ["task_id"]
    assert [c.name for c in by_name["ix_reminders_remind_at"].columns] == ["remind_at"]


# --- DDL validity (in-memory SQLite) ---


@pytest.fixture
def sqlite_engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def test_create_all_reminders_table(sqlite_engine):
    with sqlite_engine.connect() as conn:
        result = conn.execute(
            sqlalchemy.text(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='reminders'"
            )
        )
        assert result.fetchone() is not None


def test_create_all_reminders_indexes(sqlite_engine):
    with sqlite_engine.connect() as conn:
        result = conn.execute(
            sqlalchemy.text(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND tbl_name='reminders'"
            )
        )
        index_names = {row[0] for row in result}
        assert "ix_reminders_task_id" in index_names
        assert "ix_reminders_remind_at" in index_names


def test_reminders_ddl_contains_on_delete_cascade(sqlite_engine):
    with sqlite_engine.connect() as conn:
        result = conn.execute(
            sqlalchemy.text(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='reminders'"
            )
        )
        ddl = result.scalar_one()
        assert "ON DELETE CASCADE" in ddl.upper()


# --- Relationship tests ---


def _make_task(name: str = "Relationship Task") -> Task:
    return Task(
        task_name=name,
        description="Task with reminders",
        deadline=datetime.now(timezone.utc) + timedelta(days=1),
        priority=TaskPriority.MEDIUM.value,
        status=TaskStatus.TODO.value,
    )


@pytest.fixture
def db_session(sqlite_engine):
    session_factory = sessionmaker(
        bind=sqlite_engine, autoflush=False, autocommit=False, future=True
    )
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def cascade_session():
    """SQLite enforces FKs only with PRAGMA foreign_keys=ON."""
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_fk(dbapi_connection, _):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

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


def test_task_supports_multiple_reminders(db_session):
    task = _make_task()
    now = datetime.now(timezone.utc)
    task.reminders = [
        Reminder(remind_at=now + timedelta(hours=1), status=ReminderStatus.PENDING.value),
        Reminder(remind_at=now + timedelta(hours=2), status=ReminderStatus.PENDING.value),
        Reminder(remind_at=now + timedelta(hours=3), status=ReminderStatus.SENT.value),
    ]
    db_session.add(task)
    db_session.flush()

    fetched = db_session.get(Task, task.id)
    assert fetched is not None
    assert len(fetched.reminders) == 3
    assert all(r.task_id == task.id for r in fetched.reminders)


def test_reminder_back_reference_to_task(db_session):
    task = _make_task("Back Reference Task")
    reminder = Reminder(
        remind_at=datetime.now(timezone.utc) + timedelta(hours=1),
        status=ReminderStatus.PENDING.value,
    )
    task.reminders.append(reminder)
    db_session.add(task)
    db_session.flush()

    assert reminder.task is task
    assert reminder.task_id == task.id


def test_reminder_requires_task_id(db_session):
    orphan = Reminder(
        remind_at=datetime.now(timezone.utc) + timedelta(hours=1),
        status=ReminderStatus.PENDING.value,
    )
    db_session.add(orphan)
    with pytest.raises(sqlalchemy.exc.IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_reminder_audit_timestamps_populated(db_session):
    task = _make_task("Audit Task")
    reminder = Reminder(remind_at=datetime.now(timezone.utc) + timedelta(hours=1))
    task.reminders.append(reminder)
    db_session.add(task)
    db_session.flush()
    db_session.refresh(reminder)

    assert reminder.id is not None
    assert isinstance(reminder.id, uuid.UUID)
    assert reminder.created_at is not None
    assert reminder.updated_at is not None
    assert reminder.status == ReminderStatus.PENDING.value


def test_task_delete_defers_to_database_fk_cascade(cascade_session):
    """The ORM must not null out reminders.task_id; the DB FK owns the cascade."""
    task = _make_task("Cascade Task")
    task.reminders.append(
        Reminder(remind_at=datetime.now(timezone.utc) + timedelta(hours=1))
    )
    cascade_session.add(task)
    cascade_session.flush()

    count = cascade_session.execute(
        sqlalchemy.text("SELECT COUNT(*) FROM reminders")
    ).scalar_one()
    assert count == 1

    cascade_session.delete(task)
    cascade_session.flush()

    count = cascade_session.execute(
        sqlalchemy.text("SELECT COUNT(*) FROM reminders")
    ).scalar_one()
    assert count == 0
