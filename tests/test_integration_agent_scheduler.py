"""Integration test: full Agent → Task → Reminder → Scheduler → SENT flow.

Proves the Agent and Scheduler are decoupled (Agent → Database ← Scheduler).
Only the LLM is faked; everything else uses real modules, services, repositories,
and database (SQLite in-memory).
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from genai_core.genai_shared.database import Base
from personal_deadline_management_agent.config import Settings
from personal_deadline_management_agent.dependencies import (
    get_agent_response_generator,
    get_llm,
    get_session_factory,
)
from personal_deadline_management_agent.main import create_app
from personal_deadline_management_agent.models import Reminder, Task
from personal_deadline_management_agent.schemas.agent import InterpretationOutput

# Fixed datetimes — deterministic, no real-time dependency.
# TASK_DEADLINE must be far in the future: TaskService.create_task rejects
# deadlines < datetime.now(timezone.utc) (task_service.py:33-36).
TASK_DEADLINE = datetime(2099, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
REMIND_AT = datetime(2026, 9, 9, 10, 0, 0, tzinfo=timezone.utc)
TICK_NOW = datetime(2026, 9, 9, 10, 0, 1, tzinfo=timezone.utc)  # 1s after REMIND_AT


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeStructuredGenerationPort:
    """Fake LLM that returns scripted InterpretationOutput in order."""

    def __init__(self, outputs: list[InterpretationOutput]) -> None:
        self._iter = iter(outputs)

    def generate(self, *, system_prompt: str, user_prompt: str, output_type: type) -> InterpretationOutput:  # type: ignore[override]
        return next(self._iter)


class RecordingNotificationProvider:
    """Records send() calls for assertion.

    Captures scalar values immediately — the ORM instances are detached once
    the scheduler's session closes, so attribute access afterwards fails.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[uuid.UUID, uuid.UUID, str]] = []

    def send(self, *, reminder: Reminder, task: Task) -> None:
        self.calls.append((reminder.id, task.id, task.task_name))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_agent_creates_task_then_reminder_scheduler_sends(engine):  # type: ignore[no-untyped-def]
    """Full happy path: Agent creates Task → Reminder (PENDING) → Scheduler → SENT."""
    sf = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    fake_llm = FakeStructuredGenerationPort(
        [
            InterpretationOutput(
                response_type="ACTION_PROPOSED",
                action_type="CREATE_TASK",
                parameters={
                    "taskName": "Report",
                    "deadline": TASK_DEADLINE.isoformat(),
                    "priority": "HIGH",
                },
                resource_id=None,
                resource_description=None,
            ),
            InterpretationOutput(
                response_type="ACTION_PROPOSED",
                action_type="CREATE_REMINDER",
                parameters={"remindAt": REMIND_AT.isoformat()},
                resource_id=None,
                resource_description="Report",
            ),
        ]
    )

    app = create_app(Settings(database_url="sqlite:///:memory:", single_user_mode=True))
    app.dependency_overrides[get_session_factory] = lambda: sf
    app.dependency_overrides[get_llm] = lambda: fake_llm

    from personal_deadline_management_agent.services.agent_response_generator import (
        AgentResponseGenerator,
    )
    app.dependency_overrides[get_agent_response_generator] = lambda: AgentResponseGenerator(
        llm=fake_llm, enable_llm=False
    )

    provider = RecordingNotificationProvider()

    with TestClient(app) as client:
        # --- Step 1: CREATE_TASK via Agent ---
        resp = client.post(
            "/api/v1/agent/chat",
            json={"message": "create a task Report", "userId": str(uuid4())},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "EXECUTED", body
        assert body["execution_result"]["resultName"] == "Report"
        task_id = body["execution_result"]["resultId"]
        assert task_id is not None

        # Verify Task in DB
        task_uuid = uuid.UUID(task_id)
        with engine.connect() as conn:
            rows = conn.execute(
                Task.__table__.select().where(Task.__table__.c.id == task_uuid)
            ).fetchall()
        assert len(rows) == 1
        task_row = rows[0]
        assert task_row.task_name == "Report"
        assert task_row.status == "TODO"

        # --- Step 2: CREATE_REMINDER via Agent (natural-language reference) ---
        resp2 = client.post(
            "/api/v1/agent/chat",
            json={"message": "set a reminder for Report", "userId": str(uuid4())},
        )
        assert resp2.status_code == 200
        body2 = resp2.json()
        assert body2["status"] == "EXECUTED", body2
        reminder_id = body2["execution_result"]["resultId"]
        assert reminder_id is not None

        # Verify Reminder in DB is PENDING before scheduler runs
        reminder_uuid = uuid.UUID(reminder_id)
        with engine.connect() as conn:
            rows2 = conn.execute(
                Reminder.__table__.select().where(
                    Reminder.__table__.c.id == reminder_uuid
                )
            ).fetchall()
        assert len(rows2) == 1
        reminder_row = rows2[0]
        assert str(reminder_row.task_id) == str(task_id)
        assert reminder_row.status == "PENDING"

    # --- Step 3: Scheduler tick (separate from Agent — proves decoupling) ---
    from personal_deadline_management_agent.scheduler import run_once

    result = run_once(
        session_factory=sf,
        notification_provider=provider,  # type: ignore[arg-type]
        now=TICK_NOW,
        limit=100,
    )
    assert result.sent_count == 1
    assert result.due_count == 1
    assert result.failed_count == 0
    assert result.skipped_count == 0

    # Verify Reminder is now SENT
    with engine.connect() as conn:
        row = conn.execute(
            Reminder.__table__.select().where(Reminder.__table__.c.id == reminder_uuid)
        ).fetchone()
        assert row is not None
        assert row.status == "SENT"

    # Verify provider was called with correct (reminder, task)
    assert len(provider.calls) == 1
    sent_reminder_id, sent_task_id, sent_task_name = provider.calls[0]
    assert sent_reminder_id == reminder_uuid
    assert sent_task_id == task_uuid
    assert sent_task_name == "Report"

    # Idempotent second tick — nothing due
    result2 = run_once(
        session_factory=sf,
        notification_provider=provider,  # type: ignore[arg-type]
        now=TICK_NOW,
        limit=100,
    )
    assert result2.due_count == 0
    assert result2.sent_count == 0


def test_agent_pipeline_does_not_import_scheduler():  # type: ignore[no-untyped-def]
    """Agent pipeline modules must not import scheduler modules.

    Proves Agent → Database ← Scheduler decoupling.
    Uses diff-based check to be robust to test ordering.
    """
    scheduler_modules = {
        "personal_deadline_management_agent.scheduler",
        "personal_deadline_management_agent.modules.scheduler_module",
        "personal_deadline_management_agent.services.scheduler_service",
    }
    before = set(sys.modules.keys()) & scheduler_modules

    from personal_deadline_management_agent.handlers import agent_handler  # noqa: F401

    newly_imported = (set(sys.modules.keys()) & scheduler_modules) - before
    assert not newly_imported, (
        f"Agent pipeline newly imports scheduler modules: {newly_imported}"
    )
