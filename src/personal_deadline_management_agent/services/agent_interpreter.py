"""Agent interpretation service.

Interprets user intent from natural language and produces an ``ActionProposal``
via structured LLM generation.  No database access, no resource resolution,
no action execution.

```text
AgentRequest
  ↓
AgentInterpreter.interpret
  ↓
StructuredGenerationPort
  ↓
ActionProposal (untrusted)
```
"""

from __future__ import annotations

import logging
from uuid import UUID

from ..adapters.structured_generation import StructuredGenerationPort
from ..schemas.agent import (
    ActionProposal,
    ActionType,
    AgentRequest,
    AgentResponse,
    InterpretationOutput,
    ProposalStatus,
    ResourceReference,
    ResponseType,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# System prompt (trusted — never contains user input)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an intent interpreter for a personal deadline management assistant.
Your task is to understand the user's natural-language request and propose exactly \
one supported application action, or indicate clarification/rejection.

Supported action types:
  CREATE_TASK   — create a new task
  UPDATE_TASK   — update an existing task
  DELETE_TASK   — delete an existing task
  CREATE_REMINDER — create a reminder for a task
  UPDATE_REMINDER — update an existing reminder
  DELETE_REMINDER — delete an existing reminder
  ANALYZE_WORKLOAD — analyze the user's workload for a specified date range
  SUGGEST_RESCHEDULING — suggest deterministic candidate rescheduling windows

Return your interpretation as structured JSON matching the required output schema.

Rules:
- Propose ONLY the supported action types listed above.
- For UPDATE_TASK, DELETE_TASK, UPDATE_REMINDER, DELETE_REMINDER you MUST \
identify the target resource.
- For CREATE_REMINDER you MUST identify the parent task.
- CREATE_TASK, ANALYZE_WORKLOAD and SUGGEST_RESCHEDULING have no target resource — never set resource_id or \
resource_description for them; for CREATE_TASK put the new task's name in \
parameters.taskName.
- If the user provides an explicit UUID, set resource_id to that UUID as a string.
- If the target is described in natural language (e.g. "my report task"), \
set resource_description to that exact phrase. NEVER invent or guess a UUID — \
only set resource_id when the user explicitly gave one.
- Extract action-specific data into the "parameters" field. \
For tasks include: taskName, description, deadline, priority. \
For reminders include: remindAt and optionally taskId (if a UUID was given). \
For ANALYZE_WORKLOAD include: date_range_expression (required), and if the \
expression is "EXPLICIT_RANGE", also include explicit_start and explicit_end. \
For SUGGEST_RESCHEDULING include: date_range_expression (same as ANALYZE_WORKLOAD), \
and if the expression is "EXPLICIT_RANGE", also include explicit_start and explicit_end.
- Use ISO 8601 for date/time values.
- For ANALYZE_WORKLOAD, map the user's date reference to a semantic \
date_range_expression. NEVER calculate actual date boundaries — only identify \
the semantic keyword:
  "today" / "tasks today" / "workload today" → TODAY
  "tomorrow" / "tasks tomorrow" → TOMORROW
  "this week" / "workload this week" / "how many tasks this week" → THIS_WEEK
  "next week" / "busy next week" → NEXT_WEEK
  "this month" / "workload this month" → THIS_MONTH
  "next month" / "workload next month" → NEXT_MONTH
  Explicit dates like "from Sep 15 to Sep 21" → EXPLICIT_RANGE (with explicit_start \
and explicit_end as ISO 8601).
  Do NOT calculate which actual dates "this week" corresponds to — the application \
will resolve the semantic expression deterministically.
- For SUGGEST_RESCHEDULING, map natural language requests for rescheduling suggestions:
  "suggest when I should reschedule" → SUGGEST_RESCHEDULING
  "which tasks should I move" → SUGGEST_RESCHEDULING
  "can you suggest alternative times" → SUGGEST_RESCHEDULING
  "how can I rearrange my workload" → SUGGEST_RESCHEDULING
  "where could I fit my overloaded tasks" → SUGGEST_RESCHEDULING
  "gợi ý thời gian để sắp xếp lại" → SUGGEST_RESCHEDULING
  "những task nào nên dời" → SUGGEST_RESCHEDULING
  Use the same date_range_expression logic as ANALYZE_WORKLOAD (default to THIS_WEEK \
if no specific range is mentioned).
- If the request is ambiguous or missing information required for the action, \
use NEEDS_CLARIFICATION with a brief message explaining what is needed.
- If the request does not correspond to any supported action, use REJECTED \
with a brief message. Never map it to an unrelated action.
- For conversational requests that do not describe an action (e.g. greetings, \
questions), use CONVERSATION.
- If the user is confirming a previously pending destructive action \
(e.g. "yes", "ok", "confirm", "đồng ý", "có", "xác nhận"), use CONFIRMATION. \
If the user includes a reference ID (UUID), set resource_id to that UUID string. \
If no reference ID is provided, set resource_id to null.
"""


# ---------------------------------------------------------------------------
# Interpreter
# ---------------------------------------------------------------------------

_RESPONSE_TYPE_MAP: dict[str, ResponseType] = {
    "ACTION_PROPOSED": ResponseType.ACTION_PROPOSED,
    "NEEDS_CLARIFICATION": ResponseType.CLARIFICATION_REQUIRED,
    "CONFIRMATION": ResponseType.CONFIRMATION,
    "REJECTED": ResponseType.REJECTED,
    "CONVERSATION": ResponseType.CONVERSATION,
}


class AgentInterpreter:
    """Interprets user intent via structured LLM generation.

    Depends on ``StructuredGenerationPort`` — inject a fake implementation
    in tests; inject ``GenaiCoreBedrockAdapter`` in production.
    """

    def __init__(self, llm: StructuredGenerationPort) -> None:  # type: ignore[type-arg]
        self._llm = llm

    def interpret(self, request: AgentRequest) -> AgentResponse:
        """Interpret a natural-language request and produce an AgentResponse.

        The returned ``AgentResponse.proposal`` is an untrusted
        ``ActionProposal`` — the caller must validate and execute it separately.
        """
        output: InterpretationOutput = self._llm.generate(
            system_prompt=_SYSTEM_PROMPT,
            user_prompt=request.message,
            output_type=InterpretationOutput,
        )
        return self._to_agent_response(output)

    # ------------------------------------------------------------------

    def _to_agent_response(self, output: InterpretationOutput) -> AgentResponse:
        if output.response_type == "ACTION_PROPOSED":
            return self._handle_action_proposed(output)
        if output.response_type == "CONFIRMATION":
            return self._handle_confirmation(output)
        response_type = _RESPONSE_TYPE_MAP[output.response_type]
        return AgentResponse(
            response_type=response_type,
            message=output.message,
            proposal=None,
        )

    def _handle_confirmation(self, output: InterpretationOutput) -> AgentResponse:
        """Build a confirm-intent response.

        ``resource_id`` carries the pending-confirmation reference ID when
        the user included one; an invalid UUID is treated as an error
        requiring clarification — never silently dropped.
        """
        confirmation_id: UUID | None = None
        if output.resource_id is not None:
            try:
                confirmation_id = UUID(output.resource_id)
            except ValueError:
                return AgentResponse(
                    response_type=ResponseType.CLARIFICATION_REQUIRED,
                    message=output.message or "The confirmation reference is invalid.",
                    proposal=None,
                )
        return AgentResponse(
            response_type=ResponseType.CONFIRMATION,
            message=output.message,
            proposal=None,
            confirmation_id=confirmation_id,
        )

    def _handle_action_proposed(self, output: InterpretationOutput) -> AgentResponse:
        if output.action_type is None:
            return AgentResponse(
                response_type=ResponseType.CLARIFICATION_REQUIRED,
                message=output.message or "The request is ambiguous.",
                proposal=None,
            )

        # CREATE_TASK and ANALYZE_WORKLOAD have no target resource.
        if output.action_type in {ActionType.CREATE_TASK, ActionType.ANALYZE_WORKLOAD, ActionType.SUGGEST_RESCHEDULING}:
            resource: ResourceReference | None = None
        else:
            try:
                resource = self._build_resource_reference(output)
            except _ResourceError as exc:
                logger.debug("Cannot build resource reference: %s", exc)
                return AgentResponse(
                    response_type=ResponseType.CLARIFICATION_REQUIRED,
                    message=output.message or "The request is ambiguous.",
                    proposal=None,
                )

        proposal = ActionProposal(
            action_type=output.action_type,
            resource=resource,
            parameters=output.parameters or {},
            status=ProposalStatus.PROPOSED,
        )
        return AgentResponse(
            response_type=ResponseType.ACTION_PROPOSED,
            message=output.message,
            proposal=proposal,
        )

    def _build_resource_reference(self, output: InterpretationOutput) -> ResourceReference:
        """Build a ResourceReference from LLM output without fabricating IDs.

        An invalid ``resource_id`` is treated as an error requiring
        clarification — it must NEVER be reinterpreted as a natural-language
        reference.  Raises ``_ResourceError`` when no usable reference is
        available.
        """
        resource_id = output.resource_id
        resource_description = output.resource_description

        if resource_id is not None:
            try:
                parsed = UUID(resource_id)
            except ValueError:
                raise _ResourceError(f"Invalid UUID from LLM: {resource_id!r}")
            return ResourceReference(id=parsed)

        if resource_description:
            return ResourceReference(natural_language=resource_description)

        raise _ResourceError("No resource reference provided")


class _ResourceError(Exception):
    """Internal: the LLM output did not contain a usable resource reference."""
