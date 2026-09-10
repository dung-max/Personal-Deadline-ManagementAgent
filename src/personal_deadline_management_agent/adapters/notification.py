"""Notification port and console adapter.

The port (``NotificationProvider``) is the application-level interface that
higher layers depend on. The adapter (``ConsoleNotificationProvider``) is the
MVP implementation: deterministic, log-only delivery with no external side
effects.

MVP semantics: ``Reminder.status = SENT`` means the reminder was successfully
processed/claimed by the scheduler — it does NOT guarantee external delivery.
The console provider never fails and performs no I/O beyond logging.
"""

from __future__ import annotations

import logging
from typing import Protocol

from ..models import Reminder, Task

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Port (application-level interface)
# ---------------------------------------------------------------------------


class NotificationProvider(Protocol):
    """Application-level interface for reminder notification delivery.

    Higher layers (modules, services) depend on this protocol.
    Concrete adapters are injected via constructor.
    """

    def send(self, *, reminder: Reminder, task: Task) -> None: ...


# ---------------------------------------------------------------------------
# Adapter (console implementation)
# ---------------------------------------------------------------------------


class ConsoleNotificationProvider:
    """Concrete adapter that delivers notifications to the console log.

    Deterministic and non-external: no SMTP, no network calls, no database
    access — the only side effect is a structured INFO log line carrying the
    reminder/task context needed to identify the notification.
    """

    def send(self, *, reminder: Reminder, task: Task) -> None:
        logger.info(
            "Reminder notification: reminder_id=%s task_id=%s task_name=%r "
            "remind_at=%s",
            reminder.id,
            task.id,
            task.task_name,
            reminder.remind_at.isoformat(),
        )