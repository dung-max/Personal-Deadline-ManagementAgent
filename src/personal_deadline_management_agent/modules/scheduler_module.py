"""Scheduler module.

Application use-case orchestration layer for the scheduler tick.  Owns
commit/rollback for the scheduler use case using UnitOfWork.

Scheduling semantics (MVP):
    ``Reminder.status = SENT`` means the reminder was **successfully
    processed/claimed by the scheduler** — it does **NOT** guarantee external
    delivery.  The module commits only when ``due_count > 0`` (no writes to
    persist when nothing is due).  Provider exceptions are caught inside the
    service and do **NOT** trigger rollback (claims are durable).  Only DB
    exceptions propagate and trigger rollback (whole batch retried next tick,
    idempotent).

Transaction ownership:
    Only the Module calls ``uow.commit()`` / ``uow.rollback()``.  The
    ``SchedulerService.tick()`` is a pure use-case executor with no
    transaction awareness.
"""

from __future__ import annotations

import logging

from ..adapters.notification import NotificationProvider
from ..services.scheduler_service import SchedulerService, TickResult
from ..uow import UnitOfWork

logger = logging.getLogger(__name__)


class SchedulerModule:
    """Orchestrates the scheduler tick transaction.

    Wraps ``SchedulerService.tick()`` and owns the transaction lifecycle:
    commit on success (when ``due_count > 0``), rollback on DB exception.
    """

    def __init__(
        self,
        uow: UnitOfWork,
        notification_provider: NotificationProvider,
    ) -> None:
        self._uow = uow
        self._service = SchedulerService(
            reminder_repository=uow.reminders,
            task_repository=uow.tasks,
            notification_provider=notification_provider,
        )

    def tick(
        self,
        *,
        now,
        limit: int,
    ) -> TickResult:
        """Run a single scheduler tick and commit/discard.

        Args:
            now: UTC-aware datetime for the tick timestamp.
            limit: Maximum reminders to process.

        Returns:
            ``TickResult`` with sent/failed/skipped/due counts.

        Raises:
            Re-raises DB exceptions after rollback (whole batch retried next
            tick, idempotent).
        """
        try:
            result = self._service.tick(now=now, limit=limit)
        except Exception:
            self._uow.rollback()
            raise

        # Optimization: skip commit when nothing was due — no writes to
        # persist and avoids an empty transaction.
        if result.due_count > 0:
            try:
                self._uow.commit()
            except Exception:
                self._uow.rollback()
                raise

        return result
