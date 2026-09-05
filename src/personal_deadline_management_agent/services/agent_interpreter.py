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

Return your interpretation as structured JSON matching the required output schema.

Rules:
- Propose ONLY the supported action types listed above.
- For UPDATE_TASK, DELETE_TASK, UPDATE_REMINDER, DELETE_REMINDER you MUST \
identify the target resource.
- For CREATE_REMINDER you MUST identify the parent task.
- CREATE_TASK has no target resource — never set resource_id or \
resource_description for it; put the new task's name in parameters.taskName.
- If the user provides an explicit UUID, set resource_id to that UUID as a string.
- If the target is described in natural language (e.g. "my report task"), \
set resource_description to that exact phrase. NEVER invent or guess a UUID — \
only set resource_id when the user explicitly gave one.
- Extract action-specific data into the "parameters" field. \
For tasks include: taskName, description, deadline, priority. \
For reminders include: remindAt and optionally taskId (if a UUID was given).
- Use ISO 8601 for date/time values.
- If the request is ambiguous or missing information required for the action, \
use NEEDS_CLARIFICATION with a brief message explaining what is needed.
- If the request does not correspond to any supported action, use REJECTED \
with a brief message. Never map it to an unrelated action.
- For conversational requests that do not describe an action (e.g. greetings, \
questions), use CONVERSATION.
"""


# ---------------------------------------------------------------------------
# Interpreter
# ---------------------------------------------------------------------------

_RESPONSE_TYPE_MAP: dict[str, ResponseType] = {
    "ACTION_PROPOSED": ResponseType.ACTION_PROPOSED,
    "NEEDS_CLARIFICATION": ResponseType.CLARIFICATION_REQUIRED,
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
        response_type = _RESPONSE_TYPE_MAP[output.response_type]
        return AgentResponse(
            response_type=response_type,
            message=output.message,
            proposal=None,
        )

    def _handle_action_proposed(self, output: InterpretationOutput) -> AgentResponse:
        if output.action_type is None:
            return AgentResponse(
                response_type=ResponseType.CLARIFICATION_REQUIRED,
                message=output.message or "The request is ambiguous.",
                proposal=None,
            )

        # CREATE_TASK has no target resource.
        if output.action_type == ActionType.CREATE_TASK:
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
