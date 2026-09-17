"""Agent HTTP handler (application boundary).

Receives a natural-language request, runs the full Agent pipeline
(interpret → validate → resolve → decide → execute), and returns a single
shared response contract.  The handler performs no business validation and
never commits/rolls back transactions — that stays with the Modules.

```text
AgentRequest
  ↓  a. AgentInterpreter
AgentResponse
  ├─ CONFIRMATION → resolve pending confirmation → execute stored command
  └─ ACTION_PROPOSED
       ↓  b. ActionValidator
     ValidationResult
       ↓  c. ResourceResolver
     ValidatedAction
       ↓  d. DecisionService
     DecisionResult
       ↓  e. Branch
     ExecutionResult → AgentChatResponse
```
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from ..config import Settings
from ..dependencies import (
    get_action_executor,
    get_action_validator,
    get_agent_interpreter,
    get_agent_response_generator,
    get_decision_service,
    get_pending_confirmation_module,
    get_resource_resolver,
    get_settings,
)
from ..guardrails.decision_service import DecisionService
from ..guardrails.safety_policy import DecisionResult, DecisionStatus
from ..modules.pending_confirmation_module import (
    ConfirmOutcome,
    ConfirmResult,
    PendingConfirmationModule,
)
from ..schemas.agent import AgentRequest, ResponseType
from ..schemas.agent_chat import AgentChatRequest, AgentChatResponse, AgentChatStatus
from ..schemas.agent import ActionType
from ..services.action_executor import ActionExecutor
from ..services.action_validator import ValidationStatus, ValidatedAction
from ..services.agent_interpreter import AgentInterpreter
from ..services.agent_response_generator import AgentResponseGenerator
from ..services.authorization_service import AuthorizationContext
from ..services.execution_command import ExecutionCommand
from ..services.execution_result import (
    ExecutionErrorCode,
    ExecutionResult,
    ExecutionStatus,
)
from ..services.resource_resolver import ResourceResolver

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["agent"])

# Interpreter response_type → AgentChatStatus (proposal is None).
_INTERPRETER_STATUS_MAP = {
    ResponseType.CLARIFICATION_REQUIRED: AgentChatStatus.CLARIFICATION_REQUIRED,
    ResponseType.REJECTED: AgentChatStatus.REJECTED,
    ResponseType.CONVERSATION: AgentChatStatus.CONVERSATION,
}

# Validation status → AgentChatStatus (proposal failed structural check).
_VALIDATION_STATUS_MAP = {
    ValidationStatus.CLARIFICATION_REQUIRED: AgentChatStatus.CLARIFICATION_REQUIRED,
    ValidationStatus.REJECTED: AgentChatStatus.REJECTED,
}


def _agent_status_for(execution_result: ExecutionResult) -> AgentChatStatus:
    """Map an execution result to the Agent chat status."""
    if execution_result.status is ExecutionStatus.EXECUTED:
        return AgentChatStatus.EXECUTED
    if execution_result.error_code is ExecutionErrorCode.INVALID_INPUT:
        return AgentChatStatus.INVALID_INPUT
    return AgentChatStatus.ERROR


@router.post("/agent/chat", response_model=AgentChatResponse)
def agent_chat(
    payload: AgentChatRequest,
    interpreter: AgentInterpreter = Depends(get_agent_interpreter),
    validator: ActionValidator = Depends(get_action_validator),
    resolver: ResourceResolver = Depends(get_resource_resolver),
    decision_service: DecisionService = Depends(get_decision_service),
    executor: ActionExecutor = Depends(get_action_executor),
    confirmation_module: PendingConfirmationModule = Depends(
        get_pending_confirmation_module
    ),
    response_generator: AgentResponseGenerator = Depends(
        get_agent_response_generator
    ),
    settings: Settings = Depends(get_settings),
) -> AgentChatResponse:
    """Run the full Agent pipeline for a natural-language request."""
    try:
        return _process(
            payload,
            interpreter,
            validator,
            resolver,
            decision_service,
            executor,
            confirmation_module,
            response_generator,
            settings,
        )
    except Exception:
        logger.exception(
            "Agent chat failed: message_len=%d user_id=%s",
            len(payload.message),
            payload.user_id,
        )
        return AgentChatResponse(
            status=AgentChatStatus.ERROR,
            message="Something went wrong. Please try again.",
        )


# ----- Pipeline ---------------------------------------------------------------


def _process(
    payload: AgentChatRequest,
    interpreter: AgentInterpreter,
    validator: ActionValidator,
    resolver: ResourceResolver,
    decision_service: DecisionService,
    executor: ActionExecutor,
    confirmation_module: PendingConfirmationModule,
    response_generator: AgentResponseGenerator,
    settings: Settings,
) -> AgentChatResponse:
    # --- 0. Housekeeping: mark expired confirmations -------------------------
    confirmation_module.cleanup_expired()

    # --- a. Interpret --------------------------------------------------------
    agent_response = interpreter.interpret(AgentRequest(message=payload.message))

    # --- a'. Confirm-intent branch -------------------------------------------
    if agent_response.response_type is ResponseType.CONFIRMATION:
        return _handle_confirmation(
            payload, agent_response, confirmation_module, executor, response_generator
        )

    if agent_response.proposal is None:
        return AgentChatResponse(
            status=_INTERPRETER_STATUS_MAP[agent_response.response_type],
            message=agent_response.message,
        )

    proposal = agent_response.proposal

    # --- b. Validate ---------------------------------------------------------
    validation = validator.validate(proposal)
    if validation.status is not ValidationStatus.VALID:
        return AgentChatResponse(
            status=_VALIDATION_STATUS_MAP[validation.status],
            message=validation.message,
        )

    # --- c. Resolve ----------------------------------------------------------
    resolution = resolver.resolve(proposal)
    if resolution.status is not ValidationStatus.VALID:
        return AgentChatResponse(
            status=AgentChatStatus.CLARIFICATION_REQUIRED,
            message=resolution.message,
        )
    validated_action = resolution.validated_action

    # --- d. Decide -----------------------------------------------------------
    context = (
        AuthorizationContext(actor_id=payload.user_id)
        if payload.user_id is not None
        else None
    )
    decision = decision_service.decide(validated_action, context)

    # --- e. Branch -----------------------------------------------------------
    if decision.status is DecisionStatus.AUTHORIZED:
        command = ExecutionCommand.from_decision(decision)
        execution_result = executor.execute(decision, command)

        # Generate natural-language response for ANALYZE_WORKLOAD
        message = execution_result.message
        if (
            execution_result.status is ExecutionStatus.EXECUTED
            and execution_result.action_type == ActionType.ANALYZE_WORKLOAD
        ):
            message = response_generator.generate_response(
                execution_result=execution_result,
                user_message=payload.message,
            )

        return AgentChatResponse(
            status=_agent_status_for(execution_result),
            message=message,
            execution_result=execution_result,
        )

    if decision.status is DecisionStatus.CONFIRMATION_REQUIRED:
        command = ExecutionCommand.from_decision(decision)
        confirmation = confirmation_module.create(
            user_id=payload.user_id,
            command=command,
            ttl_seconds=settings.pending_confirmation_ttl_seconds,
        )
        return AgentChatResponse(
            status=AgentChatStatus.CONFIRMATION_REQUIRED,
            message=(
                f"{decision.reason} "
                f"Reply 'yes' to confirm (reference: {confirmation.id})."
            ),
            confirmation_id=confirmation.id,
        )

    if decision.status is DecisionStatus.DENIED:
        return AgentChatResponse(
            status=AgentChatStatus.DENIED,
            message=decision.reason,
        )

    # DecisionStatus.NOT_CONFIGURED
    return AgentChatResponse(
        status=AgentChatStatus.NOT_CONFIGURED,
        message=decision.reason,
    )


# ----- Confirmation flow ------------------------------------------------------


def _handle_confirmation(
    payload: AgentChatRequest,
    agent_response,
    confirmation_module: PendingConfirmationModule,
    executor: ActionExecutor,
    response_generator: AgentResponseGenerator,
) -> AgentChatResponse:
    """Resolve a pending confirmation and execute the stored command."""
    confirmation_id = agent_response.confirmation_id

    # If no reference ID, fall back to the latest pending confirmation
    # for this user (single-Q&A-round shortcut).
    if confirmation_id is None:
        latest = confirmation_module.get_latest_pending(payload.user_id)
        if latest is None:
            return AgentChatResponse(
                status=AgentChatStatus.CLARIFICATION_REQUIRED,
                message=(
                    "I don't see a pending action to confirm. "
                    "Please send your original request again."
                ),
            )
        confirmation_id = latest.id

    result = confirmation_module.confirm(confirmation_id, payload.user_id)

    if result.outcome is ConfirmOutcome.NOT_FOUND:
        return AgentChatResponse(
            status=AgentChatStatus.CLARIFICATION_REQUIRED,
            message="No pending confirmation found for this reference.",
        )

    if result.outcome is ConfirmOutcome.ALREADY_CONFIRMED:
        return AgentChatResponse(
            status=AgentChatStatus.CLARIFICATION_REQUIRED,
            message=(
                "This request has already been processed. "
                "Please send a new request if you need to do it again."
            ),
            confirmation_id=confirmation_id,
        )

    if result.outcome is ConfirmOutcome.EXPIRED:
        return AgentChatResponse(
            status=AgentChatStatus.EXPIRED,
            message=(
                "This confirmation request has expired. "
                "Please send your original request again."
            ),
            confirmation_id=confirmation_id,
        )

    if result.outcome is ConfirmOutcome.CANCELLED:
        return AgentChatResponse(
            status=AgentChatStatus.CLARIFICATION_REQUIRED,
            message=(
                "This confirmation is no longer valid. "
                "Please send a new request."
            ),
            confirmation_id=confirmation_id,
        )

    # CONFIRMED — execute the stored command directly (no pipeline re-run).
    confirmation = result.confirmation
    command = ExecutionCommand.model_validate_json(
        confirmation.execution_command
    )
    decision = _decision_for(command)
    execution_result = executor.execute(decision, command)

    # Generate natural-language response for ANALYZE_WORKLOAD
    message = execution_result.message
    if (
        execution_result.status is ExecutionStatus.EXECUTED
        and execution_result.action_type == ActionType.ANALYZE_WORKLOAD
    ):
        message = response_generator.generate_response(
            execution_result=execution_result,
            user_message=payload.message,
        )

    return AgentChatResponse(
        status=_agent_status_for(execution_result),
        message=message,
        execution_result=execution_result,
        confirmation_id=confirmation_id,
    )


def _decision_for(command: ExecutionCommand) -> DecisionResult:
    """Reconstruct the AUTHORIZED decision for a stored command.

    The command was already validated, resolved, and authorized when the
    pending confirmation was created — this only rebuilds the decision
    record the executor requires, without re-running the pipeline.
    """
    return DecisionResult(
        status=DecisionStatus.AUTHORIZED,
        reason="Confirmed by the user.",
        action=ValidatedAction(
            action_type=command.action_type,
            resource_id=command.resource_id,
            parameters=command.parameters,
        ),
    )
