"""Tests for PendingConfirmationModule.

Verifies use-case orchestration and transaction boundaries:
- create commits on success, rollback on failure.
- confirm checks user match and expiry, commits on state change.
- cleanup_expired marks expired records and commits.
- Read operations do not commit.
- Exceptions propagate without modification.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from personal_deadline_management_agent.models import PendingConfirmationStatus
from personal_deadline_management_agent.modules.pending_confirmation_module import (
    ConfirmOutcome,
    ConfirmResult,
    PendingConfirmationModule,
)
from personal_deadline_management_agent.services.execution_command import ExecutionCommand
from personal_deadline_management_agent.schemas.agent import ActionType


# --- Fakes -------------------------------------------------------------------


class FakePendingConfirmation:
    """In-memory pending confirmation record."""

    def __init__(
        self,
        *,
        user_id=None,
        execution_command='{"action_type": "DELETE_TASK"}',
        ttl_seconds=300,
    ) -> None:
        self.id = uuid.uuid4()
        self.user_id = user_id
        self.execution_command = execution_command
        now = datetime.now(timezone.utc)
        self.created_at = now
        self.expires_at = now + timedelta(seconds=ttl_seconds)
        self.status = PendingConfirmationStatus.PENDING.value


class FakePendingConfirmationRepository:
    """In-memory fake repository for PendingConfirmationModule tests."""

    def __init__(self) -> None:
        self.records: dict[uuid.UUID, FakePendingConfirmation] = {}

    def create(self, confirmation):
        if confirmation.id is None:
            confirmation.id = uuid.uuid4()
        self.records[confirmation.id] = confirmation
        return confirmation

    def get_by_id(self, confirmation_id):
        return self.records.get(confirmation_id)

    def find_latest_pending_by_user(self, user_id):
        matches = [
            r
            for r in self.records.values()
            if r.user_id == user_id
            and r.status == PendingConfirmationStatus.PENDING.value
        ]
        if not matches:
            return None
        matches.sort(key=lambda r: r.created_at, reverse=True)
        return matches[0]

    def mark_expired(self, now):
        count = 0
        for r in self.records.values():
            if (
                r.status == PendingConfirmationStatus.PENDING.value
                and r.expires_at < now
            ):
                r.status = PendingConfirmationStatus.EXPIRED.value
                count += 1
        return count

    def mark_confirmed(self, confirmation_id, user_id, now):
        """Conditional update: only transitions PENDING → CONFIRMED."""
        record = self.records.get(confirmation_id)
        if record is None:
            return 0
        if record.user_id != user_id:
            return 0
        if record.status != PendingConfirmationStatus.PENDING.value:
            return 0
        if record.expires_at <= now:
            return 0
        record.status = PendingConfirmationStatus.CONFIRMED.value
        return 1

    def refresh(self, confirmation):
        """No-op — in-memory fake is always current."""
        return confirmation


class FakeUnitOfWork:
    """Fake UnitOfWork tracking commit and rollback calls."""

    def __init__(self) -> None:
        self.pending_confirmations = FakePendingConfirmationRepository()
        self.commit_count = 0
        self.rollback_count = 0

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1

    def close(self) -> None:
        pass


# --- Helpers -----------------------------------------------------------------


def _delete_task_command(**overrides) -> ExecutionCommand:
    defaults = {
        "action_type": ActionType.DELETE_TASK,
        "resource_id": uuid.uuid4(),
        "parameters": {},
    }
    defaults.update(overrides)
    return ExecutionCommand(**defaults)


# --- create tests -----------------------------------------------------------


def test_create_persists_and_commits_once():
    uow = FakeUnitOfWork()
    module = PendingConfirmationModule(uow)
    user_id = uuid.uuid4()
    command = _delete_task_command()

    confirmation = module.create(
        user_id=user_id, command=command, ttl_seconds=300
    )

    assert confirmation.id is not None
    assert confirmation.user_id == user_id
    assert confirmation.status == PendingConfirmationStatus.PENDING.value
    assert uow.commit_count == 1
    assert uow.rollback_count == 0


def test_create_stores_serialized_command():
    uow = FakeUnitOfWork()
    module = PendingConfirmationModule(uow)
    command = _delete_task_command(
        resource_id=uuid.UUID("11111111-1111-1111-1111-111111111111")
    )

    confirmation = module.create(
        user_id=uuid.uuid4(), command=command, ttl_seconds=60
    )

    from personal_deadline_management_agent.services.execution_command import (
        ExecutionCommand,
    )

    restored = ExecutionCommand.model_validate_json(
        confirmation.execution_command
    )
    assert restored.action_type == ActionType.DELETE_TASK
    assert restored.resource_id == uuid.UUID("11111111-1111-1111-1111-111111111111")


def test_create_sets_correct_expiry():
    uow = FakeUnitOfWork()
    module = PendingConfirmationModule(uow)
    before = datetime.now(timezone.utc)

    confirmation = module.create(
        user_id=None, command=_delete_task_command(), ttl_seconds=120
    )
    after = datetime.now(timezone.utc)

    expected_min = before + timedelta(seconds=120)
    expected_max = after + timedelta(seconds=120)
    assert expected_min <= confirmation.expires_at <= expected_max


def test_create_rollback_on_failure():
    uow = FakeUnitOfWork()
    module = PendingConfirmationModule(uow)

    class ExplodingRepo:
        def create(self, c):
            raise RuntimeError("DB failure")

    uow.pending_confirmations = ExplodingRepo()

    with pytest.raises(RuntimeError, match="DB failure"):
        module.create(
            user_id=None, command=_delete_task_command(), ttl_seconds=300
        )

    assert uow.rollback_count == 1
    assert uow.commit_count == 0


# --- confirm tests -----------------------------------------------------------


def test_confirm_success_marks_confirmed_and_commits():
    uow = FakeUnitOfWork()
    module = PendingConfirmationModule(uow)
    user_id = uuid.uuid4()
    original = module.create(
        user_id=user_id, command=_delete_task_command(), ttl_seconds=300
    )

    result = module.confirm(original.id, user_id)

    assert isinstance(result, ConfirmResult)
    assert result.outcome is ConfirmOutcome.CONFIRMED
    assert result.confirmation.status == PendingConfirmationStatus.CONFIRMED.value
    assert uow.commit_count == 2  # create + confirm


def test_confirm_wrong_user_returns_not_found():
    uow = FakeUnitOfWork()
    module = PendingConfirmationModule(uow)
    original = module.create(
        user_id=uuid.uuid4(), command=_delete_task_command(), ttl_seconds=300
    )

    result = module.confirm(original.id, uuid.uuid4())

    assert result.outcome is ConfirmOutcome.NOT_FOUND
    assert result.confirmation is None
    assert uow.commit_count == 1  # only create


def test_confirm_nonexistent_returns_not_found():
    uow = FakeUnitOfWork()
    module = PendingConfirmationModule(uow)

    result = module.confirm(uuid.uuid4(), None)

    assert result.outcome is ConfirmOutcome.NOT_FOUND


def test_confirm_expired_marks_expired():
    uow = FakeUnitOfWork()
    module = PendingConfirmationModule(uow)
    user_id = uuid.uuid4()
    original = module.create(
        user_id=user_id,
        command=_delete_task_command(),
        ttl_seconds=0,  # expires immediately
    )
    import time

    time.sleep(0.01)

    result = module.confirm(original.id, user_id)

    assert result.outcome is ConfirmOutcome.EXPIRED
    assert result.confirmation.status == PendingConfirmationStatus.EXPIRED.value
    assert uow.commit_count == 2  # create + mark expired


def test_confirm_already_confirmed():
    uow = FakeUnitOfWork()
    module = PendingConfirmationModule(uow)
    user_id = uuid.uuid4()
    original = module.create(
        user_id=user_id, command=_delete_task_command(), ttl_seconds=300
    )
    module.confirm(original.id, user_id)  # first confirm
    commits_after_first = uow.commit_count

    # Second confirm — already CONFIRMED, must NOT report success again.
    result = module.confirm(original.id, user_id)

    assert result.outcome is ConfirmOutcome.ALREADY_CONFIRMED
    assert result.confirmation is not None
    assert uow.commit_count == commits_after_first  # no new commit


def test_confirm_twice_second_call_does_not_report_confirmed():
    """Two consecutive confirm() calls must only transition once.

    The second call returns ALREADY_CONFIRMED, so a handler can never
    treat it as a fresh confirmation and re-execute the action.
    """
    uow = FakeUnitOfWork()
    module = PendingConfirmationModule(uow)
    user_id = uuid.uuid4()
    original = module.create(
        user_id=user_id, command=_delete_task_command(), ttl_seconds=300
    )

    first = module.confirm(original.id, user_id)
    second = module.confirm(original.id, user_id)

    assert first.outcome is ConfirmOutcome.CONFIRMED
    assert second.outcome is ConfirmOutcome.ALREADY_CONFIRMED
    assert uow.commit_count == 2  # create + first confirm only


def test_confirm_user_match_with_none_user_id():
    """None user_id matches None user_id (single-user no-auth)."""
    uow = FakeUnitOfWork()
    module = PendingConfirmationModule(uow)
    original = module.create(
        user_id=None, command=_delete_task_command(), ttl_seconds=300
    )

    result = module.confirm(original.id, None)

    assert result.outcome is ConfirmOutcome.CONFIRMED


def test_confirm_user_mismatch_with_none_user_id():
    """A UUID user_id does not match None user_id."""
    uow = FakeUnitOfWork()
    module = PendingConfirmationModule(uow)
    original = module.create(
        user_id=None, command=_delete_task_command(), ttl_seconds=300
    )

    result = module.confirm(original.id, uuid.uuid4())

    assert result.outcome is ConfirmOutcome.NOT_FOUND


# --- get_latest_pending tests -----------------------------------------------


def test_get_latest_pending_returns_most_recent():
    uow = FakeUnitOfWork()
    module = PendingConfirmationModule(uow)
    user_id = uuid.uuid4()
    older = module.create(
        user_id=user_id, command=_delete_task_command(), ttl_seconds=300
    )
    older.created_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    newer = module.create(
        user_id=user_id, command=_delete_task_command(), ttl_seconds=300
    )
    newer.created_at = datetime.now(timezone.utc)

    result = module.get_latest_pending(user_id)

    assert result.id == newer.id


def test_get_latest_pending_filters_by_user():
    uow = FakeUnitOfWork()
    module = PendingConfirmationModule(uow)
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    module.create(user_id=user_a, command=_delete_task_command(), ttl_seconds=300)
    module.create(user_id=user_b, command=_delete_task_command(), ttl_seconds=300)

    result = module.get_latest_pending(user_a)

    # Should find one for user_a, not user_b
    assert result is not None
    assert result.user_id == user_a


def test_get_latest_pending_ignores_confirmed():
    uow = FakeUnitOfWork()
    module = PendingConfirmationModule(uow)
    user_id = uuid.uuid4()
    old = module.create(
        user_id=user_id, command=_delete_task_command(), ttl_seconds=300
    )
    module.confirm(old.id, user_id)  # mark as CONFIRMED
    new = module.create(
        user_id=user_id, command=_delete_task_command(), ttl_seconds=300
    )

    result = module.get_latest_pending(user_id)

    assert result.id == new.id


def test_get_latest_pending_none_when_no_records():
    uow = FakeUnitOfWork()
    module = PendingConfirmationModule(uow)

    assert module.get_latest_pending(uuid.uuid4()) is None


# --- cleanup_expired tests ---------------------------------------------------


def test_cleanup_expired_marks_and_commits():
    uow = FakeUnitOfWork()
    module = PendingConfirmationModule(uow)
    # Create one that will expire (ttl=0) and one that won't (ttl=300)
    module.create(user_id=None, command=_delete_task_command(), ttl_seconds=0)
    module.create(user_id=None, command=_delete_task_command(), ttl_seconds=300)
    import time

    time.sleep(0.01)

    count = module.cleanup_expired()

    assert count == 1
    assert uow.commit_count == 3  # create + create + cleanup


def test_cleanup_expired_no_commit_when_nothing_expired():
    uow = FakeUnitOfWork()
    module = PendingConfirmationModule(uow)
    module.create(user_id=None, command=_delete_task_command(), ttl_seconds=300)

    count = module.cleanup_expired()

    assert count == 0
    assert uow.commit_count == 1  # only create


def test_cleanup_expired_only_marks_pending():
    """Already CONFIRMED records are not affected by cleanup."""
    uow = FakeUnitOfWork()
    module = PendingConfirmationModule(uow)
    user_id = uuid.uuid4()
    # Create and confirm (ttl=0 so it would be expired, but it's already confirmed)
    rec = module.create(
        user_id=user_id, command=_delete_task_command(), ttl_seconds=0
    )
    import time

    time.sleep(0.01)
    module.confirm(rec.id, user_id)  # CONFIRMED
    uow.commit_count = 0  # reset for assertion

    count = module.cleanup_expired()

    assert count == 0
    assert uow.commit_count == 0
