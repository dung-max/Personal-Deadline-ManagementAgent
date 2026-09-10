"""Tests for SchedulerModule.

Verifies transaction boundaries:
- Successful tick commits once (when due_count > 0).
- DB exception rolls back once (commit 0).
- Provider failure still commits once (SENT claims durable).
- Empty tick (due_count == 0) skips commit.
- Module owns transaction; service does not.
- Multiple reminders in one tick.
- End-to-end with real SQLite UoW.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from genai_core.genai_shared.database import Base
from personal_deadline_management_agent.models import (
    Reminder,
    ReminderStatus,
    Task,
    TaskPriority,
    TaskStatus,
)
from personal_deadline_management_agent.modules.scheduler_module import SchedulerModule
from personal_deadline_management_agent.services.scheduler_service import TickResult
from personal_deadline_management_agent.uow import UnitOfWork


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeReminder:
    def __init__(
        self,
        task_id: uuid.UUID,
        remind_at: datetime,
        status: str = ReminderStatus.PENDING.value,
    ) -> None:
        self.id = uuid.uuid4()
        self.task_id = task_id
        self.remind_at = remind_at
        self.status = status


class FakeTask:
    def __init__(self, task_id: uuid.UUID, task_name: str = "Test Task") -> None:
        self.id = task_id
        self.task_name = task_name


class FakeReminderRepository:
    def __init__(self) -> None:
        self._reminders: dict[uuid.UUID, FakeReminder] = {}
        self._mark_sent_returns_zero: set[uuid.UUID] = set()

    def register(self, reminder: FakeReminder) -> None:
        self._reminders[reminder.id] = reminder

    def mark_sent_returns_zero(self, reminder_id: uuid.UUID) -> None:
        self._mark_sent_returns_zero.add(reminder_id)

    def find_due(self, *, limit: int, now: datetime) -> list[FakeReminder]:
        return [
            r
            for r in self._reminders.values()
            if r.status == ReminderStatus.PENDING.value and r.remind_at <= now
        ][:limit]

    def mark_sent(self, reminder_id: uuid.UUID, now: datetime) -> int:
        if reminder_id in self._mark_sent_returns_zero:
            return 0
        reminder = self._reminders.get(reminder_id)
        if reminder is None:
            return 0
        if reminder.status != ReminderStatus.PENDING.value:
            return 0
        if not (reminder.remind_at <= now):
            return 0
        reminder.status = ReminderStatus.SENT.value
        return 1


class FakeTaskRepository:
    def __init__(self) -> None:
        self._tasks: dict[uuid.UUID, FakeTask] = {}

    def register(self, task: FakeTask) -> None:
        self._tasks[task.id] = task

    def get_by_id(self, task_id: uuid.UUID) -> FakeTask | None:
        return self._tasks.get(task_id)


class FakeNotificationProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[FakeReminder, FakeTask]] = []
        self.fail_on: set[uuid.UUID] = set()

    def fail_for(self, reminder_id: uuid.UUID) -> None:
        self.fail_on.add(reminder_id)

    def send(self, *, reminder: FakeReminder, task: FakeTask) -> None:
        self.calls.append((reminder, task))
        if reminder.id in self.fail_on:
            raise RuntimeError(f"Provider failure for reminder {reminder.id}")


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

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime.now(timezone.utc)


def _make_module(
    *,
    reminders: list[FakeReminder] | None = None,
    tasks: list[FakeTask] | None = None,
    notification_provider: FakeNotificationProvider | None = None,
    uow: FakeUnitOfWork | None = None,
) -> tuple[SchedulerModule, FakeUnitOfWork, FakeNotificationProvider]:
    """Create a SchedulerModule with in-memory fakes."""
    if uow is None:
        reminder_repo = FakeReminderRepository()
        task_repo = FakeTaskRepository()
        for r in reminders or []:
            reminder_repo.register(r)
        for t in tasks or []:
            task_repo.register(t)
        uow = FakeUnitOfWork(reminder_repo, task_repo)
    provider = notification_provider or FakeNotificationProvider()
    module = SchedulerModule(uow, provider)  # type: ignore[arg-type]
    return module, uow, provider


def _make_reminder(
    task_id: uuid.UUID,
    remind_at: datetime,
    status: str = ReminderStatus.PENDING.value,
) -> FakeReminder:
    return FakeReminder(task_id=task_id, remind_at=remind_at, status=status)


# ---------------------------------------------------------------------------
# 1. Successful tick commits once
# ---------------------------------------------------------------------------


def test_tick_success_commits_once():
    task = FakeTask(uuid.uuid4())
    reminder = _make_reminder(task.id, NOW - timedelta(hours=1))
    module, uow, _ = _make_module(reminders=[reminder], tasks=[task])

    result = module.tick(now=NOW, limit=100)

    assert result.sent_count == 1
    assert result.due_count == 1
    assert uow.commit_count == 1
    assert uow.rollback_count == 0


# ---------------------------------------------------------------------------
# 2. DB exception rolls back once (commit 0)
# ---------------------------------------------------------------------------


def test_tick_db_exception_rolls_back():
    task = FakeTask(uuid.uuid4())
    reminder = _make_reminder(task.id, NOW - timedelta(hours=1))
    module, uow, _ = _make_module(reminders=[reminder], tasks=[task])

    # Simulate DB failure during find_due.
    uow.reminders.find_due = MagicMock(side_effect=RuntimeError("DB failure"))  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="DB failure"):
        module.tick(now=NOW, limit=100)

    assert uow.commit_count == 0
    assert uow.rollback_count == 1


def test_tick_commit_exception_rolls_back():
    task = FakeTask(uuid.uuid4())
    reminder = _make_reminder(task.id, NOW - timedelta(hours=1))
    module, uow, _ = _make_module(reminders=[reminder], tasks=[task])

    uow.commit = MagicMock(side_effect=RuntimeError("Commit failure"))  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="Commit failure"):
        module.tick(now=NOW, limit=100)

    assert uow.rollback_count == 1


# ---------------------------------------------------------------------------
# 3. Provider failure still commits once (SENT claims durable)
# ---------------------------------------------------------------------------


def test_tick_provider_failure_still_commits():
    task = FakeTask(uuid.uuid4())
    reminder = _make_reminder(task.id, NOW - timedelta(hours=1))
    provider = FakeNotificationProvider()
    provider.fail_for(reminder.id)
    module, uow, _ = _make_module(
        reminders=[reminder], tasks=[task], notification_provider=provider
    )

    result = module.tick(now=NOW, limit=100)

    assert result.sent_count == 1
    assert result.failed_count == 1
    assert result.due_count == 1
    assert uow.commit_count == 1
    assert uow.rollback_count == 0


# ---------------------------------------------------------------------------
# 4. Empty tick (due_count == 0) skips commit
# ---------------------------------------------------------------------------


def test_tick_empty_skips_commit():
    module, uow, _ = _make_module()

    result = module.tick(now=NOW, limit=100)

    assert result.due_count == 0
    assert result.sent_count == 0
    assert uow.commit_count == 0
    assert uow.rollback_count == 0


def test_tick_future_reminders_empty_skips_commit():
    task = FakeTask(uuid.uuid4())
    future = _make_reminder(task.id, NOW + timedelta(hours=1))
    module, uow, _ = _make_module(reminders=[future], tasks=[task])

    result = module.tick(now=NOW, limit=100)

    assert result.due_count == 0
    assert uow.commit_count == 0
    assert uow.rollback_count == 0


# ---------------------------------------------------------------------------
# 5. Module owns transaction (service does not)
# ---------------------------------------------------------------------------


def test_module_owns_transaction():
    """Module is the only caller of commit/rollback."""
    task = FakeTask(uuid.uuid4())
    reminder = _make_reminder(task.id, NOW - timedelta(hours=1))
    module, uow, _ = _make_module(reminders=[reminder], tasks=[task])

    module.tick(now=NOW, limit=100)

    assert uow.commit_count == 1
    assert uow.rollback_count == 0


# ---------------------------------------------------------------------------
# 6. Multiple reminders in one tick
# ---------------------------------------------------------------------------


def test_tick_multiple_reminders_one_tick():
    task = FakeTask(uuid.uuid4())
    reminders = [_make_reminder(task.id, NOW - timedelta(hours=i)) for i in range(1, 6)]
    module, uow, provider = _make_module(reminders=reminders, tasks=[task])

    result = module.tick(now=NOW, limit=100)

    assert result.sent_count == 5
    assert result.due_count == 5
    assert uow.commit_count == 1
    assert len(provider.calls) == 5


# ---------------------------------------------------------------------------
# 7. End-to-end with real SQLite UoW
# ---------------------------------------------------------------------------


def test_tick_end_to_end_real_sqlite():
    """Real SQLite UoW: past reminder → SENT, future stays PENDING."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, future=True
    )
    session = session_factory()

    try:
        uow = UnitOfWork(session)

        # Create a task and two reminders (one past, one future).
        task = Task(
            task_name="E2E Task",
            description="End-to-end test",
            deadline=NOW + timedelta(days=1),
            priority=TaskPriority.MEDIUM.value,
            status=TaskStatus.TODO.value,
        )
        session.add(task)
        session.flush()

        past_reminder = Reminder(
            task_id=task.id,
            remind_at=NOW - timedelta(hours=1),
            status=ReminderStatus.PENDING.value,
        )
        future_reminder = Reminder(
            task_id=task.id,
            remind_at=NOW + timedelta(hours=1),
            status=ReminderStatus.PENDING.value,
        )
        session.add_all([past_reminder, future_reminder])
        session.flush()
        past_id = past_reminder.id
        future_id = future_reminder.id

        from personal_deadline_management_agent.adapters.notification import (
            ConsoleNotificationProvider,
        )

        provider = ConsoleNotificationProvider()
        module = SchedulerModule(uow, provider)

        result = module.tick(now=NOW, limit=100)

        assert result.sent_count == 1
        assert result.due_count == 1
        assert result.failed_count == 0
        assert result.skipped_count == 0

        # Verify DB state.
        session.expire_all()
        assert session.get(Reminder, past_id).status == ReminderStatus.SENT.value
        assert session.get(Reminder, future_id).status == ReminderStatus.PENDING.value

        # Second tick is idempotent.
        result2 = module.tick(now=NOW, limit=100)
        assert result2.due_count == 0
        assert result2.sent_count == 0

    finally:
        session.close()
        engine.dispose()


# ---------------------------------------------------------------------------
# 8. Provider failure + success in same tick (mixed)
# ---------------------------------------------------------------------------


def test_tick_mixed_provider_failure_and_success():
    task = FakeTask(uuid.uuid4())
    r1 = _make_reminder(task.id, NOW - timedelta(hours=2))
    r2 = _make_reminder(task.id, NOW - timedelta(hours=1))
    provider = FakeNotificationProvider()
    provider.fail_for(r1.id)
    module, uow, _ = _make_module(
        reminders=[r1, r2], tasks=[task], notification_provider=provider
    )

    result = module.tick(now=NOW, limit=100)

    assert result.sent_count == 2
    assert result.failed_count == 1
    assert result.due_count == 2
    assert uow.commit_count == 1
    assert uow.rollback_count == 0
