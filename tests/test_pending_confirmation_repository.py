"""Tests for PendingConfirmationRepository.

Verifies persistence operations against in-memory SQLite.  The repository
never commits or rolls back — transaction ownership stays with the UoW.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from genai_core.genai_shared.database import Base
from personal_deadline_management_agent.models import (
    PendingConfirmation,
    PendingConfirmationStatus,
)
from personal_deadline_management_agent.repositories.pending_confirmation_repository import (
    PendingConfirmationRepository,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as s:
        yield s
    engine.dispose()


@pytest.fixture
def repo(session):
    return PendingConfirmationRepository(session)


def _confirmation(
    *,
    user_id=None,
    ttl_seconds=300,
    status=PendingConfirmationStatus.PENDING.value,
) -> PendingConfirmation:
    now = datetime.now(timezone.utc)
    return PendingConfirmation(
        user_id=user_id,
        execution_command='{"action_type": "DELETE_TASK"}',
        created_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
        status=status,
    )


def test_create_persists_values(repo, session):
    confirmation = _confirmation(user_id=uuid.uuid4())

    created = repo.create(confirmation)

    assert created.id is not None
    assert created.status == PendingConfirmationStatus.PENDING.value
    assert created.execution_command == '{"action_type": "DELETE_TASK"}'


def test_get_by_id_existing(repo, session):
    confirmation = _confirmation()
    repo.create(confirmation)

    found = repo.get_by_id(confirmation.id)

    assert found is not None
    assert found.id == confirmation.id


def test_get_by_id_non_existing(repo, session):
    assert repo.get_by_id(uuid.uuid4()) is None


def test_find_latest_pending_by_user_returns_most_recent(repo, session):
    user_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    older = _confirmation(user_id=user_id)
    older.created_at = now - timedelta(minutes=1)
    repo.create(older)
    newer = _confirmation(user_id=user_id)
    newer.created_at = now
    repo.create(newer)

    found = repo.find_latest_pending_by_user(user_id)

    assert found.id == newer.id


def test_find_latest_pending_by_user_filters_status(repo, session):
    user_id = uuid.uuid4()
    confirmed = _confirmation(
        user_id=user_id, status=PendingConfirmationStatus.CONFIRMED.value
    )
    repo.create(confirmed)
    pending = _confirmation(user_id=user_id)
    repo.create(pending)

    found = repo.find_latest_pending_by_user(user_id)

    assert found.id == pending.id


def test_find_latest_pending_by_user_filters_user(repo, session):
    repo.create(_confirmation(user_id=uuid.uuid4()))

    assert repo.find_latest_pending_by_user(uuid.uuid4()) is None


def test_find_latest_pending_by_user_none_user_id(repo, session):
    repo.create(_confirmation(user_id=None))

    found = repo.find_latest_pending_by_user(None)

    assert found is not None
    assert found.user_id is None


def test_mark_expired_updates_only_expired_pending(repo, session):
    now = datetime.now(timezone.utc)
    expired = _confirmation(ttl_seconds=0)
    repo.create(expired)
    valid = _confirmation(ttl_seconds=300)
    repo.create(valid)
    confirmed_expired = _confirmation(
        ttl_seconds=0, status=PendingConfirmationStatus.CONFIRMED.value
    )
    repo.create(confirmed_expired)

    count = repo.mark_expired(now + timedelta(seconds=1))

    assert count == 1
    session.expire_all()
    assert repo.get_by_id(expired.id).status == PendingConfirmationStatus.EXPIRED.value
    assert repo.get_by_id(valid.id).status == PendingConfirmationStatus.PENDING.value
    assert (
        repo.get_by_id(confirmed_expired.id).status
        == PendingConfirmationStatus.CONFIRMED.value
    )


def test_mark_expired_returns_zero_when_nothing_expired(repo, session):
    repo.create(_confirmation(ttl_seconds=300))

    count = repo.mark_expired(datetime.now(timezone.utc))

    assert count == 0


def test_mark_confirmed_transitions_pending(repo, session):
    user_id = uuid.uuid4()
    confirmation = _confirmation(user_id=user_id)
    repo.create(confirmation)

    count = repo.mark_confirmed(
        confirmation.id, user_id, datetime.now(timezone.utc)
    )

    assert count == 1
    session.expire_all()
    assert (
        repo.get_by_id(confirmation.id).status
        == PendingConfirmationStatus.CONFIRMED.value
    )


def test_mark_confirmed_zero_when_already_confirmed(repo, session):
    user_id = uuid.uuid4()
    confirmation = _confirmation(
        user_id=user_id, status=PendingConfirmationStatus.CONFIRMED.value
    )
    repo.create(confirmation)

    count = repo.mark_confirmed(
        confirmation.id, user_id, datetime.now(timezone.utc)
    )

    assert count == 0


def test_mark_confirmed_zero_when_expired(repo, session):
    user_id = uuid.uuid4()
    confirmation = _confirmation(user_id=user_id, ttl_seconds=0)
    repo.create(confirmation)

    count = repo.mark_confirmed(
        confirmation.id, user_id, datetime.now(timezone.utc) + timedelta(seconds=1)
    )

    assert count == 0


def test_mark_confirmed_zero_when_wrong_user(repo, session):
    confirmation = _confirmation(user_id=uuid.uuid4())
    repo.create(confirmation)

    count = repo.mark_confirmed(
        confirmation.id, uuid.uuid4(), datetime.now(timezone.utc)
    )

    assert count == 0


def test_mark_confirmed_zero_when_not_found(repo, session):
    count = repo.mark_confirmed(
        uuid.uuid4(), None, datetime.now(timezone.utc)
    )

    assert count == 0


def test_repository_does_not_commit(repo, session):
    """The repository must never commit — that is the UoW's job."""
    confirmation = _confirmation()
    repo.create(confirmation)
    # No commit happened; a rollback would discard the row.
    session.rollback()
    assert repo.get_by_id(confirmation.id) is None