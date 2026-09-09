"""Tests for the PendingConfirmation domain model.

Verifies the enum, table structure, columns, indexes, and DDL validity
using in-memory SQLite (no live PostgreSQL required).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from genai_core.genai_shared.database import Base
from personal_deadline_management_agent.models import (
    PendingConfirmation,
    PendingConfirmationStatus,
)


# --- Enum tests ---


def test_pending_confirmation_status_enum_values():
    assert PendingConfirmationStatus.PENDING.value == "PENDING"
    assert PendingConfirmationStatus.CONFIRMED.value == "CONFIRMED"
    assert PendingConfirmationStatus.EXPIRED.value == "EXPIRED"
    assert PendingConfirmationStatus.CANCELLED.value == "CANCELLED"


def test_pending_confirmation_status_members():
    assert {m.name for m in PendingConfirmationStatus} == {
        "PENDING",
        "CONFIRMED",
        "EXPIRED",
        "CANCELLED",
    }


def test_pending_confirmation_status_is_str_subclass():
    assert issubclass(PendingConfirmationStatus, str)


# --- Table metadata tests ---


def test_pending_confirmation_table_exists_in_metadata():
    assert "pending_confirmations" in Base.metadata.tables


def test_pending_confirmation_model_tablename():
    assert PendingConfirmation.__tablename__ == "pending_confirmations"


def test_pending_confirmation_columns():
    columns = {c.name for c in PendingConfirmation.__table__.columns}
    expected = {
        "id",
        "user_id",
        "execution_command",
        "created_at",
        "expires_at",
        "status",
        "updated_at",
    }
    assert columns == expected


def test_pending_confirmation_primary_key():
    pk_cols = [c.name for c in PendingConfirmation.__table__.primary_key.columns]
    assert pk_cols == ["id"]


def test_pending_confirmation_required_columns_not_nullable():
    for name in ("id", "execution_command", "created_at", "expires_at", "status"):
        assert PendingConfirmation.__table__.c[name].nullable is False, name


def test_pending_confirmation_user_id_is_nullable():
    assert PendingConfirmation.__table__.c["user_id"].nullable is True


# --- Index tests ---


def test_pending_confirmation_indexes():
    indexes = {idx.name for idx in PendingConfirmation.__table__.indexes}
    assert "ix_pending_confirmations_user_id" in indexes
    assert "ix_pending_confirmations_status" in indexes
    assert "ix_pending_confirmations_expires_at" in indexes


def test_pending_confirmation_index_columns():
    idx_map = {
        idx.name: tuple(idx.columns) for idx in PendingConfirmation.__table__.indexes
    }
    assert [c.name for c in idx_map["ix_pending_confirmations_user_id"]] == [
        "user_id"
    ]
    assert [c.name for c in idx_map["ix_pending_confirmations_status"]] == [
        "status"
    ]


# --- DDL / persistence tests ---


def test_create_all_pending_confirmations_table():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    tables = set(Base.metadata.tables.keys())
    assert "pending_confirmations" in tables
    engine.dispose()


def test_pending_confirmation_can_persist():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    now = datetime.now(timezone.utc)
    from datetime import timedelta

    conf = PendingConfirmation(
        user_id=None,
        execution_command='{"action_type": "DELETE_TASK"}',
        created_at=now,
        expires_at=now + timedelta(minutes=5),
        status=PendingConfirmationStatus.PENDING.value,
    )
    with Session() as session:
        session.add(conf)
        session.flush()
        assert conf.id is not None
        assert conf.status == "PENDING"
    engine.dispose()
