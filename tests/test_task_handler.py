"""Tests for TaskHandler and API endpoints.

Tests end-to-end HTTP request handling, response schemas (camelCase),
error contracts (400, 404, 500), and verifies that Handler delegates
strictly to TaskModule without bypassing any architectural layer.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from genai_core.genai_shared.database import Base
from personal_deadline_management_agent.config import Settings
from personal_deadline_management_agent.dependencies import get_task_module
from personal_deadline_management_agent.main import create_app
from personal_deadline_management_agent.models import Task, TaskPriority, TaskStatus
from personal_deadline_management_agent.modules.task_module import TaskModule


@pytest.fixture
def client(tmp_path):
    """TestClient with temporary file-based SQLite database."""
    db_file = tmp_path / "test.db"
    settings = Settings(database_url=f"sqlite:///{db_file}")
    app = create_app(settings)
    with TestClient(app) as test_client:
        Base.metadata.create_all(app.state.engine)
        yield test_client


# --- 1. POST /api/v1/tasks ---


def test_create_task_valid(client: TestClient):
    payload = {
        "taskName": "Complete quarterly review",
        "description": "Prepare summary slides",
        "deadline": "2026-10-15T17:00:00Z",
        "priority": "HIGH",
    }
    response = client.post("/api/v1/tasks", json=payload)
    assert response.status_code == 201

    body = response.json()
    assert body["success"] is True
    assert body["message"] == "Task created successfully"

    data = body["data"]
    assert "taskId" in data
    assert data["taskName"] == "Complete quarterly review"
    assert data["description"] == "Prepare summary slides"
    assert data["priority"] == "HIGH"
    assert data["status"] == "TODO"
    assert "createdAt" in data
    assert "updatedAt" in data


def test_create_task_missing_required_field(client: TestClient):
    # Missing taskName
    payload = {
        "description": "Missing task name",
        "deadline": "2026-10-15T17:00:00Z",
        "priority": "HIGH",
    }
    response = client.post("/api/v1/tasks", json=payload)
    assert response.status_code == 400

    body = response.json()
    assert body["success"] is False
    assert body["message"] == "Validation error"
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert "taskName" in body["error"]["message"] or "task_name" in body["error"]["message"]


def test_create_task_invalid_priority(client: TestClient):
    payload = {
        "taskName": "Invalid Priority Task",
        "deadline": "2026-10-15T17:00:00Z",
        "priority": "URGENT",  # Invalid enum value
    }
    response = client.post("/api/v1/tasks", json=payload)
    assert response.status_code == 400

    body = response.json()
    assert body["success"] is False
    assert body["message"] == "Validation error"
    assert body["error"]["code"] == "VALIDATION_ERROR"


# --- 2. GET /api/v1/tasks/{taskId} ---


def test_get_existing_task(client: TestClient):
    # Create task first
    created_resp = client.post(
        "/api/v1/tasks",
        json={
            "taskName": "To Fetch",
            "deadline": "2026-11-01T10:00:00Z",
            "priority": "MEDIUM",
        },
    )
    task_id = created_resp.json()["data"]["taskId"]

    # Fetch it
    response = client.get(f"/api/v1/tasks/{task_id}")
    assert response.status_code == 200

    body = response.json()
    assert body["success"] is True
    assert body["message"] == "Task retrieved successfully"
    assert body["data"]["taskId"] == task_id
    assert body["data"]["taskName"] == "To Fetch"


def test_get_nonexistent_task(client: TestClient):
    random_id = str(uuid.uuid4())
    response = client.get(f"/api/v1/tasks/{random_id}")
    assert response.status_code == 404

    body = response.json()
    assert body["success"] is False
    assert body["message"] == "Task not found"
    assert body["error"]["code"] == "TASK_NOT_FOUND"
    assert random_id in body["error"]["message"]


# --- 3. GET /api/v1/tasks ---


def test_list_tasks(client: TestClient):
    # Empty initial list
    response = client.get("/api/v1/tasks")
    assert response.status_code == 200
    assert response.json()["data"] == []

    # Create two tasks
    client.post(
        "/api/v1/tasks",
        json={"taskName": "T1", "deadline": "2026-12-01T00:00:00Z", "priority": "LOW"},
    )
    client.post(
        "/api/v1/tasks",
        json={"taskName": "T2", "deadline": "2026-12-02T00:00:00Z", "priority": "HIGH"},
    )

    response = client.get("/api/v1/tasks")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["message"] == "Tasks retrieved successfully"
    assert len(body["data"]) == 2
    names = {t["taskName"] for t in body["data"]}
    assert names == {"T1", "T2"}


# --- 4. PATCH /api/v1/tasks/{taskId} ---


def test_patch_partial_update(client: TestClient):
    created_resp = client.post(
        "/api/v1/tasks",
        json={
            "taskName": "Original",
            "description": "Orig Desc",
            "deadline": "2026-12-01T00:00:00Z",
            "priority": "LOW",
        },
    )
    task_id = created_resp.json()["data"]["taskId"]

    # Partially update only taskName and status
    patch_resp = client.patch(
        f"/api/v1/tasks/{task_id}",
        json={"taskName": "Updated Title", "status": "IN_PROGRESS"},
    )
    assert patch_resp.status_code == 200
    data = patch_resp.json()["data"]
    assert data["taskName"] == "Updated Title"
    assert data["status"] == "IN_PROGRESS"
    # Preserved fields
    assert data["description"] == "Orig Desc"
    assert data["priority"] == "LOW"


def test_patch_nonexistent_task(client: TestClient):
    random_id = str(uuid.uuid4())
    response = client.patch(
        f"/api/v1/tasks/{random_id}",
        json={"taskName": "Nonexistent"},
    )
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "TASK_NOT_FOUND"


# --- 5. DELETE /api/v1/tasks/{taskId} ---


def test_delete_existing_task(client: TestClient):
    created_resp = client.post(
        "/api/v1/tasks",
        json={
            "taskName": "To Delete",
            "deadline": "2026-12-01T00:00:00Z",
            "priority": "LOW",
        },
    )
    task_id = created_resp.json()["data"]["taskId"]

    # Delete
    del_resp = client.delete(f"/api/v1/tasks/{task_id}")
    assert del_resp.status_code == 204
    assert del_resp.content == b""

    # Confirm it's gone
    get_resp = client.get(f"/api/v1/tasks/{task_id}")
    assert get_resp.status_code == 404


def test_delete_nonexistent_task(client: TestClient):
    random_id = str(uuid.uuid4())
    response = client.delete(f"/api/v1/tasks/{random_id}")
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "TASK_NOT_FOUND"


# --- 6. Architecture Verification: Handler calls TaskModule ---


def test_handler_calls_task_module_without_bypass():
    """Verify handler strictly calls TaskModule methods, proving no layer bypass."""
    mock_module = MagicMock(spec=TaskModule)
    test_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    mock_task = Task(
        task_name="Mocked Task",
        description="Mocked Desc",
        deadline=now,
        priority=TaskPriority.HIGH.value,
        status=TaskStatus.TODO.value,
    )
    mock_task.id = test_id
    mock_task.created_at = now
    mock_task.updated_at = now

    mock_module.create_task.return_value = mock_task
    mock_module.get_task.return_value = mock_task
    mock_module.list_tasks.return_value = [mock_task]
    mock_module.update_task.return_value = mock_task
    mock_module.delete_task.return_value = None

    app = create_app(Settings(database_url="sqlite:///:memory:"))
    app.dependency_overrides[get_task_module] = lambda: mock_module

    with TestClient(app) as test_client:
        # POST
        r = test_client.post(
            "/api/v1/tasks",
            json={"taskName": "M", "deadline": "2026-10-15T00:00:00Z", "priority": "HIGH"},
        )
        assert r.status_code == 201
        assert mock_module.create_task.called

        # GET
        r = test_client.get(f"/api/v1/tasks/{test_id}")
        assert r.status_code == 200
        mock_module.get_task.assert_called_with(test_id)

        # LIST
        r = test_client.get("/api/v1/tasks")
        assert r.status_code == 200
        assert mock_module.list_tasks.called

        # PATCH
        r = test_client.patch(f"/api/v1/tasks/{test_id}", json={"taskName": "New M"})
        assert r.status_code == 200
        assert mock_module.update_task.called

        # DELETE
        r = test_client.delete(f"/api/v1/tasks/{test_id}")
        assert r.status_code == 204
        mock_module.delete_task.assert_called_with(test_id)


# --- 7. Unexpected Server Error (500) contract ---


def test_unexpected_server_error_contract():
    """Verify unhandled exceptions return agreed HTTP 500 error contract."""
    mock_module = MagicMock(spec=TaskModule)
    mock_module.list_tasks.side_effect = RuntimeError("Unexpected internal crash")

    app = create_app(Settings(database_url="sqlite:///:memory:"))
    app.dependency_overrides[get_task_module] = lambda: mock_module

    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get("/api/v1/tasks")
        assert response.status_code == 500
        body = response.json()
        assert body == {
            "success": False,
            "message": "Internal server error",
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "Internal server error",
            },
        }
