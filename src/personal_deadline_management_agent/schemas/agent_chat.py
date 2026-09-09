"""Agent chat HTTP contract schemas.

Shared request/response contract for the Agent endpoint.  The single
response model covers every pipeline outcome — interpreted action,
validation failure, resource resolution failure, authorization/safety
decision, execution result, and unexpected errors.
"""

from __future__ import annotations

import enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..services.execution_result import ExecutionResult


class AgentChatStatus(str, enum.Enum):
    """Outcome of an Agent chat request.

    Every pipeline branch maps to exactly one status so the Agent (or
    an API consumer) can switch on a single field.
    """

    EXECUTED = "EXECUTED"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    DENIED = "DENIED"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    REJECTED = "REJECTED"
    CONVERSATION = "CONVERSATION"
    ERROR = "ERROR"
    EXPIRED = "EXPIRED"
    INVALID_INPUT = "INVALID_INPUT"


class AgentChatRequest(BaseModel):
    """Natural-language request to the Agent endpoint."""

    message: str = Field(min_length=1)
    user_id: UUID | None = Field(default=None, alias="userId")

    model_config = ConfigDict(populate_by_name=True)


class AgentChatResponse(BaseModel):
    """Shared response for every Agent chat outcome.

    ``status`` discriminates the outcome; ``message`` is the safe,
    user-facing text; ``execution_result`` is present only when the
    action was executed (or failed to execute); ``confirmation_id`` is
    present only when a pending confirmation was created or resolved.
    """

    status: AgentChatStatus
    message: str
    execution_result: ExecutionResult | None = None
    confirmation_id: UUID | None = Field(default=None, alias="confirmationId")

    model_config = ConfigDict(populate_by_name=True)
