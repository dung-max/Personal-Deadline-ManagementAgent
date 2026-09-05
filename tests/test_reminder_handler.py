"""Tests for ReminderHandler and API endpoints.

Tests end-to-end HTTP request handling, response schemas (camelCase),
error contracts (400, 404, 500), and verifies that Handler delegates
strictly to ReminderModule without bypassing any architectural layer.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text, update

from genai_core.genai_shared.database import Base
from personal_deadline_management_agent.config import Settings
from personal_deadline_management_agent.dependencies import get_reminder_module
from personal_deadline_management_agent.main import create_app
from personal_deadline_management_agent.models import Reminder, ReminderStatus
from personal_deadline_management_agent.modules.reminder_module import ReminderModule

TASK_DEADLINE = "2026-12-01T00:00:00Z"
BEFORE_DEADLINE = "2026-11-30T09:00:00Z"
AFTER_DEADLINE = "2026-12-02T00:00:00Z"


@pytest.fixture
def client(tmp_path):
    """TestClient with temporary file-based SQLite database."""
    db_file = tmp_path / "test.db"
    settings = Settings(database_url=f"sqlite:///{db_file}")
    app = create_app(settings)
    with TestClient(app) as test_client:
        Base.metadata.create_all(app.state.engine)
        yield test_client


def _create_task(client: TestClient, deadline: str = TASK_DEADLINE) -> str:
    response = client.post(
        "/api/v1/tasks",
        json={"taskName": "Parent Task", "deadline": deadline, "priority": "MEDIUM"},
    )
    assert response.status_code == 201
    return response.json()["data"]["taskId"]


def _create_reminder(
    client: TestClient, task_id: str, remind_at: str = BEFORE_DEADLINE
) -> dict:
    response = client.post(
        f"/api/v1/tasks/{task_id}/reminders", json={"remindAt": remind_at}
    )
    assert response.status_code == 201
    return response.json()["data"]


# --- 1. POST /api/v1/tasks/{taskId}/reminders ---


def test_create_reminder_valid(client: TestClient):
    task_id = _create_task(client)
    response = client.post(
        f"/api/v1/tasks/{task_id}/reminders", json={"remindAt": BEFORE_DEADLINE}
    )
    assert response.status_code == 201

    body = response.json()
    assert body["success"] is True
    assert body["message"] == "Reminder created successfully"

    data = body["data"]
    assert data["taskId"] == task_id
    assert data["status"] == "PENDING"
    assert "reminderId" in data
    assert "remindAt" in data
    assert "createdAt" in data
    assert "updatedAt" in data


def test_create_reminder_response_uses_camel_case_only(client: TestClient):
    task_id = _create_task(client)
    data = _create_reminder(client, task_id)

    assert set(data.keys()) == {
        "reminderId",
        "taskId",
        "remindAt",
        "status",
        "createdAt",
        "updatedAt",
    }


def test_create_reminder_invalid_datetime(client: TestClient):
    task_id = _create_task(client)
    response = client.post(
        f"/api/v1/tasks/{task_id}/reminders", json={"remindAt": "not-a-datetime"}
    )
    assert response.status_code == 400

    body = response.json()
    assert body["success"] is False
    assert body["message"] == "Validation error"
    assert body["error"]["code"] == "VALIDATION_ERROR"


def test_create_reminder_missing_remind_at(client: TestClient):
    task_id = _create_task(client)
    response = client.post(f"/api/v1/tasks/{task_id}/reminders", json={})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_create_reminder_nonexistent_task(client: TestClient):
    missing_id = str(uuid.uuid4())
    response = client.post(
        f"/api/v1/tasks/{missing_id}/reminders", json={"remindAt": BEFORE_DEADLINE}
    )
    assert response.status_code == 404

    body = response.json()
    assert body["success"] is False
    assert body["message"] == "Task not found"
    assert body["error"]["code"] == "TASK_NOT_FOUND"
    assert missing_id in body["error"]["message"]


def test_create_reminder_after_deadline(client: TestClient):
    task_id = _create_task(client)
    response = client.post(
        f"/api/v1/tasks/{task_id}/reminders", json={"remindAt": AFTER_DEADLINE}
    )
    assert response.status_code == 400

    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INVALID_REMINDER"
    assert "remind_at" in body["error"]["message"]


def test_create_reminder_exactly_at_deadline_allowed(client: TestClient):
    task_id = _create_task(client)
    response = client.post(
        f"/api/v1/tasks/{task_id}/reminders", json={"remindAt": TASK_DEADLINE}
    )
    assert response.status_code == 201


# --- 2. GET /api/v1/tasks/{taskId}/reminders ---


def test_list_reminders_existing_task(client: TestClient):
    task_id = _create_task(client)
    _create_reminder(client, task_id, "2026-11-29T08:00:00Z")
    _create_reminder(client, task_id, "2026-11-30T08:00:00Z")

    response = client.get(f"/api/v1/tasks/{task_id}/reminders")
    assert response.status_code == 200

    body = response.json()
    assert body["success"] is True
    assert body["message"] == "Reminders retrieved successfully"
    assert len(body["data"]) == 2
    assert all(r["taskId"] == task_id for r in body["data"])


def test_list_reminders_empty(client: TestClient):
    task_id = _create_task(client)
    response = client.get(f"/api/v1/tasks/{task_id}/reminders")
    assert response.status_code == 200
    assert response.json()["data"] == []


def test_list_reminders_scoped_to_task(client: TestClient):
    task_a = _create_task(client)
    task_b = _create_task(client)
    _create_reminder(client, task_a)
    _create_reminder(client, task_b)

    body = client.get(f"/api/v1/tasks/{task_a}/reminders").json()
    assert len(body["data"]) == 1
    assert body["data"][0]["taskId"] == task_a


def test_list_reminders_nonexistent_task(client: TestClient):
    missing_id = str(uuid.uuid4())
    response = client.get(f"/api/v1/tasks/{missing_id}/reminders")
    assert response.status_code == 404

    body = response.json()
    assert body["message"] == "Task not found"
    assert body["error"]["code"] == "TASK_NOT_FOUND"


# --- 3. PATCH /api/v1/reminders/{reminderId} ---


def test_patch_reminder_remind_at(client: TestClient):
    task_id = _create_task(client)
    created = _create_reminder(client, task_id)

    response = client.patch(
        f"/api/v1/reminders/{created['reminderId']}",
        json={"remindAt": "2026-11-28T07:00:00Z"},
    )
    assert response.status_code == 200

    body = response.json()
    assert body["message"] == "Reminder updated successfully"
    assert body["data"]["remindAt"] != created["remindAt"]
    assert body["data"]["status"] == "PENDING"


def test_patch_reminder_status(client: TestClient):
    task_id = _create_task(client)
    created = _create_reminder(client, task_id)

    response = client.patch(
        f"/api/v1/reminders/{created['reminderId']}", json={"status": "CANCELLED"}
    )
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "CANCELLED"


def test_patch_reminder_omitted_fields_unchanged(client: TestClient):
    task_id = _create_task(client)
    created = _create_reminder(client, task_id)

    response = client.patch(
        f"/api/v1/reminders/{created['reminderId']}", json={"status": "CANCELLED"}
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["remindAt"] == created["remindAt"]
    assert data["reminderId"] == created["reminderId"]
    assert data["taskId"] == created["taskId"]


def test_patch_reminder_empty_payload_is_noop(client: TestClient):
    task_id = _create_task(client)
    created = _create_reminder(client, task_id)

    response = client.patch(f"/api/v1/reminders/{created['reminderId']}", json={})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["remindAt"] == created["remindAt"]
    assert data["status"] == created["status"]


def test_patch_reminder_invalid_status(client: TestClient):
    task_id = _create_task(client)
    created = _create_reminder(client, task_id)

    response = client.patch(
        f"/api/v1/reminders/{created['reminderId']}", json={"status": "IN_PROGRESS"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_patch_reminder_sent_status_not_assignable(client: TestClient):
    """The API schema must not accept SENT as an assignable update status."""
    task_id = _create_task(client)
    created = _create_reminder(client, task_id)

    response = client.patch(
        f"/api/v1/reminders/{created['reminderId']}", json={"status": "SENT"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_patch_reminder_invalid_datetime(client: TestClient):
    task_id = _create_task(client)
    created = _create_reminder(client, task_id)

    response = client.patch(
        f"/api/v1/reminders/{created['reminderId']}", json={"remindAt": "tomorrow"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_patch_reminder_after_deadline(client: TestClient):
    task_id = _create_task(client)
    created = _create_reminder(client, task_id)

    response = client.patch(
        f"/api/v1/reminders/{created['reminderId']}",
        json={"remindAt": AFTER_DEADLINE},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REMINDER"


def test_patch_reminder_exactly_at_deadline_allowed(client: TestClient):
    task_id = _create_task(client)
    created = _create_reminder(client, task_id)

    response = client.patch(
        f"/api/v1/reminders/{created['reminderId']}", json={"remindAt": TASK_DEADLINE}
    )
    assert response.status_code == 200


def test_patch_nonexistent_reminder(client: TestClient):
    missing_id = str(uuid.uuid4())
    response = client.patch(
        f"/api/v1/reminders/{missing_id}", json={"remindAt": BEFORE_DEADLINE}
    )
    assert response.status_code == 404

    body = response.json()
    assert body["success"] is False
    assert body["message"] == "Reminder not found"
    assert body["error"]["code"] == "REMINDER_NOT_FOUND"
    assert missing_id in body["error"]["message"]


def test_patch_sent_reminder_returns_400(client: TestClient):
    """A SENT reminder is rejected by the Service and mapped to 400."""
    task_id = _create_task(client)
    created = _create_reminder(client, task_id)

    engine = client.app.state.engine
    with engine.begin() as conn:
        conn.execute(
            update(Reminder)
            .where(Reminder.id == uuid.UUID(created["reminderId"]))
            .values(status=ReminderStatus.SENT.value)
        )

    response = client.patch(
        f"/api/v1/reminders/{created['reminderId']}",
        json={"remindAt": "2026-11-28T07:00:00Z"},
    )
    assert response.status_code == 400

    body = response.json()
    assert body["error"]["code"] == "INVALID_REMINDER"
    assert "SENT" in body["error"]["message"]


# --- 4. DELETE /api/v1/reminders/{reminderId} ---


def test_delete_reminder_existing(client: TestClient):
    task_id = _create_task(client)
    created = _create_reminder(client, task_id)

    response = client.delete(f"/api/v1/reminders/{created['reminderId']}")
    assert response.status_code == 204
    assert response.content == b""

    remaining = client.get(f"/api/v1/tasks/{task_id}/reminders").json()["data"]
    assert remaining == []


def test_delete_reminder_does_not_delete_parent_task(client: TestClient):
    task_id = _create_task(client)
    created = _create_reminder(client, task_id)

    client.delete(f"/api/v1/reminders/{created['reminderId']}")

    assert client.get(f"/api/v1/tasks/{task_id}").status_code == 200


def test_delete_nonexistent_reminder(client: TestClient):
    missing_id = str(uuid.uuid4())
    response = client.delete(f"/api/v1/reminders/{missing_id}")
    assert response.status_code == 404

    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "REMINDER_NOT_FOUND"


# --- 5. Architecture verification: Handler calls ReminderModule ---


def test_handler_calls_reminder_module_without_bypass():
    """Verify handler strictly calls ReminderModule methods, proving no layer bypass."""
    mock_module = MagicMock(spec=ReminderModule)
    reminder_id = uuid.uuid4()
    task_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    mock_reminder = Reminder(
        task_id=task_id,
        remind_at=now + timedelta(hours=1),
        status=ReminderStatus.PENDING.value,
    )
    mock_reminder.id = reminder_id
    mock_reminder.created_at = now
    mock_reminder.updated_at = now

    mock_module.create_reminder.return_value = mock_reminder
    mock_module.list_reminders_by_task.return_value = [mock_reminder]
    mock_module.update_reminder.return_value = mock_reminder
    mock_module.delete_reminder.return_value = None

    app = create_app(Settings(database_url="sqlite:///:memory:"))
    app.dependency_overrides[get_reminder_module] = lambda: mock_module

    with TestClient(app) as test_client:
        r = test_client.post(
            f"/api/v1/tasks/{task_id}/reminders", json={"remindAt": BEFORE_DEADLINE}
        )
        assert r.status_code == 201
        assert mock_module.create_reminder.called

        r = test_client.get(f"/api/v1/tasks/{task_id}/reminders")
        assert r.status_code == 200
        mock_module.list_reminders_by_task.assert_called_with(task_id)

        r = test_client.patch(
            f"/api/v1/reminders/{reminder_id}", json={"status": "CANCELLED"}
        )
        assert r.status_code == 200
        assert mock_module.update_reminder.called

        r = test_client.delete(f"/api/v1/reminders/{reminder_id}")
        assert r.status_code == 204
        mock_module.delete_reminder.assert_called_with(reminder_id)


def test_handler_forwards_only_supplied_patch_fields():
    """model_fields_set must keep omitted PATCH fields out of the Module call."""
    mock_module = MagicMock(spec=ReminderModule)
    reminder_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    mock_reminder = Reminder(
        task_id=uuid.uuid4(),
        remind_at=now,
        status=ReminderStatus.CANCELLED.value,
    )
    mock_reminder.id = reminder_id
    mock_reminder.created_at = now
    mock_reminder.updated_at = now
    mock_module.update_reminder.return_value = mock_reminder

    app = create_app(Settings(database_url="sqlite:///:memory:"))
    app.dependency_overrides[get_reminder_module] = lambda: mock_module

    with TestClient(app) as test_client:
        test_client.patch(
            f"/api/v1/reminders/{reminder_id}", json={"status": "CANCELLED"}
        )

    mock_module.update_reminder.assert_called_once_with(
        reminder_id, status=ReminderStatus.CANCELLED
    )


# --- 6. Unexpected server error (500) contract ---


def test_reminder_unexpected_server_error_contract():
    """Verify unhandled exceptions return the agreed HTTP 500 error contract."""
    mock_module = MagicMock(spec=ReminderModule)
    mock_module.list_reminders_by_task.side_effect = RuntimeError("Unexpected crash")

    app = create_app(Settings(database_url="sqlite:///:memory:"))
    app.dependency_overrides[get_reminder_module] = lambda: mock_module

    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get(f"/api/v1/tasks/{uuid.uuid4()}/reminders")
        assert response.status_code == 500
        assert response.json() == {
            "success": False,
            "message": "Internal server error",
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "Internal server error",
            },
        }
