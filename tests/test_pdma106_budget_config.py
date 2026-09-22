"""PDMA-106: Configurable daily working-hours budget.

Tests for Settings.daily_working_minutes, validation, DI wiring,
and WorkloadAnalysisService constructor/inject behavior.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from personal_deadline_management_agent.config import Settings
from personal_deadline_management_agent.models import TaskPriority, TaskStatus
from personal_deadline_management_agent.schemas.workload import TaskSummary
from personal_deadline_management_agent.services.workload_analysis_service import (
    WorkloadAnalysisService,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _summary(
    task_name: str = "Task",
    priority: TaskPriority = TaskPriority.MEDIUM,
    status: TaskStatus = TaskStatus.TODO,
    deadline: datetime | None = None,
    duration_minutes: int | None = None,
) -> TaskSummary:
    return TaskSummary(
        id=uuid4(),
        task_name=task_name,
        priority=priority,
        status=status,
        deadline=deadline,
        duration_minutes=duration_minutes,
    )


# ---------------------------------------------------------------------------
# 1-7: Settings validation
# ---------------------------------------------------------------------------


def _settings(**overrides) -> Settings:
    """Minimal Settings factory for tests (DATABASE_URL required)."""
    base = {"database_url": "sqlite:///:memory:", "daily_working_minutes": 480}
    base.update(overrides)
    return Settings(**base)


def test_settings_default_is_480():
    s = _settings()
    assert s.daily_working_minutes == 480


def test_settings_custom_valid_value():
    s = _settings(daily_working_minutes=360)
    assert s.daily_working_minutes == 360


def test_settings_1_is_valid():
    s = _settings(daily_working_minutes=1)
    assert s.daily_working_minutes == 1


def test_settings_1440_is_valid():
    s = _settings(daily_working_minutes=1440)
    assert s.daily_working_minutes == 1440


def test_settings_0_is_rejected():
    with pytest.raises(ValueError, match="daily_working_minutes"):
        _settings(daily_working_minutes=0)


def test_settings_negative_is_rejected():
    with pytest.raises(ValueError, match="daily_working_minutes"):
        _settings(daily_working_minutes=-30)


def test_settings_1441_is_rejected():
    with pytest.raises(ValueError, match="daily_working_minutes"):
        _settings(daily_working_minutes=1441)


# ---------------------------------------------------------------------------
# 8-11: Service constructor / DI behavior
# ---------------------------------------------------------------------------


def test_service_default_budget():
    svc = WorkloadAnalysisService()
    assert svc._default_budget_minutes == 480


def test_service_custom_budget():
    svc = WorkloadAnalysisService(budget_minutes=600)
    assert svc._default_budget_minutes == 600


def test_service_rejects_0_budget():
    with pytest.raises(ValueError, match="budget_minutes"):
        WorkloadAnalysisService(budget_minutes=0)


def test_service_rejects_negative_budget():
    with pytest.raises(ValueError, match="budget_minutes"):
        WorkloadAnalysisService(budget_minutes=-10)


def test_service_rejects_over_1440():
    with pytest.raises(ValueError, match="budget_minutes"):
        WorkloadAnalysisService(budget_minutes=1441)


# ---------------------------------------------------------------------------
# 8-10: DI wiring — configured budget reaches daily pressure / overload
# ---------------------------------------------------------------------------


def test_configured_budget_used_for_daily_pressure():
    """Service instance default budget flows into daily pressure calculation."""
    deadline = datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc)
    tasks = [_summary("Task A", TaskPriority.HIGH, TaskStatus.TODO, deadline, 300)]
    svc = WorkloadAnalysisService(budget_minutes=300)
    result = svc.analyze(tasks)
    assert len(result.daily_pressure) == 1
    assert result.daily_pressure[0].budget_minutes == 300
    assert result.daily_pressure[0].utilization_pct == 100.0


def test_configured_budget_used_for_overload():
    """Overload uses instance default budget, not hardcoded 480."""
    deadline = datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc)
    tasks = [_summary("Task A", TaskPriority.HIGH, TaskStatus.TODO, deadline, 300)]
    # With budget=300, planned=300 → no overload
    svc300 = WorkloadAnalysisService(budget_minutes=300)
    result300 = svc300.analyze(tasks)
    assert len(result300.overload_warnings) == 0
    # With budget=200, planned=300 → overload
    svc200 = WorkloadAnalysisService(budget_minutes=200)
    result200 = svc200.analyze(tasks)
    assert len(result200.overload_warnings) == 1
    assert result200.overload_warnings[0].excess_minutes == 100


def test_explicit_budget_override_still_works():
    """Explicit budget_minutes= in analyze() overrides instance default."""
    deadline = datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc)
    tasks = [_summary("Task A", TaskPriority.HIGH, TaskStatus.TODO, deadline, 300)]
    svc = WorkloadAnalysisService(budget_minutes=200)
    # Override to 400 — no overload
    result = svc.analyze(tasks, budget_minutes=400)
    assert result.daily_pressure[0].budget_minutes == 400
    assert len(result.overload_warnings) == 0
    # Default would be 200 → overload
    result_default = svc.analyze(tasks)
    assert result_default.daily_pressure[0].budget_minutes == 200
    assert len(result_default.overload_warnings) == 1


# ---------------------------------------------------------------------------
# 11: DI wiring — dependency.py
# ---------------------------------------------------------------------------


def test_get_workload_analysis_service_from_settings():
    """get_workload_analysis_service reads daily_working_minutes from Settings."""
    from personal_deadline_management_agent.dependencies import (
        get_workload_analysis_service,
    )

    settings = Settings(database_url="sqlite:///:memory:", daily_working_minutes=600)
    svc = get_workload_analysis_service(settings)
    assert isinstance(svc, WorkloadAnalysisService)
    assert svc._default_budget_minutes == 600
