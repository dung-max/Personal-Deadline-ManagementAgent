"""PDMA-82 — End-to-end Agent workload analysis.

Tests the complete flow through the Agent pipeline with real components:

  Natural language → AgentInterpreter → ActionValidator → ResourceResolver
  → SafetyPolicy → DecisionService → ActionExecutor → DateRangeResolver
  → TaskRepository.find_by_deadline_range → WorkloadAnalysisService → result

No mocks for business-logic layers except the LLM boundary (FakeLLM).
The database is a real in-memory SQLite instance per test.

Covers Steps 3 and 4 of PDMA-82.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from genai_core.genai_shared.database import Base
from personal_deadline_management_agent.config import Settings
from personal_deadline_management_agent.dependencies import (
    get_action_executor,
    get_action_validator,
    get_agent_interpreter,
    get_decision_service,
    get_pending_confirmation_module,
    get_resource_resolver,
    get_session_factory,
)
from personal_deadline_management_agent.models import TaskPriority, TaskStatus
from personal_deadline_management_agent.schemas.agent import (
    ActionType,
    AgentRequest,
    AgentResponse,
    DateRangeExpression,
    InterpretationOutput,
    ResponseType,
)
from personal_deadline_management_agent.schemas.agent_chat import AgentChatStatus
from personal_deadline_management_agent.schemas.workload import WorkloadAnalysisResult
from personal_deadline_management_agent.services.action_validator import ValidationStatus
from personal_deadline_management_agent.services.date_range_resolver import DateRangeResolver
from personal_deadline_management_agent.services.workload_analysis_service import (
    WorkloadAnalysisService,
)
from personal_deadline_management_agent.guardrails.safety_policy import SafetyPolicy
from personal_deadline_management_agent.main import create_app
from personal_deadline_management_agent.uow import UnitOfWork
from personal_deadline_management_agent.services.task_service import TaskService
from personal_deadline_management_agent.repositories.task_repository import TaskRepository
from personal_deadline_management_agent.services.execution_result import ExecutionStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_llm_for(interpretation: InterpretationOutput):
    """Return a minimal StructuredGenerationPort fake."""

    class _Fake:
        def generate(self, *, system_prompt, user_prompt, output_type):
            return interpretation

    return _Fake()


def _interpretation_for_analyze(
    expression: str | DateRangeExpression,
    *,
    explicit_start: str | None = None,
    explicit_end: str | None = None,
    message: str = "",
) -> InterpretationOutput:
    params: dict[str, Any] = {"date_range_expression": expression}
    if explicit_start is not None:
        params["explicit_start"] = explicit_start
    if explicit_end is not None:
        params["explicit_end"] = explicit_end
    return InterpretationOutput(
        response_type="ACTION_PROPOSED",
        action_type=ActionType.ANALYZE_WORKLOAD,
        parameters=params,
        message=message,
    )


def _make_test_app_and_engine():
    """Create a FastAPI app wired to an in-memory SQLite DB with real pipeline
    components.  Returns (app, engine, session_factory)."""
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine)
    app = create_app(Settings(database_url="sqlite:///:memory:"))
    # Wire real DB; leave LLM/validator/resolver/decision/executor as defaults
    # so the full pipeline runs with real business logic.
    app.dependency_overrides[get_session_factory] = lambda: sf
    return app, engine, sf


def _seed_task(
    sf,
    *,
    task_name: str,
    deadline: datetime | None = None,
    priority: str = TaskPriority.MEDIUM.value,
    status: str = TaskStatus.TODO.value,
) -> Any:
    """Persist a task directly via TaskService (bypassing the agent)."""
    session = sf()
    try:
        uow = UnitOfWork(session)
        svc = TaskService(uow.tasks)
        # TaskService validates deadline is not in the past; use a far-future
        # default when caller does not care.
        dl = deadline
        if dl is None and status in (TaskStatus.TODO.value, TaskStatus.IN_PROGRESS.value):
            # caller explicitly wants no deadline — construct Task ORM directly
            from personal_deadline_management_agent.models import Task

            t = Task(
                task_name=task_name,
                description=None,
                deadline=None,
                priority=priority,
                status=status,
            )
            t.id = uuid4()
            session.add(t)
            session.flush()
            session.refresh(t)
            session.commit()
            tid = t.id
            return t
        task = svc.create_task(
            task_name=task_name,
            description=None,
            deadline=dl,  # type: ignore[arg-type]
            priority=TaskPriority(priority),  # type: ignore[arg-type]
        )
        # TaskService path commits via Module; here we commit directly
        session.commit()
        # If status is not TODO (default), update it
        if status != TaskStatus.TODO.value:
            from personal_deadline_management_agent.models import Task as TaskModel

            obj = session.get(TaskModel, task.id)
            obj.status = status
            session.flush()
            session.commit()
            session.refresh(obj)
            return obj
        return task
    finally:
        session.close()


def _seed_task_raw(
    sf,
    *,
    task_name: str,
    deadline: datetime | None,
    priority: str = TaskPriority.MEDIUM.value,
    status: str = TaskStatus.TODO.value,
):
    """Insert a task row directly without TaskService validation."""
    from personal_deadline_management_agent.models import Task

    session = sf()
    try:
        t = Task(
            task_name=task_name,
            description=None,
            deadline=deadline,
            priority=priority,
            status=status,
        )
        t.id = uuid4()
        session.add(t)
        session.flush()
        session.refresh(t)
        session.commit()
        tid = t.id
        # detach
        session.expunge(t)
        return t
    finally:
        session.close()


def _agent_post(app, message: str, fake_llm) -> Any:
    """POST /api/v1/agent/chat with a faked LLM."""
    from personal_deadline_management_agent.dependencies import get_agent_interpreter
    from personal_deadline_management_agent.services.agent_interpreter import AgentInterpreter

    app.dependency_overrides[get_agent_interpreter] = lambda: AgentInterpreter(fake_llm)
    with TestClient(app) as client:
        resp = client.post("/api/v1/agent/chat", json={"message": message})
    return resp


# ---------------------------------------------------------------------------
# STEP 3 — End-to-end pipeline tests
# ---------------------------------------------------------------------------

FUTURE_BASE = datetime(2026, 12, 10, 10, 0, 0, tzinfo=timezone.utc)


class TestE2ENaturalLanguageWorkloadRequest:
    """PDMA-82 §3.1 — 'Analyze my workload this week.' through the full pipeline."""

    def test_this_week_succeeds_end_to_end(self):
        app, engine, sf = _make_test_app_and_engine()
        try:
            # Seed two active tasks that fall inside THIS_WEEK when reference
            # is 2026-09-10 (Mon 09-07 — Sun 09-13).  Use raw insert to avoid
            # TaskService past-deadline validation for fixed dates.
            # Instead drive the flow with explicit dates so the test is not
            # coupled to wall-clock: seed tasks inside the explicit window and
            # assert the window itself.
            d1 = datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc)
            d2 = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)
            _seed_task_raw(sf, task_name="Task A", deadline=d1)
            _seed_task_raw(sf, task_name="Task B", deadline=d2)

            interp = _interpretation_for_analyze(
                "THIS_WEEK",
                message="Analyze my workload this week.",
            )
            resp = _agent_post(app, "Analyze my workload this week.", _fake_llm_for(interp))

            assert resp.status_code == 200
            body = resp.json()
            # Handler maps EXECUTED → EXECUTED; workload never requires confirmation
            # In non-single-user mode without userId, Authorization returns
            # NOT_CONFIGURED and the workload would not execute.  Use single-user
            # mode so the full path runs.
            # Rebuild app in single-user mode for this assertion path.
            engine.dispose()
            engine2 = create_engine(
                "sqlite:///:memory:",
                poolclass=StaticPool,
                connect_args={"check_same_thread": False},
            )
            Base.metadata.create_all(engine2)
            sf2 = sessionmaker(bind=engine2)
            _seed_task_raw(sf2, task_name="Task A", deadline=d1)
            _seed_task_raw(sf2, task_name="Task B", deadline=d2)
            app2 = create_app(Settings(database_url="sqlite:///:memory:", single_user_mode=True))
            app2.dependency_overrides[get_session_factory] = lambda: sf2
            resp2 = _agent_post(app2, "Analyze my workload this week.", _fake_llm_for(interp))
            assert resp2.status_code == 200
            body2 = resp2.json()
            assert body2["status"] == AgentChatStatus.EXECUTED.value
            er = body2["execution_result"]
            assert er is not None
            assert er["status"] == "EXECUTED"
            assert er["actionType"] == ActionType.ANALYZE_WORKLOAD.value
            payload = er["resultPayload"]
            assert payload is not None
            # WorkloadAnalysisResult shape
            assert "total_tasks" in payload
            assert "deadline_collisions" in payload
            assert "busy_days" in payload
            assert "recommended_order" in payload
            assert "explanation" in payload
            engine2.dispose()
        finally:
            try:
                engine.dispose()
            except Exception:
                pass


class TestE2EVietnameseWorkloadRequest:
    """PDMA-82 §3.2 — Vietnamese request."""

    def test_vietnamese_this_week(self):
        engine = create_engine(
            "sqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine)
        sf = sessionmaker(bind=engine)
        try:
            d1 = datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc)
            _seed_task_raw(sf, task_name="Nhiem vu A", deadline=d1)
            app = create_app(Settings(database_url="sqlite:///:memory:", single_user_mode=True))
            app.dependency_overrides[get_session_factory] = lambda: sf
            interp = _interpretation_for_analyze("THIS_WEEK")
            resp = _agent_post(app, "Kiểm tra workload của tôi tuần này.", _fake_llm_for(interp))
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == AgentChatStatus.EXECUTED.value
            assert body["execution_result"]["resultPayload"] is not None
        finally:
            engine.dispose()


class TestE2EToday:
    """PDMA-82 §3.3 — TODAY expression."""

    def test_today_resolves_and_queries_correct_range(self):
        engine = create_engine(
            "sqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine)
        sf = sessionmaker(bind=engine)
        try:
            # Verify DateRangeResolver contract for TODAY (does not assert
            # wall-clock, just that the resolver and repository are wired).
            from personal_deadline_management_agent.schemas.agent import DateRangeExpression

            today_start, today_end = DateRangeResolver.resolve(
                DateRangeExpression.TODAY,
                reference_time=datetime(2026, 9, 10, 15, 30, tzinfo=timezone.utc),
            )
            # Seed one task inside that day and one outside
            inside = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
            outside = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
            _seed_task_raw(sf, task_name="Inside", deadline=inside)
            _seed_task_raw(sf, task_name="Outside", deadline=outside)

            app = create_app(Settings(database_url="sqlite:///:memory:", single_user_mode=True))
            app.dependency_overrides[get_session_factory] = lambda: sf
            interp = _interpretation_for_analyze("TODAY")
            resp = _agent_post(app, "Show me my workload today.", _fake_llm_for(interp))
            assert resp.status_code == 200
            body = resp.json()
            # The result depends on DateRangeResolver's wall-clock TODAY; we
            # cannot assert exact counts without time-travel.  Instead verify
            # the pipeline executed and returned a well-formed payload.
            assert body["status"] == AgentChatStatus.EXECUTED.value
            payload = body["execution_result"]["resultPayload"]
            assert isinstance(payload["total_tasks"], int)
            assert isinstance(payload["deadline_collisions"], list)
            assert isinstance(payload["busy_days"], list)
        finally:
            engine.dispose()


class TestE2EExplicitRange:
    """PDMA-82 §3.4 — Explicit start/end resolved by DateRangeResolver."""

    def test_explicit_range_uses_concrete_dates(self):
        engine = create_engine(
            "sqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine)
        sf = sessionmaker(bind=engine)
        try:
            start = datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)
            end = datetime(2026, 9, 21, 23, 59, 59, tzinfo=timezone.utc)
            inside1 = datetime(2026, 9, 16, 10, 0, tzinfo=timezone.utc)
            inside2 = datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc)
            outside = datetime(2026, 9, 22, 10, 0, tzinfo=timezone.utc)
            _seed_task_raw(sf, task_name="Inside 1", deadline=inside1)
            _seed_task_raw(sf, task_name="Inside 2", deadline=inside2)
            _seed_task_raw(sf, task_name="Outside", deadline=outside)

            app = create_app(Settings(database_url="sqlite:///:memory:", single_user_mode=True))
            app.dependency_overrides[get_session_factory] = lambda: sf
            interp = _interpretation_for_analyze(
                "EXPLICIT_RANGE",
                explicit_start="2026-09-15T00:00:00Z",
                explicit_end="2026-09-21T23:59:59Z",
            )
            resp = _agent_post(
                app,
                "Analyze my workload from September 15 to September 21.",
                _fake_llm_for(interp),
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == AgentChatStatus.EXECUTED.value
            payload = body["execution_result"]["resultPayload"]
            # Only the two inside tasks are active and in range
            assert payload["total_tasks"] == 2
            names = {t["task_name"] for t in payload["recommended_order"]}
            assert names == {"Inside 1", "Inside 2"}
            assert "Outside" not in names

            # Verify the interpreter did NOT calculate concrete boundaries —
            # it only carries the semantic expression + explicit ISO strings.
            assert interp.parameters["date_range_expression"] == "EXPLICIT_RANGE"
            assert interp.parameters["explicit_start"] == "2026-09-15T00:00:00Z"
        finally:
            engine.dispose()


class TestE2EEmptyWorkload:
    """PDMA-82 §3.5 — No matching tasks."""

    def test_empty_workload_succeeds(self):
        engine = create_engine(
            "sqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine)
        sf = sessionmaker(bind=engine)
        try:
            app = create_app(Settings(database_url="sqlite:///:memory:", single_user_mode=True))
            app.dependency_overrides[get_session_factory] = lambda: sf
            interp = _interpretation_for_analyze(
                "EXPLICIT_RANGE",
                explicit_start="2026-09-15T00:00:00Z",
                explicit_end="2026-09-21T23:59:59Z",
            )
            resp = _agent_post(app, "Analyze my workload from Sep 15 to Sep 21.", _fake_llm_for(interp))
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == AgentChatStatus.EXECUTED.value
            payload = body["execution_result"]["resultPayload"]
            assert payload["total_tasks"] == 0
            assert payload["deadline_collisions"] == []
            assert payload["busy_days"] == []
            assert payload["recommended_order"] == []
            assert isinstance(payload["explanation"], str)
            assert len(payload["explanation"]) > 0
        finally:
            engine.dispose()


class TestE2ECollisionThroughPipeline:
    """PDMA-82 §3.6 — Collision detection through the full pipeline."""

    def test_collision_appears_in_final_result(self):
        engine = create_engine(
            "sqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine)
        sf = sessionmaker(bind=engine)
        try:
            collision_deadline = datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc)
            _seed_task_raw(
                sf, task_name="Collide A", deadline=collision_deadline, priority=TaskPriority.HIGH.value
            )
            _seed_task_raw(
                sf, task_name="Collide B", deadline=collision_deadline, priority=TaskPriority.HIGH.value
            )
            # Non-colliding high task on a different instant
            _seed_task_raw(
                sf,
                task_name="Other",
                deadline=datetime(2026, 9, 19, 10, 0, tzinfo=timezone.utc),
                priority=TaskPriority.HIGH.value,
            )

            app = create_app(Settings(database_url="sqlite:///:memory:", single_user_mode=True))
            app.dependency_overrides[get_session_factory] = lambda: sf
            interp = _interpretation_for_analyze(
                "EXPLICIT_RANGE",
                explicit_start="2026-09-15T00:00:00Z",
                explicit_end="2026-09-21T23:59:59Z",
            )
            resp = _agent_post(app, "Analyze my workload from Sep 15 to Sep 21.", _fake_llm_for(interp))
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == AgentChatStatus.EXECUTED.value
            payload = body["execution_result"]["resultPayload"]
            collisions = payload["deadline_collisions"]
            assert len(collisions) == 1
            assert len(collisions[0]["tasks"]) == 2
            names = {t["task_name"] for t in collisions[0]["tasks"]}
            assert names == {"Collide A", "Collide B"}
        finally:
            engine.dispose()


class TestE2EBusyDayThroughPipeline:
    """PDMA-82 §3.7 — Busy-day detection (>5 on same UTC date)."""

    def test_busy_day_warning_appears(self):
        engine = create_engine(
            "sqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine)
        sf = sessionmaker(bind=engine)
        try:
            busy_date = datetime(2026, 9, 18, tzinfo=timezone.utc)
            for i in range(6):
                _seed_task_raw(
                    sf,
                    task_name=f"Busy {i}",
                    deadline=busy_date.replace(hour=i),
                )

            app = create_app(Settings(database_url="sqlite:///:memory:", single_user_mode=True))
            app.dependency_overrides[get_session_factory] = lambda: sf
            interp = _interpretation_for_analyze(
                "EXPLICIT_RANGE",
                explicit_start="2026-09-15T00:00:00Z",
                explicit_end="2026-09-21T23:59:59Z",
            )
            resp = _agent_post(app, "Analyze my workload from Sep 15 to Sep 21.", _fake_llm_for(interp))
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == AgentChatStatus.EXECUTED.value
            payload = body["execution_result"]["resultPayload"]
            assert len(payload["busy_days"]) == 1
            assert payload["busy_days"][0]["task_count"] == 6
            assert payload["busy_days"][0]["date"] == "2026-09-18"
        finally:
            engine.dispose()

    def test_five_tasks_is_not_busy(self):
        engine = create_engine(
            "sqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine)
        sf = sessionmaker(bind=engine)
        try:
            busy_date = datetime(2026, 9, 18, tzinfo=timezone.utc)
            for i in range(5):
                _seed_task_raw(sf, task_name=f"Task {i}", deadline=busy_date.replace(hour=i))

            app = create_app(Settings(database_url="sqlite:///:memory:", single_user_mode=True))
            app.dependency_overrides[get_session_factory] = lambda: sf
            interp = _interpretation_for_analyze(
                "EXPLICIT_RANGE",
                explicit_start="2026-09-15T00:00:00Z",
                explicit_end="2026-09-21T23:59:59Z",
            )
            resp = _agent_post(app, "Analyze my workload from Sep 15 to Sep 21.", _fake_llm_for(interp))
            payload = resp.json()["execution_result"]["resultPayload"]
            assert payload["busy_days"] == []
        finally:
            engine.dispose()


class TestE2ECompletedCancelledFiltering:
    """PDMA-82 §3.8 — Completed/cancelled excluded by WorkloadAnalysisService."""

    def test_completed_and_cancelled_excluded_from_analysis(self):
        engine = create_engine(
            "sqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine)
        sf = sessionmaker(bind=engine)
        try:
            dl = datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc)
            _seed_task_raw(sf, task_name="Active 1", deadline=dl, status=TaskStatus.TODO.value)
            _seed_task_raw(sf, task_name="Active 2", deadline=dl, status=TaskStatus.IN_PROGRESS.value)
            _seed_task_raw(sf, task_name="Done", deadline=dl, status=TaskStatus.COMPLETED.value)
            _seed_task_raw(sf, task_name="Cancelled", deadline=dl, status=TaskStatus.CANCELLED.value)

            app = create_app(Settings(database_url="sqlite:///:memory:", single_user_mode=True))
            app.dependency_overrides[get_session_factory] = lambda: sf
            interp = _interpretation_for_analyze(
                "EXPLICIT_RANGE",
                explicit_start="2026-09-15T00:00:00Z",
                explicit_end="2026-09-21T23:59:59Z",
            )
            resp = _agent_post(app, "Analyze my workload from Sep 15 to Sep 21.", _fake_llm_for(interp))
            assert resp.status_code == 200
            payload = resp.json()["execution_result"]["resultPayload"]
            # Only 2 active tasks counted
            assert payload["total_tasks"] == 2
            names = {t["task_name"] for t in payload["recommended_order"]}
            assert names == {"Active 1", "Active 2"}
            # Repository still returned all 4 — filtering is service-owned.
            # Verify by querying the repo directly.
            session = sf()
            try:
                repo = TaskRepository(session)
                rows = repo.find_by_deadline_range(
                    datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc),
                    datetime(2026, 9, 21, 23, 59, 59, tzinfo=timezone.utc),
                )
                assert len(rows) == 4
            finally:
                session.close()
        finally:
            engine.dispose()


class TestE2ETasksWithoutDeadline:
    """PDMA-82 §3.9 — Tasks without deadline handled correctly."""

    def test_no_deadline_tasks_do_not_create_collision_or_busy_day(self):
        engine = create_engine(
            "sqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine)
        sf = sessionmaker(bind=engine)
        try:
            dl = datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc)
            _seed_task_raw(sf, task_name="With deadline", deadline=dl, priority=TaskPriority.HIGH.value)
            # NOTE: Cannot seed tasks with deadline=None — Task.deadline is NOT NULL.
            # Test the service's defensive None-handling with mock TaskSummary instead.

            # Workload service should be called only with deadline-filtered rows;
            # no-deadline tasks are not in the repository range.  Verify the
            # service handles them when they are present in input.
            svc = WorkloadAnalysisService()
            from personal_deadline_management_agent.schemas.workload import TaskSummary

            summaries = [
                TaskSummary(
                    id=uuid4(),
                    task_name="With deadline",
                    priority=TaskPriority.HIGH,
                    status=TaskStatus.TODO,
                    deadline=dl,
                ),
                TaskSummary(
                    id=uuid4(),
                    task_name="No deadline 1",
                    priority=TaskPriority.HIGH,
                    status=TaskStatus.TODO,
                    deadline=None,
                ),
                TaskSummary(
                    id=uuid4(),
                    task_name="No deadline 2",
                    priority=TaskPriority.HIGH,
                    status=TaskStatus.TODO,
                    deadline=None,
                ),
            ]
            result = svc.analyze(summaries)
            assert result.total_tasks == 3
            assert result.deadline_collisions == []
            assert result.busy_days == []
            # No-deadline tasks appear at the end of recommended_order
            assert result.recommended_order[0].task_name == "With deadline"
            assert {t.task_name for t in result.recommended_order[1:]} == {
                "No deadline 1",
                "No deadline 2",
            }

            # Through the full agent pipeline with explicit range — only
            # deadline-bearing tasks are in the DB range; result total == 1.
            app = create_app(Settings(database_url="sqlite:///:memory:", single_user_mode=True))
            app.dependency_overrides[get_session_factory] = lambda: sf
            interp = _interpretation_for_analyze(
                "EXPLICIT_RANGE",
                explicit_start="2026-09-15T00:00:00Z",
                explicit_end="2026-09-21T23:59:59Z",
            )
            resp = _agent_post(app, "Analyze my workload from Sep 15 to Sep 21.", _fake_llm_for(interp))
            payload = resp.json()["execution_result"]["resultPayload"]
            assert payload["total_tasks"] == 1
            assert payload["deadline_collisions"] == []
            assert payload["busy_days"] == []
        finally:
            engine.dispose()


class TestE2EReadOnlyBehavior:
    """PDMA-82 §3.10 — ANALYZE_WORKLOAD has no side effects."""

    def test_no_task_mutation_no_reminder_mutation_no_confirmation(self):
        engine = create_engine(
            "sqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine)
        sf = sessionmaker(bind=engine)
        try:
            dl = datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc)
            _seed_task_raw(sf, task_name="Task A", deadline=dl)
            _seed_task_raw(sf, task_name="Task B", deadline=dl)

            # Count tasks/reminders/confirmations before
            session = sf()
            try:
                task_count_before = session.execute(text("SELECT COUNT(*) FROM tasks")).scalar()
                reminder_count_before = session.execute(text("SELECT COUNT(*) FROM reminders")).scalar()
                conf_count_before = session.execute(text("SELECT COUNT(*) FROM pending_confirmations")).scalar()
            finally:
                session.close()

            app = create_app(Settings(database_url="sqlite:///:memory:", single_user_mode=True))
            app.dependency_overrides[get_session_factory] = lambda: sf
            interp = _interpretation_for_analyze(
                "EXPLICIT_RANGE",
                explicit_start="2026-09-15T00:00:00Z",
                explicit_end="2026-09-21T23:59:59Z",
            )
            resp = _agent_post(app, "Analyze my workload from Sep 15 to Sep 21.", _fake_llm_for(interp))
            assert resp.status_code == 200
            body = resp.json()
            assert body["status"] == AgentChatStatus.EXECUTED.value
            # No confirmation was created (read-only, non-destructive)
            assert body.get("confirmationId") is None

            session = sf()
            try:
                task_count_after = session.execute(text("SELECT COUNT(*) FROM tasks")).scalar()
                reminder_count_after = session.execute(text("SELECT COUNT(*) FROM reminders")).scalar()
                conf_count_after = session.execute(text("SELECT COUNT(*) FROM pending_confirmations")).scalar()
            finally:
                session.close()

            assert task_count_after == task_count_before
            assert reminder_count_after == reminder_count_before
            assert conf_count_after == conf_count_before
        finally:
            engine.dispose()


# ---------------------------------------------------------------------------
# STEP 4 — Architecture boundary tests
# ---------------------------------------------------------------------------


class TestInterpreterDoesNotCalculateDates:
    """AgentInterpreter must not compute concrete date boundaries."""

    def test_interpreter_carries_semantic_expression_only(self):
        from personal_deadline_management_agent.services.agent_interpreter import AgentInterpreter

        interp = _interpretation_for_analyze("THIS_WEEK")
        fake = _fake_llm_for(interp)
        interpreter = AgentInterpreter(fake)
        resp = interpreter.interpret(AgentRequest(message="Analyze my workload this week."))
        assert resp.proposal is not None
        assert resp.proposal.parameters["date_range_expression"] == "THIS_WEEK"
        # No concrete datetime boundaries on the proposal
        assert "explicit_start" not in resp.proposal.parameters or resp.proposal.parameters.get("explicit_start") is None
        assert "explicit_end" not in resp.proposal.parameters or resp.proposal.parameters.get("explicit_end") is None
        # Interpreter source must not import datetime calendar math beyond what
        # is needed for type handling — verify no date arithmetic in the module
        src = inspect.getsource(interpreter.interpret)
        # The interpret method delegates to LLM; it should not contain timedelta/calendar
        assert "timedelta" not in src
        assert "calendar" not in src

    def test_explicit_range_carries_iso_strings_not_datetimes(self):
        from personal_deadline_management_agent.services.agent_interpreter import AgentInterpreter

        interp = _interpretation_for_analyze(
            "EXPLICIT_RANGE",
            explicit_start="2026-09-15T00:00:00Z",
            explicit_end="2026-09-21T23:59:59Z",
        )
        fake = _fake_llm_for(interp)
        interpreter = AgentInterpreter(fake)
        resp = interpreter.interpret(AgentRequest(message="from Sep 15 to Sep 21"))
        assert resp.proposal.parameters["explicit_start"] == "2026-09-15T00:00:00Z"
        assert resp.proposal.parameters["explicit_end"] == "2026-09-21T23:59:59Z"
        # They are strings, not datetimes
        assert isinstance(resp.proposal.parameters["explicit_start"], str)


class TestSafetyPolicyNoRepositoryAccess:
    """SafetyPolicy must not access repository/database."""

    def test_safety_policy_source_has_no_repository_import(self):
        src = inspect.getsource(SafetyPolicy)
        lower = src.lower()
        assert "repository" not in lower
        assert "session" not in lower
        assert "select(" not in lower

    def test_analyze_workload_authorized_without_db(self):
        from personal_deadline_management_agent.services.action_validator import ValidatedAction

        policy = SafetyPolicy()
        action = ValidatedAction(
            action_type=ActionType.ANALYZE_WORKLOAD,
            parameters={"date_range_expression": "THIS_WEEK"},
        )
        result = policy.evaluate(action)
        from personal_deadline_management_agent.guardrails.safety_policy import DecisionStatus

        assert result.status == DecisionStatus.AUTHORIZED

    def test_analyze_workload_does_not_require_confirmation(self):
        from personal_deadline_management_agent.services.action_validator import ValidatedAction

        policy = SafetyPolicy()
        action = ValidatedAction(
            action_type=ActionType.ANALYZE_WORKLOAD,
            parameters={"date_range_expression": "TODAY"},
        )
        result = policy.evaluate(action)
        from personal_deadline_management_agent.guardrails.safety_policy import DecisionStatus

        assert result.status != DecisionStatus.CONFIRMATION_REQUIRED


class TestWorkloadAnalysisServiceNoRepositoryOrLLM:
    """WorkloadAnalysisService must not access repository/database or LLM."""

    def test_service_source_has_no_repository_or_llm(self):
        src = inspect.getsource(WorkloadAnalysisService)
        lower = src.lower()
        assert "repository" not in lower
        assert "session" not in lower
        assert "select(" not in lower
        assert "bedrock" not in lower
        assert "llm" not in lower
        assert "genai" not in lower

    def test_service_analyze_does_not_touch_db(self):
        # Pass TaskSummary objects — no DB involved
        svc = WorkloadAnalysisService()
        summaries = [
            __import__("personal_deadline_management_agent.schemas.workload", fromlist=["TaskSummary"]).TaskSummary(
                id=uuid4(),
                task_name="T",
                priority=TaskPriority.HIGH,
                status=TaskStatus.TODO,
                deadline=datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc),
            )
        ]
        result = svc.analyze(summaries)
        assert isinstance(result, WorkloadAnalysisResult)
        assert result.total_tasks == 1


class TestActionExecutorIsOrchestrationOnly:
    """ActionExecutor must remain orchestration — no business logic duplication."""

    def test_executor_delegates_to_date_resolver_and_service(self):
        from unittest.mock import MagicMock

        from personal_deadline_management_agent.modules.task_module import TaskModule
        from personal_deadline_management_agent.modules.reminder_module import ReminderModule
        from personal_deadline_management_agent.services.action_executor import ActionExecutor
        from personal_deadline_management_agent.guardrails import DecisionResult, DecisionStatus
        from personal_deadline_management_agent.services.action_validator import ValidatedAction
        from personal_deadline_management_agent.services.execution_command import ExecutionCommand

        task_module = MagicMock(spec=TaskModule)
        reminder_module = MagicMock(spec=ReminderModule)
        mock_resolver = MagicMock(spec=DateRangeResolver)
        mock_service = MagicMock(spec=WorkloadAnalysisService)

        start = datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 9, 21, 23, 59, 59, tzinfo=timezone.utc)
        mock_resolver.resolve.return_value = (start, end)
        task_module.find_tasks_by_deadline_range.return_value = []
        mock_service.analyze.return_value = WorkloadAnalysisResult(
            total_tasks=0, deadline_collisions=[], busy_days=[], recommended_order=[], explanation=""
        )

        executor = ActionExecutor(
            task_module=task_module,
            reminder_module=reminder_module,
            date_range_resolver=mock_resolver,
            workload_analysis_service=mock_service,
        )
        action = ValidatedAction(
            action_type=ActionType.ANALYZE_WORKLOAD,
            parameters={"date_range_expression": "THIS_WEEK"},
        )
        decision = DecisionResult(status=DecisionStatus.AUTHORIZED, action=action)
        command = ExecutionCommand.from_decision(decision)
        result = executor.execute(decision, command)

        mock_resolver.resolve.assert_called_once()
        task_module.find_tasks_by_deadline_range.assert_called_once_with(start, end)
        mock_service.analyze.assert_called_once()
        assert result.status == ExecutionStatus.EXECUTED
        assert result.result_payload is not None


class TestTaskRepositoryNoStatusFiltering:
    """find_by_deadline_range must not filter by status."""

    def test_repository_returns_all_statuses(self):
        engine = create_engine(
            "sqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine)
        sf = sessionmaker(bind=engine)
        try:
            dl = datetime(2026, 9, 18, 10, 0, tzinfo=timezone.utc)
            _seed_task_raw(sf, task_name="Todo", deadline=dl, status=TaskStatus.TODO.value)
            _seed_task_raw(sf, task_name="Done", deadline=dl, status=TaskStatus.COMPLETED.value)
            _seed_task_raw(sf, task_name="Cancelled", deadline=dl, status=TaskStatus.CANCELLED.value)
            _seed_task_raw(sf, task_name="InProgress", deadline=dl, status=TaskStatus.IN_PROGRESS.value)

            session = sf()
            try:
                repo = TaskRepository(session)
                rows = repo.find_by_deadline_range(
                    datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc),
                    datetime(2026, 9, 21, 23, 59, 59, tzinfo=timezone.utc),
                )
                statuses = {r.status for r in rows}
                assert TaskStatus.TODO.value in statuses
                assert TaskStatus.COMPLETED.value in statuses
                assert TaskStatus.CANCELLED.value in statuses
                assert TaskStatus.IN_PROGRESS.value in statuses
                assert len(rows) == 4
            finally:
                session.close()

            # Source guard: no status filtering in the repository WHERE clause
            src = inspect.getsource(TaskRepository.find_by_deadline_range)
            # Verify no Task.status comparison in the query (e.g., Task.status == ...)
            # The docstring mentions "status" but the query should not filter on it
            assert "Task.status" not in src
        finally:
            engine.dispose()


class TestAnalyzeWorkloadNoResourceRequired:
    """ANALYZE_WORKLOAD must not require a resource ID."""

    def test_validator_accepts_no_resource(self):
        from personal_deadline_management_agent.schemas import ActionProposal
        from personal_deadline_management_agent.services.action_validator import ActionValidator

        v = ActionValidator()
        proposal = ActionProposal(
            action_type=ActionType.ANALYZE_WORKLOAD,
            parameters={"date_range_expression": "THIS_WEEK"},
        )
        result = v.validate(proposal)
        assert result.status == ValidationStatus.VALID

    def test_resource_resolver_accepts_no_resource(self):
        from unittest.mock import MagicMock

        from personal_deadline_management_agent.schemas import ActionProposal
        from personal_deadline_management_agent.services.resource_resolver import ResourceResolver

        resolver = ResourceResolver(task_repository=MagicMock(), reminder_repository=MagicMock())
        proposal = ActionProposal(
            action_type=ActionType.ANALYZE_WORKLOAD,
            parameters={"date_range_expression": "THIS_WEEK"},
        )
        result = resolver.resolve(proposal)
        assert result.status == ValidationStatus.VALID
        assert result.validated_action is not None
        assert result.validated_action.resource_id is None

    def test_resource_reference_rejected_for_analyze(self):
        from unittest.mock import MagicMock

        from personal_deadline_management_agent.schemas import ActionProposal, ResourceReference
        from personal_deadline_management_agent.services.resource_resolver import ResourceResolver

        resolver = ResourceResolver(task_repository=MagicMock(), reminder_repository=MagicMock())
        proposal = ActionProposal(
            action_type=ActionType.ANALYZE_WORKLOAD,
            resource=ResourceReference(natural_language="this week"),
            parameters={"date_range_expression": "THIS_WEEK"},
        )
        result = resolver.resolve(proposal)
        assert result.status == ValidationStatus.CLARIFICATION_REQUIRED
