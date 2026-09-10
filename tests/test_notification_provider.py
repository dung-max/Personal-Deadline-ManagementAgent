"""Tests for the notification provider port and console adapter."""

from __future__ import annotations

import inspect
import logging
import uuid
from datetime import datetime, timedelta, timezone

from personal_deadline_management_agent.adapters.notification import (
    ConsoleNotificationProvider,
    NotificationProvider,
)
from personal_deadline_management_agent.models import (
    Reminder,
    ReminderStatus,
    Task,
    TaskPriority,
    TaskStatus,
)


def _make_task() -> Task:
    return Task(
        id=uuid.uuid4(),
        task_name="Write quarterly report",
        description=None,
        deadline=datetime.now(timezone.utc) + timedelta(days=1),
        priority=TaskPriority.MEDIUM.value,
        status=TaskStatus.TODO.value,
    )


def _make_reminder(task_id: uuid.UUID) -> Reminder:
    return Reminder(
        id=uuid.uuid4(),
        task_id=task_id,
        remind_at=datetime.now(timezone.utc) + timedelta(hours=1),
        status=ReminderStatus.PENDING.value,
    )


def test_console_provider_logs_reminder_and_task_context(caplog):
    provider = ConsoleNotificationProvider()
    task = _make_task()
    reminder = _make_reminder(task.id)

    with caplog.at_level(
        logging.INFO,
        logger="personal_deadline_management_agent.adapters.notification",
    ):
        provider.send(reminder=reminder, task=task)

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.levelno == logging.INFO
    message = record.getMessage()
    assert str(reminder.id) in message
    assert str(task.id) in message
    assert task.task_name in message
    assert reminder.remind_at.isoformat() in message


def test_console_provider_send_returns_none():
    provider = ConsoleNotificationProvider()
    task = _make_task()
    reminder = _make_reminder(task.id)

    assert provider.send(reminder=reminder, task=task) is None


def test_console_provider_signature_matches_port():
    """The adapter exposes the same keyword-only send() contract as the port."""
    port_params = inspect.signature(NotificationProvider.send).parameters
    adapter_params = inspect.signature(ConsoleNotificationProvider.send).parameters

    assert list(port_params) == ["self", "reminder", "task"]
    assert list(adapter_params) == ["self", "reminder", "task"]
    for name in ("reminder", "task"):
        assert port_params[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert adapter_params[name].kind is inspect.Parameter.KEYWORD_ONLY