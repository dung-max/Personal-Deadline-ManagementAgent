"""Tests for PDMA-102: duration_minutes wiring through Service + Module + Handler.

Covers:
- TaskService.create_task: duration_minutes persistence
- TaskService.update_task: _UNSET / None / positive int semantics
- TaskService validation: 0 and negative rejection
- TaskHandler create: duration_minutes passed through to module
- TaskHandler update: model_fields_set dispatch (omitted / null / value)
- Regression: existing behavior unchanged when duration is omitted
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from personal_deadline_management_agent.config import Settings
from personal_deadline_management_agent.exceptions.task import InvalidTaskError
from personal_deadline_management_agent.main import create_app
from personal_deadline_management_agent.models import Task, TaskPriority, TaskStatus
from personal_deadline_management_agent.repositories.task_repository import TaskRepository
from personal_deadline_management_agent.services.task_service import TaskService

from genai_core.genai_shared.database import Base


# ---------------------------------------------------------------------------
# Fake repo (shared with existing test_task_service.py pattern)
# ---------------------------------------------------------------------------

class FakeTaskRepository:
    def __init__(self) -> None:
        self.tasks: dict[uuid.UUID, Task] = {}
        self.create_called = False
        self.update_called = False

    def create(self, task: Task) -> Task:
        self.create_called = True
        if task.id is None:
            task.id = uuid.uuid4()
        self.tasks[task.id] = task
        return task

    def get_by_id(self, task_id: uuid.UUID) -> Task | None:
        return self.tasks.get(task_id)

    def list(self) -> list[Task]:
        return list(self.tasks.values())

    def update(self, task: Task) -> Task:
        self.update_called = True
        self.tasks[task.id] = task
        return task

    def delete(self, task_id: uuid.UUID) -> bool:
        if task_id in self.tasks:
            del self.tasks[task_id]
            return True
        return False

    def find_by_name(self, phrase: str) -> list[Task]:
        return []

    def find_by_deadline_range(self, start: datetime, end: datetime) -> list[Task]:
        return []


@pytest.fixture
def fake_repo() -> FakeTaskRepository:
    return FakeTaskRepository()


@pytest.fixture
def svc(fake_repo: FakeTaskRepository) -> TaskService:
    return TaskService(fake_repo)  # type: ignore[arg-type]


FUTURE = datetime.now(timezone.utc) + timedelta(days=30)


# ===========================================================================
# 1. TaskService.create_task — duration_minutes
# ===========================================================================


class TestServiceCreateDuration:
    def test_create_omitted_duration_is_none(self, svc: TaskService, fake_repo: FakeTaskRepository) -> None:
        task = svc.create_task("T", None, FUTURE, TaskPriority.LOW)
        assert task.duration_minutes is None
        assert fake_repo.create_called

    def test_create_with_duration_120(self, svc: TaskService, fake_repo: FakeTaskRepository) -> None:
        task = svc.create_task("T", None, FUTURE, TaskPriority.LOW, duration_minutes=120)
        assert task.duration_minutes == 120

    def test_create_with_duration_1(self, svc: TaskService, fake_repo: FakeTaskRepository) -> None:
        task = svc.create_task("T", None, FUTURE, TaskPriority.LOW, duration_minutes=1)
        assert task.duration_minutes == 1

    def test_create_with_explicit_none_duration(self, svc: TaskService, fake_repo: FakeTaskRepository) -> None:
        task = svc.create_task("T", None, FUTURE, TaskPriority.LOW, duration_minutes=None)
        assert task.duration_minutes is None

    def test_create_with_duration_zero_rejected(self, svc: TaskService, fake_repo: FakeTaskRepository) -> None:
        with pytest.raises(InvalidTaskError, match="positive"):
            svc.create_task("T", None, FUTURE, TaskPriority.LOW, duration_minutes=0)
        assert not fake_repo.create_called

    def test_create_with_negative_duration_rejected(self, svc: TaskService, fake_repo: FakeTaskRepository) -> None:
        with pytest.raises(InvalidTaskError, match="positive"):
            svc.create_task("T", None, FUTURE, TaskPriority.LOW, duration_minutes=-10)
        assert not fake_repo.create_called


# ===========================================================================
# 2. TaskService.update_task — duration_minutes PATCH semantics
# ===========================================================================


class TestServiceUpdateDuration:
    def _create_with_duration(self, svc: TaskService, fake_repo: FakeTaskRepository, dur: int | None = None) -> Task:
        task = svc.create_task("T", None, FUTURE, TaskPriority.LOW, duration_minutes=dur)
        fake_repo.update_called = False  # reset
        return task

    def test_update_omitted_preserves_existing(self, svc: TaskService, fake_repo: FakeTaskRepository) -> None:
        task = self._create_with_duration(svc, fake_repo, dur=120)
        updated = svc.update_task(task.id)  # no duration_minutes arg → _UNSET
        assert updated.duration_minutes == 120
        # TaskService always calls repo.update; value must simply be preserved

    def test_update_explicit_none_clears(self, svc: TaskService, fake_repo: FakeTaskRepository) -> None:
        task = self._create_with_duration(svc, fake_repo, dur=120)
        updated = svc.update_task(task.id, duration_minutes=None)
        assert updated.duration_minutes is None
        assert fake_repo.update_called

    def test_update_positive_int_replaces(self, svc: TaskService, fake_repo: FakeTaskRepository) -> None:
        task = self._create_with_duration(svc, fake_repo, dur=120)
        updated = svc.update_task(task.id, duration_minutes=180)
        assert updated.duration_minutes == 180
        assert fake_repo.update_called

    def test_update_from_none_to_positive(self, svc: TaskService, fake_repo: FakeTaskRepository) -> None:
        task = self._create_with_duration(svc, fake_repo, dur=None)
        updated = svc.update_task(task.id, duration_minutes=60)
        assert updated.duration_minutes == 60

    def test_update_zero_rejected(self, svc: TaskService, fake_repo: FakeTaskRepository) -> None:
        task = self._create_with_duration(svc, fake_repo, dur=120)
        with pytest.raises(InvalidTaskError, match="positive"):
            svc.update_task(task.id, duration_minutes=0)
        assert not fake_repo.update_called

    def test_update_negative_rejected(self, svc: TaskService, fake_repo: FakeTaskRepository) -> None:
        task = self._create_with_duration(svc, fake_repo, dur=120)
        with pytest.raises(InvalidTaskError, match="positive"):
            svc.update_task(task.id, duration_minutes=-5)
        assert not fake_repo.update_called


# ===========================================================================
# 3. Handler create — duration_minutes passthrough
# ===========================================================================


@pytest.fixture
def client(tmp_path) -> TestClient:
    db_file = tmp_path / "test.db"
    settings = Settings(database_url=f"sqlite:///{db_file}")
    app = create_app(settings)
    with TestClient(app) as test_client:
        Base.metadata.create_all(app.state.engine)
        yield test_client


class TestHandlerCreateDuration:
    def test_create_omitted_duration_returns_null(self, client: TestClient) -> None:
        resp = client.post("/api/v1/tasks", json={
            "taskName": "T",
            "deadline": FUTURE.isoformat().replace("+00:00", "Z"),
            "priority": "LOW",
        })
        assert resp.status_code == 201
        assert resp.json()["data"]["durationMinutes"] is None

    def test_create_with_duration_returns_value(self, client: TestClient) -> None:
        resp = client.post("/api/v1/tasks", json={
            "taskName": "T",
            "deadline": FUTURE.isoformat().replace("+00:00", "Z"),
            "priority": "LOW",
            "durationMinutes": 120,
        })
        assert resp.status_code == 201
        assert resp.json()["data"]["durationMinutes"] == 120

    def test_create_duration_zero_rejected(self, client: TestClient) -> None:
        resp = client.post("/api/v1/tasks", json={
            "taskName": "T",
            "deadline": FUTURE.isoformat().replace("+00:00", "Z"),
            "priority": "LOW",
            "durationMinutes": 0,
        })
        assert resp.status_code == 400

    def test_create_negative_duration_rejected(self, client: TestClient) -> None:
        resp = client.post("/api/v1/tasks", json={
            "taskName": "T",
            "deadline": FUTURE.isoformat().replace("+00:00", "Z"),
            "priority": "LOW",
            "durationMinutes": -10,
        })
        assert resp.status_code == 400


# ===========================================================================
# 4. Handler update — PATCH semantics for durationMinutes
# ===========================================================================


class TestHandlerUpdateDuration:
    def _create_task(self, client: TestClient, duration: int | None = None) -> str:
        payload = {
            "taskName": "T",
            "deadline": FUTURE.isoformat().replace("+00:00", "Z"),
            "priority": "LOW",
        }
        if duration is not None:
            payload["durationMinutes"] = duration
        resp = client.post("/api/v1/tasks", json=payload)
        assert resp.status_code == 201
        return resp.json()["data"]["taskId"]

    def test_update_omitted_preserves_duration(self, client: TestClient) -> None:
        task_id = self._create_task(client, duration=120)
        # PATCH only taskName — durationMinutes not sent
        resp = client.patch(f"/api/v1/tasks/{task_id}", json={"taskName": "Renamed"})
        assert resp.status_code == 200
        assert resp.json()["data"]["durationMinutes"] == 120
        assert resp.json()["data"]["taskName"] == "Renamed"

    def test_update_null_clears_duration(self, client: TestClient) -> None:
        task_id = self._create_task(client, duration=120)
        resp = client.patch(f"/api/v1/tasks/{task_id}", json={"durationMinutes": None})
        assert resp.status_code == 200
        assert resp.json()["data"]["durationMinutes"] is None

    def test_update_positive_replaces_duration(self, client: TestClient) -> None:
        task_id = self._create_task(client, duration=120)
        resp = client.patch(f"/api/v1/tasks/{task_id}", json={"durationMinutes": 180})
        assert resp.status_code == 200
        assert resp.json()["data"]["durationMinutes"] == 180

    def test_update_zero_rejected(self, client: TestClient) -> None:
        task_id = self._create_task(client, duration=120)
        resp = client.patch(f"/api/v1/tasks/{task_id}", json={"durationMinutes": 0})
        assert resp.status_code == 400

    def test_update_negative_rejected(self, client: TestClient) -> None:
        task_id = self._create_task(client, duration=120)
        resp = client.patch(f"/api/v1/tasks/{task_id}", json={"durationMinutes": -5})
        assert resp.status_code == 400


# ===========================================================================
# 5. Regression — existing tests should remain unaffected
# ===========================================================================


class TestRegression:
    def test_create_task_defaults_to_todo(self, svc: TaskService, fake_repo: FakeTaskRepository) -> None:
        task = svc.create_task("T", "desc", FUTURE, TaskPriority.HIGH)
        assert task.status == TaskStatus.TODO.value
        assert task.priority == TaskPriority.HIGH.value
        assert task.task_name == "T"
        assert task.description == "desc"
        assert task.duration_minutes is None

    def test_update_preserves_unrelated_fields(self, svc: TaskService, fake_repo: FakeTaskRepository) -> None:
        task = svc.create_task("Orig", "Orig Desc", FUTURE, TaskPriority.MEDIUM)
        updated = svc.update_task(task.id, task_name="New", priority=TaskPriority.HIGH)
        assert updated.task_name == "New"
        assert updated.priority == TaskPriority.HIGH.value
        assert updated.description == "Orig Desc"
        assert updated.deadline == FUTURE
        assert updated.duration_minutes is None  # unchanged from original

    def test_handler_create_existing_fields_unaffected(self, client: TestClient) -> None:
        resp = client.post("/api/v1/tasks", json={
            "taskName": "Existing",
            "deadline": FUTURE.isoformat().replace("+00:00", "Z"),
            "priority": "HIGH",
            "description": "test",
        })
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["taskName"] == "Existing"
        assert data["description"] == "test"
        assert data["priority"] == "HIGH"
        assert data["status"] == "TODO"
        assert data["durationMinutes"] is None
