"""Tests for the timezone policy (Phase 4.9).

Verifies:
- Shared datetime utility functions (require_aware_utc, parse_iso_datetime).
- Direct API schema validation rejects naive datetimes and normalizes aware → UTC.
- Agent path (ActionExecutor) uses the same datetime policy as Direct API.
- Business rules (deadline in the past, remind_at <= deadline) still work across timezones.
- UTC normalization preserves the represented instant.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from personal_deadline_management_agent.utils.datetime_utils import (
    NaiveDateTimeError,
    parse_iso_datetime,
    require_aware_utc,
)


# ==========================================================================
# 1. Shared utility: require_aware_utc
# ==========================================================================


class TestRequireAwareUtc:
    """Tests for the core UTC-normalization utility."""

    def test_rejects_naive_datetime(self):
        naive = datetime(2026, 10, 1, 12, 0)
        with pytest.raises(NaiveDateTimeError, match="must be timezone-aware"):
            require_aware_utc(naive)

    def test_accepts_utc_datetime(self):
        aware = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
        result = require_aware_utc(aware)
        assert result.tzinfo is not None
        assert result == aware

    def test_accepts_positive_offset_and_normalizes_to_utc(self):
        offset = timezone(timedelta(hours=7))
        aware = datetime(2026, 10, 1, 19, 0, tzinfo=offset)
        result = require_aware_utc(aware)
        assert result == datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

    def test_accepts_negative_offset_and_normalizes_to_utc(self):
        offset = timezone(timedelta(hours=-5))
        aware = datetime(2026, 10, 1, 8, 0, tzinfo=offset)
        result = require_aware_utc(aware)
        assert result == datetime(2026, 10, 1, 13, 0, tzinfo=timezone.utc)

    def test_preserves_represented_instant(self):
        """An aware datetime and its UTC equivalent represent the same instant."""
        offset = timezone(timedelta(hours=3))
        original = datetime(2026, 10, 1, 15, 0, tzinfo=offset)
        normalized = require_aware_utc(original)
        # Both represent the same point in time
        assert original == normalized

    def test_accepts_utcoffset_none_but_zero(self):
        """Edge case: fixed-offset(0) is equivalent to UTC."""
        offset = timezone(timedelta(hours=0))
        aware = datetime(2026, 10, 1, 12, 0, tzinfo=offset)
        result = require_aware_utc(aware)
        assert result == datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

    def test_is_valueerror_subclass(self):
        assert issubclass(NaiveDateTimeError, ValueError)


# ==========================================================================
# 2. Shared utility: parse_iso_datetime
# ==========================================================================


class TestParseIsoDatetime:
    """Tests for ISO-8601 string parsing with timezone enforcement."""

    def test_accepts_utc_z_suffix(self):
        result = parse_iso_datetime("2026-10-01T12:00:00Z")
        assert result == datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

    def test_accepts_utc_plus0000(self):
        result = parse_iso_datetime("2026-10-01T12:00:00+00:00")
        assert result == datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

    def test_normalizes_positive_offset_to_utc(self):
        result = parse_iso_datetime("2026-10-01T19:00:00+07:00")
        assert result == datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

    def test_normalizes_negative_offset_to_utc(self):
        result = parse_iso_datetime("2026-10-01T08:00:00-05:00")
        assert result == datetime(2026, 10, 1, 13, 0, tzinfo=timezone.utc)

    def test_rejects_naive_string(self):
        with pytest.raises(NaiveDateTimeError, match="must be timezone-aware"):
            parse_iso_datetime("2026-10-01T12:00:00")

    def test_rejects_invalid_string(self):
        with pytest.raises(ValueError):
            parse_iso_datetime("not-a-date")

    def test_equivalent_instants_in_different_offsets(self):
        """Two strings representing the same instant produce the same result."""
        result_z = parse_iso_datetime("2026-10-01T12:00:00Z")
        result_plus7 = parse_iso_datetime("2026-10-01T19:00:00+07:00")
        assert result_z == result_plus7


# ==========================================================================
# 3. Direct API: Task schema datetime validation
# ==========================================================================


@pytest.fixture
def client(tmp_path):
    """TestClient with temporary file-based SQLite database."""
    from genai_core.genai_shared.database import Base
    from personal_deadline_management_agent.config import Settings
    from personal_deadline_management_agent.main import create_app

    db_file = tmp_path / "test.db"
    settings = Settings(database_url=f"sqlite:///{db_file}")
    app = create_app(settings)
    with TestClient(app) as test_client:
        Base.metadata.create_all(app.state.engine)
        yield test_client


class TestDirectApiTaskDatetimePolicy:
    """Direct API: naive deadlines must be rejected, aware normalized to UTC."""

    def test_aware_utc_deadline_accepted(self, client: TestClient):
        response = client.post(
            "/api/v1/tasks",
            json={
                "taskName": "UTC Task",
                "deadline": "2026-10-01T12:00:00Z",
                "priority": "LOW",
            },
        )
        assert response.status_code == 201
        data = response.json()["data"]
        assert data["deadline"] == "2026-10-01T12:00:00Z"

    def test_aware_plus0700_deadline_accepted(self, client: TestClient):
        response = client.post(
            "/api/v1/tasks",
            json={
                "taskName": "Vietnam Task",
                "deadline": "2026-10-01T19:00:00+07:00",
                "priority": "MEDIUM",
            },
        )
        assert response.status_code == 201
        # Stored as UTC: 19:00+07:00 == 12:00 UTC
        assert response.json()["data"]["deadline"] == "2026-10-01T12:00:00Z"

    def test_aware_negative_offset_deadline_accepted(self, client: TestClient):
        response = client.post(
            "/api/v1/tasks",
            json={
                "taskName": "US East Task",
                "deadline": "2026-10-01T08:00:00-05:00",
                "priority": "HIGH",
            },
        )
        assert response.status_code == 201
        # 08:00-05:00 == 13:00 UTC
        assert response.json()["data"]["deadline"] == "2026-10-01T13:00:00Z"

    def test_naive_deadline_rejected(self, client: TestClient):
        response = client.post(
            "/api/v1/tasks",
            json={
                "taskName": "Naive Task",
                "deadline": "2026-10-01T12:00:00",
                "priority": "LOW",
            },
        )
        assert response.status_code == 400
        body = response.json()
        assert body["success"] is False
        assert body["error"]["code"] == "VALIDATION_ERROR"

    def test_update_naive_deadline_rejected(self, client: TestClient):
        # Create a valid task first
        created = client.post(
            "/api/v1/tasks",
            json={
                "taskName": "Original",
                "deadline": "2026-10-01T00:00:00Z",
                "priority": "LOW",
            },
        )
        task_id = created.json()["data"]["taskId"]

        response = client.patch(
            f"/api/v1/tasks/{task_id}",
            json={"deadline": "2026-12-01T12:00:00"},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_update_aware_offset_deadline_normalizes(self, client: TestClient):
        created = client.post(
            "/api/v1/tasks",
            json={
                "taskName": "Original",
                "deadline": "2026-10-01T00:00:00Z",
                "priority": "LOW",
            },
        )
        task_id = created.json()["data"]["taskId"]

        response = client.patch(
            f"/api/v1/tasks/{task_id}",
            json={"deadline": "2026-12-01T19:00:00+07:00"},
        )
        assert response.status_code == 200
        assert response.json()["data"]["deadline"] == "2026-12-01T12:00:00Z"


# ==========================================================================
# 4. Direct API: Reminder schema datetime validation
# ==========================================================================


class TestDirectApiReminderDatetimePolicy:
    """Direct API: naive remind_at must be rejected, aware normalized to UTC."""

    def _create_task(self, client: TestClient, deadline: str) -> str:
        response = client.post(
            "/api/v1/tasks",
            json={
                "taskName": "Parent Task",
                "deadline": deadline,
                "priority": "MEDIUM",
            },
        )
        assert response.status_code == 201
        return response.json()["data"]["taskId"]

    def test_aware_utc_remind_at_accepted(self, client: TestClient):
        task_id = self._create_task(client, "2026-10-01T23:00:00Z")
        response = client.post(
            f"/api/v1/tasks/{task_id}/reminders",
            json={"remindAt": "2026-10-01T12:00:00Z"},
        )
        assert response.status_code == 201
        assert response.json()["data"]["remindAt"] == "2026-10-01T12:00:00Z"

    def test_aware_plus0700_remind_at_normalized(self, client: TestClient):
        task_id = self._create_task(client, "2026-10-02T00:00:00Z")
        response = client.post(
            f"/api/v1/tasks/{task_id}/reminders",
            json={"remindAt": "2026-10-01T19:00:00+07:00"},
        )
        assert response.status_code == 201
        # 19:00+07:00 == 12:00 UTC
        assert response.json()["data"]["remindAt"] == "2026-10-01T12:00:00Z"

    def test_naive_remind_at_rejected(self, client: TestClient):
        task_id = self._create_task(client, "2026-10-01T23:00:00Z")
        response = client.post(
            f"/api/v1/tasks/{task_id}/reminders",
            json={"remindAt": "2026-10-01T12:00:00"},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_update_naive_remind_at_rejected(self, client: TestClient):
        task_id = self._create_task(client, "2026-12-01T23:00:00Z")
        # Create a valid reminder first
        created = client.post(
            f"/api/v1/tasks/{task_id}/reminders",
            json={"remindAt": "2026-12-01T10:00:00Z"},
        )
        reminder_id = created.json()["data"]["reminderId"]

        response = client.patch(
            f"/api/v1/reminders/{reminder_id}",
            json={"remindAt": "2026-12-01T12:00:00"},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_reminder_comparison_cross_offset(self, client: TestClient):
        """remind_at in +07:00 and task.deadline in +05:30 — both represent
        the same instant, so the reminder must be accepted (not after deadline)."""
        # Task deadline: 2026-10-01T13:00:00Z
        task_id = self._create_task(client, "2026-10-01T13:00:00Z")
        # remind_at: 2026-10-01T20:00:00+07:00 == 2026-10-01T13:00:00Z (same instant)
        response = client.post(
            f"/api/v1/tasks/{task_id}/reminders",
            json={"remindAt": "2026-10-01T20:00:00+07:00"},
        )
        assert response.status_code == 201  # At deadline is allowed

    def test_reminder_after_deadline_rejected_across_offsets(self, client: TestClient):
        """remind_at at +07:00 that maps to AFTER the UTC deadline must be rejected."""
        # Task deadline: 2026-10-01T13:00:00Z
        task_id = self._create_task(client, "2026-10-01T13:00:00Z")
        # remind_at: 2026-10-01T21:00:00+07:00 == 2026-10-01T14:00:00Z (after deadline)
        response = client.post(
            f"/api/v1/tasks/{task_id}/reminders",
            json={"remindAt": "2026-10-01T21:00:00+07:00"},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_REMINDER"


# ==========================================================================
# 5. Task business validation still works with timezone normalization
# ==========================================================================


class TestBusinessValidationWithTimezones:
    """Business rules (deadline not in past, remind_at <= deadline) must work
    correctly after timezone normalization."""

    def test_future_deadline_with_plus07_accepted(self, client: TestClient):
        response = client.post(
            "/api/v1/tasks",
            json={
                "taskName": "Vietnam Future",
                "deadline": "2027-01-01T10:00:00+07:00",
                "priority": "LOW",
            },
        )
        assert response.status_code == 201

    def test_past_deadline_with_plus07_rejected(self, client: TestClient):
        response = client.post(
            "/api/v1/tasks",
            json={
                "taskName": "Vietnam Past",
                "deadline": "2020-01-01T10:00:00+07:00",
                "priority": "LOW",
            },
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_TASK"
        assert "past" in response.json()["error"]["message"].lower()


# ==========================================================================
# 6. Agent path uses the same datetime policy
# ==========================================================================


def test_agent_naive_deadline_returns_invalid_input():
    """The Agent path must reject naive deadline strings, returning INVALID_INPUT."""
    from unittest.mock import MagicMock

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from genai_core.genai_shared.database import Base
    from personal_deadline_management_agent.config import Settings
    from personal_deadline_management_agent.dependencies import (
        get_agent_interpreter,
        get_action_validator,
        get_decision_service,
        get_resource_resolver,
        get_session_factory,
    )
    from personal_deadline_management_agent.guardrails import DecisionResult, DecisionStatus
    from personal_deadline_management_agent.main import create_app
    from personal_deadline_management_agent.schemas.agent import (
        ActionProposal,
        ActionType,
        AgentResponse,
        ResponseType,
    )
    from personal_deadline_management_agent.schemas.agent_chat import AgentChatStatus
    from personal_deadline_management_agent.services.action_validator import (
        ValidationResult,
        ValidationStatus,
        ValidatedAction,
    )
    from personal_deadline_management_agent.services.resource_resolver import (
        ResolutionResult,
    )

    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine)

    app = create_app(Settings(database_url="sqlite:///:memory:"))
    app.dependency_overrides[get_session_factory] = lambda: sf

    # LLM produces a naive datetime string
    params = {"taskName": "Naive Agent Task", "deadline": "2026-10-01T12:00:00"}
    interpreter = MagicMock()
    interpreter.interpret.return_value = AgentResponse(
        response_type=ResponseType.ACTION_PROPOSED,
        message="",
        proposal=ActionProposal(action_type=ActionType.CREATE_TASK, parameters=params),
    )
    validator = MagicMock()
    validator.validate.return_value = ValidationResult(status=ValidationStatus.VALID)
    resolver = MagicMock()
    resolver.resolve.return_value = ResolutionResult(
        status=ValidationStatus.VALID,
        validated_action=ValidatedAction(
            action_type=ActionType.CREATE_TASK, parameters=params
        ),
    )
    decision_service = MagicMock()
    decision_service.decide.return_value = DecisionResult(
        status=DecisionStatus.AUTHORIZED,
        reason="Action is permitted.",
        action=ValidatedAction(action_type=ActionType.CREATE_TASK, parameters=params),
    )

    app.dependency_overrides[get_agent_interpreter] = lambda: interpreter
    app.dependency_overrides[get_action_validator] = lambda: validator
    app.dependency_overrides[get_resource_resolver] = lambda: resolver
    app.dependency_overrides[get_decision_service] = lambda: decision_service

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/v1/agent/chat", json={"message": "create a task"}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == AgentChatStatus.INVALID_INPUT.value
    assert body["execution_result"]["errorCode"] == "INVALID_INPUT"

    engine.dispose()


def test_agent_plus0700_deadline_normalized_to_utc():
    """The Agent path must normalize +07:00 deadlines to UTC before module call."""
    from unittest.mock import MagicMock

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from genai_core.genai_shared.database import Base
    from personal_deadline_management_agent.config import Settings
    from personal_deadline_management_agent.dependencies import (
        get_session_factory,
        get_action_executor,
        get_action_validator,
        get_agent_interpreter,
        get_decision_service,
        get_pending_confirmation_module,
        get_resource_resolver,
    )
    from personal_deadline_management_agent.main import create_app
    from personal_deadline_management_agent.schemas.agent import (
        ActionProposal,
        ActionType,
        AgentResponse,
        ResponseType,
    )
    from personal_deadline_management_agent.schemas.agent_chat import AgentChatStatus
    from personal_deadline_management_agent.services.action_validator import (
        ValidationResult,
        ValidationStatus,
        ValidatedAction,
    )
    from personal_deadline_management_agent.services.resource_resolver import (
        ResolutionResult,
    )
    from personal_deadline_management_agent.guardrails import DecisionResult, DecisionStatus

    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine)

    app = create_app(Settings(database_url="sqlite:///:memory:"))
    app.dependency_overrides[get_session_factory] = lambda: sf

    # LLM produces a +07:00 datetime
    params = {"taskName": "Vietnam Agent Task", "deadline": "2026-12-01T19:00:00+07:00"}
    interpreter = MagicMock()
    interpreter.interpret.return_value = AgentResponse(
        response_type=ResponseType.ACTION_PROPOSED,
        message="",
        proposal=ActionProposal(action_type=ActionType.CREATE_TASK, parameters=params),
    )
    validator = MagicMock()
    validator.validate.return_value = ValidationResult(status=ValidationStatus.VALID)
    resolver = MagicMock()
    resolver.resolve.return_value = ResolutionResult(
        status=ValidationStatus.VALID,
        validated_action=ValidatedAction(
            action_type=ActionType.CREATE_TASK, parameters=params
        ),
    )
    decision_service = MagicMock()
    decision_service.decide.return_value = DecisionResult(
        status=DecisionStatus.AUTHORIZED,
        reason="Action is permitted.",
        action=ValidatedAction(action_type=ActionType.CREATE_TASK, parameters=params),
    )

    app.dependency_overrides[get_agent_interpreter] = lambda: interpreter
    app.dependency_overrides[get_action_validator] = lambda: validator
    app.dependency_overrides[get_resource_resolver] = lambda: resolver
    app.dependency_overrides[get_decision_service] = lambda: decision_service

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/v1/agent/chat", json={"message": "create a task"}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == AgentChatStatus.EXECUTED.value

    # Verify the task was created with UTC-normalized deadline
    with engine.connect() as conn:
        from personal_deadline_management_agent.models import Task
        rows = conn.execute(Task.__table__.select()).fetchall()
    assert len(rows) == 1
    # The stored deadline should be 12:00 UTC (19:00+07:00 normalized)
    stored_deadline = rows[0]._mapping["deadline"]
    assert stored_deadline.hour == 12

    engine.dispose()


# ==========================================================================
# 7. Persistence: stored datetimes remain timezone-aware
# ==========================================================================


def test_persisted_deadline_is_aware_after_read(client: TestClient):
    """After creating a task with an offset deadline, reading it back returns
    an aware datetime (UTC-normalized)."""
    response = client.post(
        "/api/v1/tasks",
        json={
            "taskName": "Persistence Test",
            "deadline": "2026-10-01T19:00:00+07:00",
            "priority": "LOW",
        },
    )
    assert response.status_code == 201
    task_id = response.json()["data"]["taskId"]

    get_resp = client.get(f"/api/v1/tasks/{task_id}")
    assert get_resp.status_code == 200
    # The JSON serialization includes Z suffix (UTC)
    deadline_str = get_resp.json()["data"]["deadline"]
    assert deadline_str.endswith("Z")
    assert deadline_str == "2026-10-01T12:00:00Z"


def test_utc_normalization_preserves_instant():
    """Two requests representing the same instant via different offsets
    must produce the same stored deadline."""
    from genai_core.genai_shared.database import Base
    from personal_deadline_management_agent.config import Settings
    from personal_deadline_management_agent.main import create_app

    import tempfile
    import os

    db_file = tempfile.mktemp(suffix=".db")
    settings = Settings(database_url=f"sqlite:///{db_file}")
    app = create_app(settings)
    with TestClient(app) as client:
        Base.metadata.create_all(app.state.engine)
        # Request 1: 12:00Z
        r1 = client.post(
            "/api/v1/tasks",
            json={
                "taskName": "Task UTC",
                "deadline": "2026-10-01T12:00:00Z",
                "priority": "LOW",
            },
        )
        # Request 2: 19:00+07:00 (same instant)
        r2 = client.post(
            "/api/v1/tasks",
            json={
                "taskName": "Task +07",
                "deadline": "2026-10-01T19:00:00+07:00",
                "priority": "LOW",
            },
        )

    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["data"]["deadline"] == r2.json()["data"]["deadline"]
    assert r1.json()["data"]["deadline"] == "2026-10-01T12:00:00Z"

    app.state.engine.dispose()
    os.unlink(db_file)
