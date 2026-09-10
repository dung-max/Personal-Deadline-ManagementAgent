"""Tests for SchedulerService.

Verifies tick logic: due filtering, task resolution, atomic claim, provider
notification, and counting semantics (sent/failed/skipped/due).

Sent semantics (MVP):
    ``Reminder.status = SENT`` means the reminder was successfully
    processed/claimed by the scheduler — it does **NOT** guarantee external
    delivery.  Provider failure after claim increments both ``sent_count``
    and ``failed_count``; the reminder stays SENT (claim is durable).

Tick order:
    1. ``find_due(limit, now)``
    2. For each: resolve task → ``mark_sent`` → notify.
    3. Task missing → ``failed_count++``, skip (not claimed).
    4. ``mark_sent`` returns 0 → ``skipped_count++``, skip (race lost).
    5. Provider exception → ``sent_count++`` AND ``failed_count++``.

Transaction ownership:
    This service owns **no transaction** — it never calls ``commit`` or
    ``rollback``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from personal_deadline_management_agent.models import ReminderStatus
from personal_deadline_management_agent.services.scheduler_service import (
    SchedulerService,
    TickResult,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeReminder:
    """In-memory reminder for service tests."""

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
    """In-memory task for service tests."""

    def __init__(self, task_id: uuid.UUID, task_name: str = "Test Task") -> None:
        self.id = task_id
        self.task_name = task_name


class FakeReminderRepository:
    """In-memory fake implementing find_due and mark_sent."""

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
    """In-memory fake for TaskRepository.get_by_id."""

    def __init__(self) -> None:
        self._tasks: dict[uuid.UUID, FakeTask] = {}

    def register(self, task: FakeTask) -> None:
        self._tasks[task.id] = task

    def get_by_id(self, task_id: uuid.UUID) -> FakeTask | None:
        return self._tasks.get(task_id)


class FakeNotificationProvider:
    """In-memory fake that records calls and optionally raises."""

    def __init__(self) -> None:
        self.calls: list[tuple[FakeReminder, FakeTask]] = []
        self.fail_on: set[uuid.UUID] = set()

    def fail_for(self, reminder_id: uuid.UUID) -> None:
        self.fail_on.add(reminder_id)

    def send(self, *, reminder: FakeReminder, task: FakeTask) -> None:
        self.calls.append((reminder, task))
        if reminder.id in self.fail_on:
            raise RuntimeError(f"Provider failure for reminder {reminder.id}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime.now(timezone.utc)


def _make_service(
    *,
    reminders: list[FakeReminder] | None = None,
    tasks: list[FakeTask] | None = None,
    notification_provider: FakeNotificationProvider | None = None,
) -> tuple[
    SchedulerService,
    FakeReminderRepository,
    FakeTaskRepository,
    FakeNotificationProvider,
]:
    """Create a SchedulerService with in-memory fakes."""
    reminder_repo = FakeReminderRepository()
    task_repo = FakeTaskRepository()
    provider = notification_provider or FakeNotificationProvider()

    for r in reminders or []:
        reminder_repo.register(r)
    for t in tasks or []:
        task_repo.register(t)

    service = SchedulerService(reminder_repo, task_repo, provider)
    return service, reminder_repo, task_repo, provider


def _make_reminder(
    task_id: uuid.UUID,
    remind_at: datetime,
    status: str = ReminderStatus.PENDING.value,
) -> FakeReminder:
    return FakeReminder(task_id=task_id, remind_at=remind_at, status=status)


# ---------------------------------------------------------------------------
# 1. TickResult frozen dataclass
# ---------------------------------------------------------------------------


def test_tick_result_is_frozen():
    result = TickResult(sent_count=0, failed_count=0, skipped_count=0, due_count=0)
    with pytest.raises(AttributeError):
        result.sent_count = 1  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2. No due reminders
# ---------------------------------------------------------------------------


def test_tick_no_due_reminders_returns_all_zero():
    task = FakeTask(uuid.uuid4())
    future_reminder = _make_reminder(task.id, NOW + timedelta(hours=1))
    service, _, _, provider = _make_service(
        reminders=[future_reminder], tasks=[task]
    )

    result = service.tick(now=NOW, limit=100)

    assert result.sent_count == 0
    assert result.failed_count == 0
    assert result.skipped_count == 0
    assert result.due_count == 0
    assert len(provider.calls) == 0


def test_tick_no_due_reminders_provider_never_called():
    service, _, _, provider = _make_service()

    result = service.tick(now=NOW, limit=100)

    assert result.due_count == 0
    assert len(provider.calls) == 0


# ---------------------------------------------------------------------------
# 3. One due reminder — success
# ---------------------------------------------------------------------------


def test_tick_one_due_reminder_success():
    task = FakeTask(uuid.uuid4())
    reminder = _make_reminder(task.id, NOW - timedelta(hours=1))
    service, _, _, provider = _make_service(
        reminders=[reminder], tasks=[task]
    )

    result = service.tick(now=NOW, limit=100)

    assert result.sent_count == 1
    assert result.failed_count == 0
    assert result.skipped_count == 0
    assert result.due_count == 1
    assert len(provider.calls) == 1
    assert provider.calls[0][0].id == reminder.id
    assert provider.calls[0][1].id == task.id


# ---------------------------------------------------------------------------
# 4. Multiple due reminders — all succeed
# ---------------------------------------------------------------------------


def test_tick_multiple_due_reminders_all_succeed():
    task = FakeTask(uuid.uuid4())
    r1 = _make_reminder(task.id, NOW - timedelta(hours=3))
    r2 = _make_reminder(task.id, NOW - timedelta(hours=2))
    r3 = _make_reminder(task.id, NOW - timedelta(hours=1))
    service, _, _, provider = _make_service(
        reminders=[r1, r2, r3], tasks=[task]
    )

    result = service.tick(now=NOW, limit=100)

    assert result.sent_count == 3
    assert result.failed_count == 0
    assert result.skipped_count == 0
    assert result.due_count == 3
    assert len(provider.calls) == 3


# ---------------------------------------------------------------------------
# 5. Task context resolved BEFORE mark_sent (assert call order)
# ---------------------------------------------------------------------------


def test_tick_resolves_task_before_mark_sent():
    """Task context is resolved before mark_sent — provider requires task."""
    task = FakeTask(uuid.uuid4())
    reminder = _make_reminder(task.id, NOW - timedelta(hours=1))

    reminder_repo = FakeReminderRepository()
    reminder_repo.register(reminder)
    task_repo = FakeTaskRepository()
    task_repo.register(task)

    call_order: list[str] = []

    original_get_by_id = task_repo.get_by_id

    def tracked_get_by_id(task_id):
        call_order.append("resolve_task")
        return original_get_by_id(task_id)

    original_mark_sent = reminder_repo.mark_sent

    def tracked_mark_sent(reminder_id, now):
        call_order.append("mark_sent")
        return original_mark_sent(reminder_id, now)

    task_repo.get_by_id = tracked_get_by_id  # type: ignore[assignment]
    reminder_repo.mark_sent = tracked_mark_sent  # type: ignore[assignment]

    provider = FakeNotificationProvider()
    service = SchedulerService(reminder_repo, task_repo, provider)

    service.tick(now=NOW, limit=100)

    assert call_order == ["resolve_task", "mark_sent"]
    assert len(provider.calls) == 1


# ---------------------------------------------------------------------------
# 6. Missing task → not claimed, failed++
# ---------------------------------------------------------------------------


def test_tick_missing_task_not_claimed():
    task = FakeTask(uuid.uuid4())
    reminder = _make_reminder(task.id, NOW - timedelta(hours=1))
    service, _, _, provider = _make_service(
        reminders=[reminder], tasks=[]
    )

    result = service.tick(now=NOW, limit=100)

    assert result.failed_count == 1
    assert result.sent_count == 0
    assert result.skipped_count == 0
    assert result.due_count == 1
    assert len(provider.calls) == 0


# ---------------------------------------------------------------------------
# 7. mark_sent returns 0 → skipped++, provider not called
# ---------------------------------------------------------------------------


def test_tick_mark_sent_zero_skipped():
    task = FakeTask(uuid.uuid4())
    reminder = _make_reminder(task.id, NOW - timedelta(hours=1))
    service, reminder_repo, _, provider = _make_service(
        reminders=[reminder], tasks=[task]
    )
    reminder_repo.mark_sent_returns_zero(reminder.id)

    result = service.tick(now=NOW, limit=100)

    assert result.skipped_count == 1
    assert result.sent_count == 0
    assert result.failed_count == 0
    assert result.due_count == 1
    assert len(provider.calls) == 0


# ---------------------------------------------------------------------------
# 8. Provider failure after claim: sent++ AND failed++, continues
# ---------------------------------------------------------------------------


def test_tick_provider_failure_after_claim_counts_both():
    task = FakeTask(uuid.uuid4())
    reminder = _make_reminder(task.id, NOW - timedelta(hours=1))
    service, _, _, provider = _make_service(
        reminders=[reminder], tasks=[task]
    )
    provider.fail_for(reminder.id)

    result = service.tick(now=NOW, limit=100)

    assert result.sent_count == 1
    assert result.failed_count == 1
    assert result.skipped_count == 0
    assert result.due_count == 1
    assert reminder.status == ReminderStatus.SENT.value
    assert len(provider.calls) == 1


def test_tick_provider_failure_continues_processing():
    """Provider failure on first reminder does not prevent processing of others."""
    task = FakeTask(uuid.uuid4())
    r1 = _make_reminder(task.id, NOW - timedelta(hours=2))
    r2 = _make_reminder(task.id, NOW - timedelta(hours=1))
    service, _, _, provider = _make_service(
        reminders=[r1, r2], tasks=[task]
    )
    provider.fail_for(r1.id)

    result = service.tick(now=NOW, limit=100)

    assert result.sent_count == 2
    assert result.failed_count == 1
    assert result.skipped_count == 0
    assert result.due_count == 2
    assert r1.status == ReminderStatus.SENT.value
    assert r2.status == ReminderStatus.SENT.value
    assert len(provider.calls) == 2


# ---------------------------------------------------------------------------
# 9. due_count correctness
# ---------------------------------------------------------------------------


def test_tick_due_count_matches_find_due_result():
    task = FakeTask(uuid.uuid4())
    reminders = [_make_reminder(task.id, NOW - timedelta(hours=i)) for i in range(1, 6)]
    service, _, _, _ = _make_service(reminders=reminders, tasks=[task])

    result = service.tick(now=NOW, limit=100)

    assert result.due_count == 5


# ---------------------------------------------------------------------------
# 10. Correct final TickResult
# ---------------------------------------------------------------------------


def test_tick_final_tick_result_fields():
    task = FakeTask(uuid.uuid4())
    r_success = _make_reminder(task.id, NOW - timedelta(hours=3))
    r_provider_fail = _make_reminder(task.id, NOW - timedelta(hours=2))
    r_missing_task = _make_reminder(uuid.uuid4(), NOW - timedelta(hours=1))

    service, _, _, provider = _make_service(
        reminders=[r_success, r_provider_fail, r_missing_task], tasks=[task]
    )
    provider.fail_for(r_provider_fail.id)

    result = service.tick(now=NOW, limit=100)

    assert result.sent_count == 2
    assert result.failed_count == 2
    assert result.skipped_count == 0
    assert result.due_count == 3


# ---------------------------------------------------------------------------
# 11. Service never commits or rolls back
# ---------------------------------------------------------------------------


def test_tick_never_commits_or_rollbacks():
    """SchedulerService owns no transaction — it never calls commit/rollback."""
    task = FakeTask(uuid.uuid4())
    reminder = _make_reminder(task.id, NOW - timedelta(hours=1))

    reminder_repo = FakeReminderRepository()
    reminder_repo.register(reminder)
    task_repo = FakeTaskRepository()
    task_repo.register(task)

    provider = FakeNotificationProvider()
    service = SchedulerService(reminder_repo, task_repo, provider)

    result = service.tick(now=NOW, limit=100)

    assert result.sent_count == 1
    assert not hasattr(service, "commit")
    assert not hasattr(service, "rollback")


# ---------------------------------------------------------------------------
# 12. Limit respected
# ---------------------------------------------------------------------------


def test_tick_respects_limit():
    task = FakeTask(uuid.uuid4())
    reminders = [_make_reminder(task.id, NOW - timedelta(hours=i)) for i in range(1, 11)]
    service, _, _, _ = _make_service(reminders=reminders, tasks=[task])

    result = service.tick(now=NOW, limit=3)

    assert result.sent_count == 3
    assert result.due_count == 3


# ---------------------------------------------------------------------------
# 13. Exactly at now (inclusive)
# ---------------------------------------------------------------------------


def test_tick_includes_exactly_at_now():
    task = FakeTask(uuid.uuid4())
    reminder = _make_reminder(task.id, NOW)
    service, _, _, provider = _make_service(
        reminders=[reminder], tasks=[task]
    )

    result = service.tick(now=NOW, limit=100)

    assert result.due_count == 1
    assert result.sent_count == 1
    assert len(provider.calls) == 1


# ---------------------------------------------------------------------------
# 14. Future reminders excluded
# ---------------------------------------------------------------------------


def test_tick_future_reminders_excluded():
    task = FakeTask(uuid.uuid4())
    past = _make_reminder(task.id, NOW - timedelta(hours=1))
    future = _make_reminder(task.id, NOW + timedelta(hours=1))
    service, _, _, provider = _make_service(
        reminders=[past, future], tasks=[task]
    )

    result = service.tick(now=NOW, limit=100)

    assert result.due_count == 1
    assert result.sent_count == 1
    assert len(provider.calls) == 1
    assert provider.calls[0][0].id == past.id


# ---------------------------------------------------------------------------
# 15. Already-SENT reminders excluded from due
# ---------------------------------------------------------------------------


def test_tick_already_sent_reminders_excluded():
    task = FakeTask(uuid.uuid4())
    sent_reminder = _make_reminder(
        task.id, NOW - timedelta(hours=1), ReminderStatus.SENT.value
    )
    service, _, _, _ = _make_service(
        reminders=[sent_reminder], tasks=[task]
    )

    result = service.tick(now=NOW, limit=100)

    assert result.due_count == 0
    assert result.sent_count == 0