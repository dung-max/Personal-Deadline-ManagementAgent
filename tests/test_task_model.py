"""Tests for the Task domain model.

Verifies enums, table structure, columns, indexes, and DDL validity
using in-memory SQLite (no live PostgreSQL required).
"""

from __future__ import annotations

import sqlalchemy
from sqlalchemy import create_engine

from genai_core.genai_shared.database import Base
from personal_deadline_management_agent.models import Task, TaskPriority, TaskStatus


# --- Enum tests ---


def test_task_priority_enum_values():
    assert TaskPriority.LOW.value == "LOW"
    assert TaskPriority.MEDIUM.value == "MEDIUM"
    assert TaskPriority.HIGH.value == "HIGH"


def test_task_status_enum_values():
    assert TaskStatus.TODO.value == "TODO"
    assert TaskStatus.IN_PROGRESS.value == "IN_PROGRESS"
    assert TaskStatus.COMPLETED.value == "COMPLETED"
    assert TaskStatus.CANCELLED.value == "CANCELLED"


def test_task_enums_are_str_subclass():
    assert issubclass(TaskPriority, str)
    assert issubclass(TaskStatus, str)


# --- Table metadata tests ---


def test_task_table_exists_in_metadata():
    assert "tasks" in Base.metadata.tables


def test_task_model_tablename():
    assert Task.__tablename__ == "tasks"


def test_task_columns():
    columns = {c.name for c in Task.__table__.columns}
    expected = {
        "id",
        "task_name",
        "description",
        "deadline",
        "priority",
        "status",
        "created_at",
        "updated_at",
    }
    assert columns == expected


def test_task_primary_key():
    pk_cols = [c.name for c in Task.__table__.primary_key.columns]
    assert pk_cols == ["id"]


def test_task_indexes():
    indexes = {idx.name for idx in Task.__table__.indexes}
    assert "ix_tasks_status" in indexes
    assert "ix_tasks_deadline" in indexes


# --- Column type tests ---


def test_task_name_type():
    assert isinstance(Task.__table__.c.task_name.type, sqlalchemy.String)
    assert Task.__table__.c.task_name.type.length == 255


def test_task_description_nullable():
    assert Task.__table__.c.description.nullable is True


def test_task_priority_type():
    assert isinstance(Task.__table__.c.priority.type, sqlalchemy.String)


def test_task_status_type():
    assert isinstance(Task.__table__.c.status.type, sqlalchemy.String)


def test_task_updated_at_not_null():
    assert Task.__table__.c.updated_at.nullable is False


def test_task_audit_columns_are_timezone_aware():
    assert Task.__table__.c.created_at.type.timezone is True
    assert Task.__table__.c.updated_at.type.timezone is True


# --- DDL validity (in-memory SQLite) ---


def test_create_all_tasks_table():
    """Verify DDL is valid by creating in-memory SQLite."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        result = conn.execute(
            sqlalchemy.text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'"
            )
        )
        assert result.fetchone() is not None


def test_create_all_tasks_indexes():
    """Verify indexes are created correctly in SQLite."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        result = conn.execute(
            sqlalchemy.text(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='tasks'"
            )
        )
        index_names = {row[0] for row in result}
        assert "ix_tasks_status" in index_names
        assert "ix_tasks_deadline" in index_names
