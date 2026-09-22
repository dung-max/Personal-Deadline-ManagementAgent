"""Tests for the Agent HTTP handler (full pipeline endpoint).

Unit tests only — every pipeline component below the handler is mocked;
no real database, LLM, or AWS calls.  Covers every DecisionResult status
branch, interpreter/validator/resolver branches, generic error handling,
execution failure, architecture guards, and request validation.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from personal_deadline_management_agent.config import Settings
from personal_deadline_management_agent.dependencies import (
    get_action_executor,
    get_action_validator,
    get_agent_interpreter,
    get_agent_response_generator,
    get_decision_service,
    get_pending_confirmation_module,
    get_resource_resolver,
)
from personal_deadline_management_agent.guardrails import DecisionResult, DecisionStatus
from personal_deadline_management_agent.main import create_app
from personal_deadline_management_agent.models import PendingConfirmationStatus
from personal_deadline_management_agent.modules.pending_confirmation_module import (
    ConfirmOutcome,
    ConfirmResult,
)
from personal_deadline_management_agent.schemas import (
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
from personal_deadline_management_agent.services.authorization_service import (
    AuthorizationContext,
)
from personal_deadline_management_agent.services.execution_command import ExecutionCommand
from personal_deadline_management_agent.services.execution_result import (
    ExecutionErrorCode,
    ExecutionResult,
    ExecutionStatus,
)
from personal_deadline_management_agent.services.resource_resolver import ResolutionResult


# --- Fixtures and helpers ----------------------------------------------------

TASK_ID = uuid4()

_CREATE_TASK_PARAMS = {"taskName": "Report", "deadline": "2026-10-01T12:00:00Z"}


def _proposal() -> ActionProposal:
    return ActionProposal(
        action_type=ActionType.CREATE_TASK,
        parameters=_CREATE_TASK_PARAMS,
    )


def _validated_action() -> ValidatedAction:
    return ValidatedAction(
        action_type=ActionType.CREATE_TASK,
        parameters=_CREATE_TASK_PARAMS,
    )


@pytest.fixture
def pipeline_mocks():
    from unittest.mock import MagicMock

    return {
        "interpreter": MagicMock(),
        "validator": MagicMock(),
        "resolver": MagicMock(),
        "decision_service": MagicMock(),
        "executor": MagicMock(),
        "confirmation_module": MagicMock(),
        "response_generator": MagicMock(),
    }


@pytest.fixture
def client(pipeline_mocks):
    app = create_app(Settings(database_url="sqlite:///:memory:"))
    app.dependency_overrides[get_agent_interpreter] = (
        lambda: pipeline_mocks["interpreter"]
    )
    app.dependency_overrides[get_action_validator] = (
        lambda: pipeline_mocks["validator"]
    )
    app.dependency_overrides[get_resource_resolver] = (
        lambda: pipeline_mocks["resolver"]
    )
    app.dependency_overrides[get_decision_service] = (
        lambda: pipeline_mocks["decision_service"]
    )
    app.dependency_overrides[get_action_executor] = (
        lambda: pipeline_mocks["executor"]
    )
    app.dependency_overrides[get_pending_confirmation_module] = (
        lambda: pipeline_mocks["confirmation_module"]
    )
    app.dependency_overrides[get_agent_response_generator] = (
        lambda: pipeline_mocks["response_generator"]
    )
    with TestClient(app) as test_client:
        yield test_client


def _happy_path(pipeline_mocks):
    """Configure every mock for an AUTHORIZED → EXECUTED flow."""
    pipeline_mocks["interpreter"].interpret.return_value = AgentResponse(
        response_type=ResponseType.ACTION_PROPOSED,
        message="",
        proposal=_proposal(),
    )
    pipeline_mocks["validator"].validate.return_value = ValidationResult(
        status=ValidationStatus.VALID,
    )
    pipeline_mocks["resolver"].resolve.return_value = ResolutionResult(
        status=ValidationStatus.VALID,
        validated_action=_validated_action(),
    )
    pipeline_mocks["decision_service"].decide.return_value = DecisionResult(
        status=DecisionStatus.AUTHORIZED,
        reason="Action is permitted.",
        action=_validated_action(),
    )
    pipeline_mocks["executor"].execute.return_value = ExecutionResult(
        status=ExecutionStatus.EXECUTED,
        action_type=ActionType.CREATE_TASK,
        message="Action executed successfully.",
        result_id=uuid4(),
        result_name="Report",
    )


# --- a. Authorized → executed -----------------------------------------------


def test_authorized_flow_executes_and_returns_result(client, pipeline_mocks):
    _happy_path(pipeline_mocks)
    user_id = uuid4()

    response = client.post(
        "/api/v1/agent/chat",
        json={"message": "create a report task", "userId": str(user_id)},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == AgentChatStatus.EXECUTED.value
    assert body["message"] == "Action executed successfully."
    assert body["execution_result"]["status"] == "EXECUTED"
    assert body["execution_result"]["actionType"] == "CREATE_TASK"
    assert body["execution_result"]["resultName"] == "Report"

    context = pipeline_mocks["decision_service"].decide.call_args.args[1]
    assert isinstance(context, AuthorizationContext)
    assert context.actor_id == user_id
    pipeline_mocks["executor"].execute.assert_called_once()


def test_authorized_flow_builds_command_from_decision(client, pipeline_mocks):
    _happy_path(pipeline_mocks)

    client.post("/api/v1/agent/chat", json={"message": "create a task"})

    args = pipeline_mocks["executor"].execute.call_args.args
    decision, command = args[0], args[1]
    assert isinstance(command, ExecutionCommand)
    assert command.action_type == ActionType.CREATE_TASK
    assert command.action_type == decision.action.action_type


def test_missing_user_id_passes_none_context(client, pipeline_mocks):
    _happy_path(pipeline_mocks)

    response = client.post("/api/v1/agent/chat", json={"message": "create a task"})

    assert response.status_code == 200
    context = pipeline_mocks["decision_service"].decide.call_args.args[1]
    assert context is None


# --- b/c. Interpreter non-action branches -----------------------------------


@pytest.mark.parametrize(
    ("response_type", "expected_status"),
    [
        (ResponseType.CLARIFICATION_REQUIRED, AgentChatStatus.CLARIFICATION_REQUIRED),
        (ResponseType.REJECTED, AgentChatStatus.REJECTED),
        (ResponseType.CONVERSATION, AgentChatStatus.CONVERSATION),
    ],
)
def test_interpreter_non_action_branches_stop_pipeline(
    client, pipeline_mocks, response_type, expected_status
):
    pipeline_mocks["interpreter"].interpret.return_value = AgentResponse(
        response_type=response_type,
        message="Need more information.",
        proposal=None,
    )

    response = client.post("/api/v1/agent/chat", json={"message": "hello"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == expected_status.value
    assert body["message"] == "Need more information."
    assert body["execution_result"] is None
    pipeline_mocks["validator"].validate.assert_not_called()
    pipeline_mocks["executor"].execute.assert_not_called()


# --- b. Validation branches --------------------------------------------------


@pytest.mark.parametrize(
    ("validation_status", "expected_status"),
    [
        (ValidationStatus.CLARIFICATION_REQUIRED, AgentChatStatus.CLARIFICATION_REQUIRED),
        (ValidationStatus.REJECTED, AgentChatStatus.REJECTED),
    ],
)
def test_validation_branches_stop_pipeline(
    client, pipeline_mocks, validation_status, expected_status
):
    pipeline_mocks["interpreter"].interpret.return_value = AgentResponse(
        response_type=ResponseType.ACTION_PROPOSED,
        message="",
        proposal=_proposal(),
    )
    pipeline_mocks["validator"].validate.return_value = ValidationResult(
        status=validation_status,
        message="Missing required parameter(s): deadline.",
    )

    response = client.post("/api/v1/agent/chat", json={"message": "create a task"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == expected_status.value
    assert body["message"] == "Missing required parameter(s): deadline."
    pipeline_mocks["resolver"].resolve.assert_not_called()
    pipeline_mocks["executor"].execute.assert_not_called()


# --- c. Resolution branch ----------------------------------------------------


def test_resolver_clarification_stops_pipeline(client, pipeline_mocks):
    pipeline_mocks["interpreter"].interpret.return_value = AgentResponse(
        response_type=ResponseType.ACTION_PROPOSED,
        message="",
        proposal=_proposal(),
    )
    pipeline_mocks["validator"].validate.return_value = ValidationResult(
        status=ValidationStatus.VALID,
    )
    pipeline_mocks["resolver"].resolve.return_value = ResolutionResult(
        status=ValidationStatus.CLARIFICATION_REQUIRED,
        message="No resource matches that reference.",
    )

    response = client.post(
        "/api/v1/agent/chat", json={"message": "update my report task"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == AgentChatStatus.CLARIFICATION_REQUIRED.value
    assert body["message"] == "No resource matches that reference."
    pipeline_mocks["decision_service"].decide.assert_not_called()
    pipeline_mocks["executor"].execute.assert_not_called()


# --- d. Decision branches (non-executed) -------------------------------------


@pytest.mark.parametrize(
    ("decision_status", "expected_status"),
    [
        (DecisionStatus.DENIED, AgentChatStatus.DENIED),
        (DecisionStatus.NOT_CONFIGURED, AgentChatStatus.NOT_CONFIGURED),
    ],
)
def test_non_executed_decision_branches_do_not_execute(
    client, pipeline_mocks, decision_status, expected_status
):
    _happy_path(pipeline_mocks)
    pipeline_mocks["decision_service"].decide.return_value = DecisionResult(
        status=decision_status,
        reason="Reason for the user.",
        action=_validated_action(),
    )

    response = client.post("/api/v1/agent/chat", json={"message": "delete a task"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == expected_status.value
    assert body["message"] == "Reason for the user."
    assert body["execution_result"] is None
    pipeline_mocks["executor"].execute.assert_not_called()


# --- Execution failure → ERROR with result -----------------------------------


def test_execution_failure_returns_error_with_result(client, pipeline_mocks):
    _happy_path(pipeline_mocks)
    pipeline_mocks["executor"].execute.return_value = ExecutionResult(
        status=ExecutionStatus.EXECUTION_FAILED,
        action_type=ActionType.CREATE_TASK,
        message="The application operation failed.",
        error_code=ExecutionErrorCode.INFRASTRUCTURE,
    )

    response = client.post("/api/v1/agent/chat", json={"message": "create a task"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == AgentChatStatus.ERROR.value
    assert body["message"] == "The application operation failed."
    assert body["execution_result"]["status"] == "EXECUTION_FAILED"
    assert body["execution_result"]["errorCode"] == "INFRASTRUCTURE"


def test_invalid_input_returns_invalid_input_status(client, pipeline_mocks):
    _happy_path(pipeline_mocks)
    pipeline_mocks["executor"].execute.return_value = ExecutionResult(
        status=ExecutionStatus.EXECUTION_FAILED,
        action_type=ActionType.CREATE_TASK,
        message="The application operation failed.",
        error_code=ExecutionErrorCode.INVALID_INPUT,
    )

    response = client.post("/api/v1/agent/chat", json={"message": "create a task"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == AgentChatStatus.INVALID_INPUT.value
    assert body["execution_result"]["errorCode"] == "INVALID_INPUT"


# --- Generic error handling ---------------------------------------------------


def test_pipeline_exception_returns_generic_error(
    client, pipeline_mocks, caplog
):
    pipeline_mocks["interpreter"].interpret.side_effect = RuntimeError(
        "connection to server at db.internal:5432 failed: "
        "password authentication failed for user 'app'"
    )

    with caplog.at_level(
        logging.ERROR,
        logger="personal_deadline_management_agent.handlers.agent",
    ):
        response = client.post(
            "/api/v1/agent/chat", json={"message": "create a task"}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == AgentChatStatus.ERROR.value
    assert body["message"] == "Something went wrong. Please try again."
    assert body["execution_result"] is None
    # Internal details must not leak.
    assert "db.internal" not in body["message"]
    assert "password" not in body["message"]
    assert "Traceback" not in body["message"]
    # Server-side log contains full context.
    assert "Agent chat failed" in caplog.text
    assert "db.internal" in caplog.text


# --- Request validation ------------------------------------------------------


def test_empty_message_is_rejected(client):
    response = client.post("/api/v1/agent/chat", json={"message": ""})
    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "VALIDATION_ERROR"


def test_missing_message_is_rejected(client):
    response = client.post("/api/v1/agent/chat", json={})
    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "VALIDATION_ERROR"


# --- CONFIRMATION_REQUIRED branch --------------------------------------------


def test_confirmation_required_creates_pending_and_returns_reference(
    client, pipeline_mocks
):
    confirmation_id = uuid4()
    pipeline_mocks["interpreter"].interpret.return_value = AgentResponse(
        response_type=ResponseType.ACTION_PROPOSED,
        message="",
        proposal=ActionProposal(
            action_type=ActionType.DELETE_TASK,
            resource={"id": str(uuid4())},
        ),
    )
    pipeline_mocks["validator"].validate.return_value = ValidationResult(
        status=ValidationStatus.VALID,
    )
    pipeline_mocks["resolver"].resolve.return_value = ResolutionResult(
        status=ValidationStatus.VALID,
        validated_action=ValidatedAction(
            action_type=ActionType.DELETE_TASK,
            resource_id=uuid4(),
        ),
    )
    pipeline_mocks["decision_service"].decide.return_value = DecisionResult(
        status=DecisionStatus.CONFIRMATION_REQUIRED,
        reason="Destructive action requires confirmation before execution.",
        action=ValidatedAction(
            action_type=ActionType.DELETE_TASK, resource_id=uuid4()
        ),
    )
    pipeline_mocks["confirmation_module"].create.return_value = SimpleNamespace(
        id=confirmation_id
    )

    response = client.post("/api/v1/agent/chat", json={"message": "delete a task"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == AgentChatStatus.CONFIRMATION_REQUIRED.value
    assert body["confirmationId"] == str(confirmation_id)
    assert "reference" in body["message"]
    assert body["execution_result"] is None
    # The command was stored
    create_call = pipeline_mocks["confirmation_module"].create.call_args
    assert isinstance(create_call.kwargs["command"], ExecutionCommand)
    assert create_call.kwargs["command"].action_type == ActionType.DELETE_TASK
    pipeline_mocks["executor"].execute.assert_not_called()


# --- Confirm flow: success ---------------------------------------------------


def test_confirm_with_reference_executes_stored_command(client, pipeline_mocks):
    confirmation_id = uuid4()
    stored_command = ExecutionCommand(
        action_type=ActionType.DELETE_TASK,
        resource_id=uuid4(),
        parameters={},
    )
    pipeline_mocks["confirmation_module"].confirm.return_value = ConfirmResult(
        outcome=ConfirmOutcome.CONFIRMED,
        confirmation=SimpleNamespace(
            id=confirmation_id,
            status=PendingConfirmationStatus.CONFIRMED.value,
            execution_command=stored_command.model_dump_json(),
        ),
    )
    pipeline_mocks["executor"].execute.return_value = ExecutionResult(
        status=ExecutionStatus.EXECUTED,
        action_type=ActionType.DELETE_TASK,
        message="Action executed successfully.",
    )
    pipeline_mocks["interpreter"].interpret.return_value = AgentResponse(
        response_type=ResponseType.CONFIRMATION,
        message="",
        proposal=None,
        confirmation_id=confirmation_id,
    )

    response = client.post("/api/v1/agent/chat", json={"message": "yes"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == AgentChatStatus.EXECUTED.value
    assert body["confirmationId"] == str(confirmation_id)
    # Executor called with reconstructed AUTHORIZED decision + stored command
    args = pipeline_mocks["executor"].execute.call_args.args
    decision, executed_command = args[0], args[1]
    assert decision.status == DecisionStatus.AUTHORIZED
    assert executed_command.action_type == ActionType.DELETE_TASK
    # Pipeline NOT re-run
    pipeline_mocks["validator"].validate.assert_not_called()
    pipeline_mocks["decision_service"].decide.assert_not_called()


def test_confirm_without_reference_uses_latest_pending(client, pipeline_mocks):
    confirmation_id = uuid4()
    stored_command = ExecutionCommand(
        action_type=ActionType.DELETE_TASK,
        resource_id=uuid4(),
        parameters={},
    )
    pipeline_mocks["confirmation_module"].get_latest_pending.return_value = (
        SimpleNamespace(id=confirmation_id)
    )
    pipeline_mocks["confirmation_module"].confirm.return_value = ConfirmResult(
        outcome=ConfirmOutcome.CONFIRMED,
        confirmation=SimpleNamespace(
            id=confirmation_id,
            status=PendingConfirmationStatus.CONFIRMED.value,
            execution_command=stored_command.model_dump_json(),
        ),
    )
    pipeline_mocks["executor"].execute.return_value = ExecutionResult(
        status=ExecutionStatus.EXECUTED,
        action_type=ActionType.DELETE_TASK,
        message="Action executed successfully.",
    )
    pipeline_mocks["interpreter"].interpret.return_value = AgentResponse(
        response_type=ResponseType.CONFIRMATION,
        message="",
        proposal=None,
        confirmation_id=None,
    )

    response = client.post("/api/v1/agent/chat", json={"message": "yes"})

    assert response.status_code == 200
    assert response.json()["status"] == AgentChatStatus.EXECUTED.value
    pipeline_mocks["confirmation_module"].get_latest_pending.assert_called_once()
    pipeline_mocks["confirmation_module"].confirm.assert_called_once_with(
        confirmation_id, None
    )


# --- Confirm flow: expired ---------------------------------------------------


def test_confirm_expired_returns_expired(client, pipeline_mocks):
    confirmation_id = uuid4()
    pipeline_mocks["confirmation_module"].confirm.return_value = ConfirmResult(
        outcome=ConfirmOutcome.EXPIRED,
        confirmation=SimpleNamespace(
            id=confirmation_id,
            status=PendingConfirmationStatus.EXPIRED.value,
            execution_command="{}",
        ),
    )
    pipeline_mocks["interpreter"].interpret.return_value = AgentResponse(
        response_type=ResponseType.CONFIRMATION,
        message="",
        proposal=None,
        confirmation_id=confirmation_id,
    )

    response = client.post("/api/v1/agent/chat", json={"message": "yes"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == AgentChatStatus.EXPIRED.value
    assert "expired" in body["message"].lower()
    assert body["execution_result"] is None
    assert body["confirmationId"] == str(confirmation_id)
    pipeline_mocks["executor"].execute.assert_not_called()


# --- Confirm flow: wrong user -------------------------------------------------


def test_confirm_wrong_user_returns_clarification(client, pipeline_mocks):
    confirmation_id = uuid4()
    pipeline_mocks["confirmation_module"].confirm.return_value = ConfirmResult(
        outcome=ConfirmOutcome.NOT_FOUND
    )
    pipeline_mocks["interpreter"].interpret.return_value = AgentResponse(
        response_type=ResponseType.CONFIRMATION,
        message="",
        proposal=None,
        confirmation_id=confirmation_id,
    )

    user_id = uuid4()
    response = client.post(
        "/api/v1/agent/chat",
        json={"message": "yes", "userId": str(user_id)},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == AgentChatStatus.CLARIFICATION_REQUIRED.value
    assert "No pending confirmation" in body["message"]
    pipeline_mocks["executor"].execute.assert_not_called()


# --- Confirm flow: no pending at all -----------------------------------------


def test_confirm_without_pending_returns_clarification(client, pipeline_mocks):
    pipeline_mocks["confirmation_module"].get_latest_pending.return_value = None
    pipeline_mocks["interpreter"].interpret.return_value = AgentResponse(
        response_type=ResponseType.CONFIRMATION,
        message="",
        proposal=None,
        confirmation_id=None,
    )

    response = client.post("/api/v1/agent/chat", json={"message": "yes"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == AgentChatStatus.CLARIFICATION_REQUIRED.value
    assert "pending action" in body["message"].lower()
    pipeline_mocks["executor"].execute.assert_not_called()


# --- Confirm flow: execution failure -----------------------------------------


def test_confirm_execution_failure_returns_error(client, pipeline_mocks):
    confirmation_id = uuid4()
    stored_command = ExecutionCommand(
        action_type=ActionType.DELETE_TASK,
        resource_id=uuid4(),
        parameters={},
    )
    pipeline_mocks["confirmation_module"].confirm.return_value = ConfirmResult(
        outcome=ConfirmOutcome.CONFIRMED,
        confirmation=SimpleNamespace(
            id=confirmation_id,
            status=PendingConfirmationStatus.CONFIRMED.value,
            execution_command=stored_command.model_dump_json(),
        ),
    )
    pipeline_mocks["executor"].execute.return_value = ExecutionResult(
        status=ExecutionStatus.EXECUTION_FAILED,
        action_type=ActionType.DELETE_TASK,
        message="The application operation failed.",
        error_code=ExecutionErrorCode.INFRASTRUCTURE,
    )
    pipeline_mocks["interpreter"].interpret.return_value = AgentResponse(
        response_type=ResponseType.CONFIRMATION,
        message="",
        proposal=None,
        confirmation_id=confirmation_id,
    )

    response = client.post("/api/v1/agent/chat", json={"message": "yes"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == AgentChatStatus.ERROR.value
    assert body["execution_result"]["errorCode"] == "INFRASTRUCTURE"
    assert body["confirmationId"] == str(confirmation_id)


# --- Confirm flow: invalid reference from interpreter -----------------------


def test_confirm_invalid_reference_returns_clarification(client, pipeline_mocks):
    pipeline_mocks["interpreter"].interpret.return_value = AgentResponse(
        response_type=ResponseType.CONFIRMATION,
        message="",
        proposal=None,
        confirmation_id=uuid4(),
    )
    pipeline_mocks["confirmation_module"].confirm.return_value = ConfirmResult(
        outcome=ConfirmOutcome.NOT_FOUND
    )

    response = client.post("/api/v1/agent/chat", json={"message": "yes"})

    assert response.status_code == 200
    assert response.json()["status"] == AgentChatStatus.CLARIFICATION_REQUIRED.value


# --- Cleanup runs on each request --------------------------------------------


def test_cleanup_expired_runs_on_each_request(client, pipeline_mocks):
    _happy_path(pipeline_mocks)

    client.post("/api/v1/agent/chat", json={"message": "create a task"})

    pipeline_mocks["confirmation_module"].cleanup_expired.assert_called_once()


# --- Interpreter confirm-intent recognition ----------------------------------


def test_interpreter_confirm_response_routes_to_confirm_branch(
    client, pipeline_mocks
):
    """When the interpreter returns CONFIRMATION, the pipeline skips
    validation/resolution/decision and goes straight to the confirm handler."""
    pipeline_mocks["confirmation_module"].get_latest_pending.return_value = None
    pipeline_mocks["interpreter"].interpret.return_value = AgentResponse(
        response_type=ResponseType.CONFIRMATION,
        message="",
        proposal=None,
        confirmation_id=None,
    )

    client.post("/api/v1/agent/chat", json={"message": "yes"})

    pipeline_mocks["validator"].validate.assert_not_called()
    pipeline_mocks["resolver"].resolve.assert_not_called()
    pipeline_mocks["decision_service"].decide.assert_not_called()


# --- Architecture guard: no commit/rollback ---------------------------------


def test_handler_does_not_commit_or_rollback(client, pipeline_mocks, monkeypatch):
    from personal_deadline_management_agent.uow import UnitOfWork

    def explode(*args, **kwargs):
        raise AssertionError("handler must not commit or rollback")

    monkeypatch.setattr(UnitOfWork, "commit", explode)
    monkeypatch.setattr(UnitOfWork, "rollback", explode)
    _happy_path(pipeline_mocks)

    response = client.post("/api/v1/agent/chat", json={"message": "create a task"})

    assert response.status_code == 200
    assert response.json()["status"] == AgentChatStatus.EXECUTED.value


# --- Double-confirm guard (real module + SQLite) -----------------------------


def test_confirm_twice_does_not_execute_action_twice(client, pipeline_mocks):
    """Two 'yes' requests for the same confirmation must execute only once.

    Uses a real PendingConfirmationModule backed by in-memory SQLite to
    exercise the actual conditional-UPDATE transition across requests —
    not just a mocked return value.
    """
    from uuid import UUID

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from genai_core.genai_shared.database import Base
    from personal_deadline_management_agent.modules.pending_confirmation_module import (
        ConfirmOutcome,
        PendingConfirmationModule,
    )
    from personal_deadline_management_agent.uow import UnitOfWork

    # StaticPool: TestClient serves requests on a worker thread, and a plain
    # ":memory:" engine gives each connection its own database.  Sharing one
    # connection keeps the tables visible across threads.
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    real_module = PendingConfirmationModule(UnitOfWork(session))
    client.app.dependency_overrides[get_pending_confirmation_module] = (
        lambda: real_module
    )

    target_id = uuid4()

    # --- Request 1: DELETE_TASK -> CONFIRMATION_REQUIRED -> pending created ---
    pipeline_mocks["interpreter"].interpret.return_value = AgentResponse(
        response_type=ResponseType.ACTION_PROPOSED,
        message="",
        proposal=ActionProposal(
            action_type=ActionType.DELETE_TASK,
            resource={"id": str(target_id)},
        ),
    )
    pipeline_mocks["validator"].validate.return_value = ValidationResult(
        status=ValidationStatus.VALID,
    )
    pipeline_mocks["resolver"].resolve.return_value = ResolutionResult(
        status=ValidationStatus.VALID,
        validated_action=ValidatedAction(
            action_type=ActionType.DELETE_TASK,
            resource_id=target_id,
        ),
    )
    pipeline_mocks["decision_service"].decide.return_value = DecisionResult(
        status=DecisionStatus.CONFIRMATION_REQUIRED,
        reason="Destructive action requires confirmation.",
        action=ValidatedAction(
            action_type=ActionType.DELETE_TASK, resource_id=target_id
        ),
    )

    response1 = client.post(
        "/api/v1/agent/chat", json={"message": "delete a task"}
    )
    assert response1.status_code == 200
    body1 = response1.json()
    assert body1["status"] == AgentChatStatus.CONFIRMATION_REQUIRED.value
    confirmation_id = body1["confirmationId"]
    assert confirmation_id is not None

    # --- Request 2: "yes" -> confirm -> execute once -------------------------
    pipeline_mocks["interpreter"].interpret.return_value = AgentResponse(
        response_type=ResponseType.CONFIRMATION,
        message="",
        proposal=None,
        confirmation_id=UUID(confirmation_id),
    )
    pipeline_mocks["executor"].execute.return_value = ExecutionResult(
        status=ExecutionStatus.EXECUTED,
        action_type=ActionType.DELETE_TASK,
        message="Action executed successfully.",
    )

    response2 = client.post("/api/v1/agent/chat", json={"message": "yes"})
    assert response2.status_code == 200
    body2 = response2.json()
    assert body2["status"] == AgentChatStatus.EXECUTED.value
    assert pipeline_mocks["executor"].execute.call_count == 1

    # --- Request 3: "yes" again -> ALREADY_CONFIRMED, no re-execution --------
    response3 = client.post("/api/v1/agent/chat", json={"message": "yes"})
    assert response3.status_code == 200
    body3 = response3.json()
    assert body3["status"] == AgentChatStatus.CLARIFICATION_REQUIRED.value
    assert "already been processed" in body3["message"].lower()
    assert pipeline_mocks["executor"].execute.call_count == 1  # unchanged

    session.close()
    engine.dispose()


# --- Race condition: conditional UPDATE ensures exactly one winner -----------


def test_confirm_already_confirmed_returns_distinct_message(client, pipeline_mocks):
    """When confirm() returns ALREADY_CONFIRMED the handler must NOT re-execute
    and must return a distinct 'already processed' message."""
    confirmation_id = uuid4()
    pipeline_mocks["confirmation_module"].confirm.return_value = ConfirmResult(
        outcome=ConfirmOutcome.ALREADY_CONFIRMED,
        confirmation=SimpleNamespace(
            id=confirmation_id,
            status=PendingConfirmationStatus.CONFIRMED.value,
            execution_command="{}",
        ),
    )
    pipeline_mocks["interpreter"].interpret.return_value = AgentResponse(
        response_type=ResponseType.CONFIRMATION,
        message="",
        proposal=None,
        confirmation_id=confirmation_id,
    )

    response = client.post("/api/v1/agent/chat", json={"message": "yes"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == AgentChatStatus.CLARIFICATION_REQUIRED.value
    assert "already been processed" in body["message"].lower()
    assert body["confirmationId"] == str(confirmation_id)
    pipeline_mocks["executor"].execute.assert_not_called()


def test_confirm_race_condition_only_one_wins(tmp_path):
    """Two sessions that both observe PENDING — only one confirm() wins.

    Simulates the TOCTOU race window: both sessions load the row as
    PENDING before either writes.  The conditional UPDATE (WHERE
    status='PENDING') guarantees the second writer matches 0 rows and
    returns ALREADY_CONFIRMED instead of re-executing.
    """
    from uuid import uuid4 as _uuid4

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from genai_core.genai_shared.database import Base
    from personal_deadline_management_agent.modules.pending_confirmation_module import (
        PendingConfirmationModule,
    )
    from personal_deadline_management_agent.uow import UnitOfWork

    def _seed_command():
        return ExecutionCommand(
            action_type=ActionType.DELETE_TASK,
            resource_id=_uuid4(),
            parameters={},
        )

    db_path = tmp_path / "race.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    # Seed one PENDING confirmation.
    user_id = uuid4()
    with Session() as seed_session:
        seed_module = PendingConfirmationModule(UnitOfWork(seed_session))
        original = seed_module.create(
            user_id=user_id,
            command=_seed_command(),
            ttl_seconds=300,
        )
        confirmation_id = original.id

    s1 = Session()
    s2 = Session()
    m1 = PendingConfirmationModule(UnitOfWork(s1))
    m2 = PendingConfirmationModule(UnitOfWork(s2))

    # Both sessions observe the row as PENDING (the race window).
    assert m1.get_latest_pending(user_id).status == PendingConfirmationStatus.PENDING.value
    assert m2.get_latest_pending(user_id).status == PendingConfirmationStatus.PENDING.value

    # Both attempt the conditional update.
    r1 = m1.confirm(confirmation_id, user_id)
    r2 = m2.confirm(confirmation_id, user_id)

    s1.close()
    s2.close()
    engine.dispose()

    outcomes = {r1.outcome, r2.outcome}
    assert ConfirmOutcome.CONFIRMED in outcomes, f"Expected CONFIRMED, got {outcomes}"
    assert ConfirmOutcome.ALREADY_CONFIRMED in outcomes, (
        f"Expected ALREADY_CONFIRMED, got {outcomes}"
    )
    assert r1.outcome != r2.outcome, "Both callers must not get the same outcome"


# --- Agent path enforces the deadline business rule (real TaskModule) --------


def test_agent_past_deadline_returns_invalid_input_without_mutation():
    """The Agent path must enforce the deadline rule through the real
    TaskModule — a past deadline yields INVALID_INPUT and no task is created."""
    import os
    from unittest.mock import MagicMock

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from genai_core.genai_shared.database import Base
    from personal_deadline_management_agent.dependencies import (
        get_llm,
        get_session_factory,
    )

    # Set required env var to prevent Bedrock initialization errors
    os.environ.setdefault("BEDROCK_MODEL_ID", "test-model")

    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    sf = sessionmaker(bind=engine)

    app = create_app(Settings(database_url="sqlite:///:memory:"))
    app.dependency_overrides[get_session_factory] = lambda: sf

    # Mock LLM to prevent Bedrock initialization
    # The handler now has two LLM consumers: interpreter and response generator
    mock_llm = MagicMock()
    app.dependency_overrides[get_llm] = lambda: mock_llm

    interpreter = MagicMock()
    validator = MagicMock()
    resolver = MagicMock()
    decision_service = MagicMock()
    response_generator = MagicMock()

    params = {"taskName": "Past Task", "deadline": "2020-01-01T00:00:00Z"}
    interpreter.interpret.return_value = AgentResponse(
        response_type=ResponseType.ACTION_PROPOSED,
        message="",
        proposal=ActionProposal(action_type=ActionType.CREATE_TASK, parameters=params),
    )
    validator.validate.return_value = ValidationResult(status=ValidationStatus.VALID)
    resolver.resolve.return_value = ResolutionResult(
        status=ValidationStatus.VALID,
        validated_action=ValidatedAction(
            action_type=ActionType.CREATE_TASK, parameters=params
        ),
    )
    decision_service.decide.return_value = DecisionResult(
        status=DecisionStatus.AUTHORIZED,
        reason="Action is permitted.",
        action=ValidatedAction(action_type=ActionType.CREATE_TASK, parameters=params),
    )

    app.dependency_overrides[get_agent_interpreter] = lambda: interpreter
    app.dependency_overrides[get_action_validator] = lambda: validator
    app.dependency_overrides[get_resource_resolver] = lambda: resolver
    app.dependency_overrides[get_decision_service] = lambda: decision_service
    app.dependency_overrides[get_agent_response_generator] = lambda: response_generator

    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/v1/agent/chat", json={"message": "create a task"}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == AgentChatStatus.INVALID_INPUT.value
    assert body["execution_result"]["errorCode"] == "INVALID_INPUT"

    # No task must have been created (no partial creation on business failure).
    from personal_deadline_management_agent.models import Task

    with engine.connect() as conn:
        rows = conn.execute(Task.__table__.select()).fetchall()
    assert rows == []

    engine.dispose()


# --- PDMA-83: Response Generator Integration Tests ---------------------------


def test_analyze_workload_calls_response_generator(client, pipeline_mocks):
    """Successful ANALYZE_WORKLOAD calls response generator."""
    _happy_path(pipeline_mocks)

    # Override executor to return ANALYZE_WORKLOAD result
    pipeline_mocks["executor"].execute.return_value = ExecutionResult(
        status=ExecutionStatus.EXECUTED,
        action_type=ActionType.ANALYZE_WORKLOAD,
        message="Workload analysis completed.",
        result_payload={
            "total_tasks": 3,
            "deadline_collisions": [],
            "busy_days": [],
            "recommended_order": [],
            "explanation": "You have 3 active tasks.",
        },
    )

    # Mock response generator
    pipeline_mocks["response_generator"].generate_response.return_value = (
        "You have 3 tasks this week!"
    )

    response = client.post(
        "/api/v1/agent/chat",
        json={"message": "Show my workload"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == AgentChatStatus.EXECUTED.value
    assert body["message"] == "You have 3 tasks this week!"

    # Verify generator was called
    pipeline_mocks["response_generator"].generate_response.assert_called_once()
    call_kwargs = pipeline_mocks["response_generator"].generate_response.call_args.kwargs
    assert call_kwargs["execution_result"].action_type == ActionType.ANALYZE_WORKLOAD
    assert call_kwargs["user_message"] == "Show my workload"


def test_analyze_workload_generated_response_becomes_message(client, pipeline_mocks):
    """Generated response becomes AgentChatResponse.message."""
    _happy_path(pipeline_mocks)

    pipeline_mocks["executor"].execute.return_value = ExecutionResult(
        status=ExecutionStatus.EXECUTED,
        action_type=ActionType.ANALYZE_WORKLOAD,
        message="Original message",
        result_payload={"total_tasks": 5, "explanation": "5 tasks"},
    )

    pipeline_mocks["response_generator"].generate_response.return_value = (
        "Custom generated response"
    )

    response = client.post(
        "/api/v1/agent/chat",
        json={"message": "Analyze workload"},
    )

    body = response.json()
    assert body["message"] == "Custom generated response"
    assert body["message"] != "Original message"


def test_analyze_workload_preserves_execution_result(client, pipeline_mocks):
    """Original execution_result is preserved unchanged."""
    _happy_path(pipeline_mocks)

    original_result = ExecutionResult(
        status=ExecutionStatus.EXECUTED,
        action_type=ActionType.ANALYZE_WORKLOAD,
        message="Original message",
        result_payload={"totalTasks": 7, "explanation": "7 tasks"},
    )
    pipeline_mocks["executor"].execute.return_value = original_result

    pipeline_mocks["response_generator"].generate_response.return_value = (
        "Generated response"
    )

    response = client.post(
        "/api/v1/agent/chat",
        json={"message": "Show workload"},
    )

    body = response.json()
    # execution_result unchanged
    assert body["execution_result"]["actionType"] == "ANALYZE_WORKLOAD"
    assert body["execution_result"]["message"] == "Original message"
    assert body["execution_result"]["resultPayload"]["totalTasks"] == 7


def test_non_workload_action_keeps_original_message(client, pipeline_mocks):
    """Non-ANALYZE_WORKLOAD action does not call generator."""
    _happy_path(pipeline_mocks)

    # CREATE_TASK action
    pipeline_mocks["executor"].execute.return_value = ExecutionResult(
        status=ExecutionStatus.EXECUTED,
        action_type=ActionType.CREATE_TASK,
        message="Task created successfully.",
        result_id=uuid4(),
        result_name="New Task",
    )

    response = client.post(
        "/api/v1/agent/chat",
        json={"message": "Create a task"},
    )

    body = response.json()
    assert body["message"] == "Task created successfully."

    # Generator NOT called
    pipeline_mocks["response_generator"].generate_response.assert_not_called()


def test_early_return_paths_do_not_call_generator(client, pipeline_mocks):
    """Early-return paths (clarification, rejection, etc.) do not call generator."""
    # Interpreter returns CLARIFICATION_REQUIRED
    pipeline_mocks["interpreter"].interpret.return_value = AgentResponse(
        response_type=ResponseType.CLARIFICATION_REQUIRED,
        message="Please clarify your request.",
        proposal=None,
    )

    response = client.post(
        "/api/v1/agent/chat",
        json={"message": "unclear request"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == AgentChatStatus.CLARIFICATION_REQUIRED.value

    # Generator NOT called
    pipeline_mocks["response_generator"].generate_response.assert_not_called()


def test_confirmation_execution_calls_generator_for_analyze_workload(client, pipeline_mocks):
    """Confirmation execution calls generator for ANALYZE_WORKLOAD."""
    confirmation_id = uuid4()
    stored_command = ExecutionCommand(
        action_type=ActionType.ANALYZE_WORKLOAD,
        parameters={
            "date_range_expression": "THIS_WEEK",
            "explicit_start": None,
            "explicit_end": None,
        },
    )

    pipeline_mocks["confirmation_module"].confirm.return_value = ConfirmResult(
        outcome=ConfirmOutcome.CONFIRMED,
        confirmation=SimpleNamespace(
            id=confirmation_id,
            status=PendingConfirmationStatus.CONFIRMED.value,
            execution_command=stored_command.model_dump_json(),
        ),
    )

    pipeline_mocks["executor"].execute.return_value = ExecutionResult(
        status=ExecutionStatus.EXECUTED,
        action_type=ActionType.ANALYZE_WORKLOAD,
        message="Workload analysis completed.",
        result_payload={"total_tasks": 2, "explanation": "2 tasks"},
    )

    pipeline_mocks["response_generator"].generate_response.return_value = (
        "You have 2 tasks confirmed!"
    )

    pipeline_mocks["interpreter"].interpret.return_value = AgentResponse(
        response_type=ResponseType.CONFIRMATION,
        message="",
        proposal=None,
        confirmation_id=confirmation_id,
    )

    response = client.post(
        "/api/v1/agent/chat",
        json={"message": "yes"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == AgentChatStatus.EXECUTED.value
    assert body["message"] == "You have 2 tasks confirmed!"

    # Generator called
    pipeline_mocks["response_generator"].generate_response.assert_called_once()


def test_generator_failure_does_not_cause_action_re_execution(client, pipeline_mocks):
    """Generator fallback/failure does not cause action re-execution."""
    _happy_path(pipeline_mocks)

    pipeline_mocks["executor"].execute.return_value = ExecutionResult(
        status=ExecutionStatus.EXECUTED,
        action_type=ActionType.ANALYZE_WORKLOAD,
        message="Fallback message",
        result_payload={"total_tasks": 1},
    )

    # Generator returns fallback (simulating LLM failure handled internally)
    pipeline_mocks["response_generator"].generate_response.return_value = (
        "Fallback message"
    )

    response = client.post(
        "/api/v1/agent/chat",
        json={"message": "Show workload"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == AgentChatStatus.EXECUTED.value

    # Action executed exactly once
    pipeline_mocks["executor"].execute.assert_called_once()


def test_action_executor_called_exactly_once_with_generator(client, pipeline_mocks):
    """Action executor is called exactly once even with response generation."""
    _happy_path(pipeline_mocks)

    pipeline_mocks["executor"].execute.return_value = ExecutionResult(
        status=ExecutionStatus.EXECUTED,
        action_type=ActionType.ANALYZE_WORKLOAD,
        message="Analysis complete",
        result_payload={"total_tasks": 4},
    )

    pipeline_mocks["response_generator"].generate_response.return_value = (
        "Generated response"
    )

    client.post(
        "/api/v1/agent/chat",
        json={"message": "Analyze my workload"},
    )

    # Executor called exactly once
    assert pipeline_mocks["executor"].execute.call_count == 1


def test_failed_execution_does_not_call_generator(client, pipeline_mocks):
    """Failed execution does not call response generator."""
    _happy_path(pipeline_mocks)

    pipeline_mocks["executor"].execute.return_value = ExecutionResult(
        status=ExecutionStatus.EXECUTION_FAILED,
        action_type=ActionType.ANALYZE_WORKLOAD,
        message="Analysis failed",
        error_code=ExecutionErrorCode.INFRASTRUCTURE,
    )

    response = client.post(
        "/api/v1/agent/chat",
        json={"message": "Show workload"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == AgentChatStatus.ERROR.value
    assert body["message"] == "Analysis failed"

    # Generator NOT called on failed execution
    pipeline_mocks["response_generator"].generate_response.assert_not_called()

