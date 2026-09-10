"""Scheduler service.

Batch tick logic for due reminders: find due, resolve task, atomically claim
(``PENDING → SENT``), and notify via the injected ``NotificationProvider``.

This service owns **no transaction**; it never calls ``commit`` or ``rollback``.
The calling Module is responsible for transaction lifecycle.

Sent semantics (MVP):
    ``Reminder.status = SENT`` means the reminder was **successfully
    processed/claimed by the scheduler** — the scheduler atomically won the
    claim and took responsibility for it.  ``SENT`` does **NOT** guarantee
    external delivery.  ``updated_at`` is a generic modification timestamp,
    **not** ``notified_at``.

Tick order:
    1. ``find_due(limit, now)`` — retrieve due reminders.
    2. For each reminder:
       a. **Resolve task context** — ``task = task_repo.get_by_id(...)``.
          If task is ``None`` (defensive only — FK CASCADE), count failed and
          **skip** (no context → cannot notify → must not transition).
       b. **Atomic claim** — ``mark_sent(reminder_id, now)``.  If returns 0,
          the reminder lost the race → count skipped, continue.
       c. **Notify** — ``provider.send(reminder, task)``.  On success:
          increment ``sent_count``.  On exception: log warning, increment
          ``sent_count`` (it was claimed) **AND** ``failed_count`` (delivery
          failed), continue.  Provider exceptions are caught **per reminder**
          and do **NOT** stop the batch.
    3. Return ``TickResult``.

Provider failure after claim:
    The reminder **stays SENT** (claim is durable).  Both ``sent_count`` and
    ``failed_count`` are incremented (provider failure counts in both).

Task context before claim:
    Task context is resolved **before** ``mark_sent()``.  A reminder whose
    task cannot be resolved is **not claimed** — only ``failed_count`` is
    incremented (not ``sent_count``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ..adapters.notification import NotificationProvider
from ..models import Reminder, ReminderStatus, Task
from ..repositories.reminder_repository import ReminderRepository
from ..repositories.task_repository import TaskRepository

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TickResult
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TickResult:
    """Result of a single scheduler tick.

    Fields:
        sent_count: Number of reminders successfully claimed/processed by the
            scheduler.  This includes reminders whose notification provider
            fails after the claim — SENT is durable.
        failed_count: Number of reminders whose task context could not be
            resolved, OR whose notification provider failed after the
            reminder was claimed (counts in both sent and failed).
        skipped_count: Number of due reminders whose atomic ``mark_sent()``
            returned 0 because the reminder was concurrently changed,
            rescheduled, or cancelled.
        due_count: Number of reminders returned by ``find_due()``.
    """

    sent_count: int
    failed_count: int
    skipped_count: int
    due_count: int


# ---------------------------------------------------------------------------
# SchedulerService
# ---------------------------------------------------------------------------


class SchedulerService:
    """Batch tick logic for the scheduler worker.

    Owned by the scheduler process — not exposed through the FastAPI app.
    Injects repositories and a notification provider via constructor.
    """

    def __init__(
        self,
        reminder_repository: ReminderRepository,
        task_repository: TaskRepository,
        notification_provider: NotificationProvider,
    ) -> None:
        self._reminder_repository = reminder_repository
        self._task_repository = task_repository
        self._notification_provider = notification_provider

    def tick(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> TickResult:
        """Process all due reminders in a single batch.

        Args:
            now: UTC-aware datetime.  ``find_due`` queries ``remind_at <= now``.
            limit: Maximum reminders to process per tick.

        Returns:
            ``TickResult`` with sent/failed/skipped/due counts.
        """
        due = self._reminder_repository.find_due(limit=limit, now=now)
        due_count = len(due)

        sent_count = 0
        failed_count = 0
        skipped_count = 0

        for reminder in due:
            # 1. Resolve task context BEFORE the SENT transition (provider
            #    requires task context; missing context = not claimed).
            task = self._task_repository.get_by_id(reminder.task_id)

            if task is None:
                # Defensive only — FK CASCADE deletes reminders with their
                # task.  If we reach here, the task was deleted between
                # find_due and here.  Do NOT claim (no context).
                logger.warning(
                    "Task %s for reminder %s not found — skipping (not claimed)",
                    reminder.task_id,
                    reminder.id,
                )
                failed_count += 1
                continue

            # 2. Atomic claim: PENDING → SENT (conditional UPDATE).
            rowcount = self._reminder_repository.mark_sent(
                reminder.id, now
            )

            if rowcount == 0:
                # Race: reminder was concurrently cancelled/rescheduled/claimed.
                skipped_count += 1
                continue

            # 3. Notify — provider call is best-effort; claim is already durable.
            try:
                self._notification_provider.send(
                    reminder=reminder, task=task
                )
                sent_count += 1
            except Exception:
                # Provider failed after claim.  Reminder stays SENT (durable).
                # Both sent_count and failed_count are incremented.
                logger.warning(
                    "Provider failed for reminder %s after claim",
                    reminder.id,
                    exc_info=True,
                )
                sent_count += 1
                failed_count += 1
                continue

        return TickResult(
            sent_count=sent_count,
            failed_count=failed_count,
            skipped_count=skipped_count,
            due_count=due_count,
        )
