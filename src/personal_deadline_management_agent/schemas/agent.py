"""Agent contract schemas.

Defines the untrusted contracts between the Agent (LLM) and the application.
The Agent produces ActionProposals; the application validates and executes them.
No execution logic, LLM integration, or workflow state machines here.
"""

from __future__ import annotations

import enum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ActionType(str, enum.Enum):
    """Supported application actions the Agent may propose."""

    CREATE_TASK = "CREATE_TASK"
    UPDATE_TASK = "UPDATE_TASK"
    DELETE_TASK = "DELETE_TASK"
    CREATE_REMINDER = "CREATE_REMINDER"
    UPDATE_REMINDER = "UPDATE_REMINDER"
    DELETE_REMINDER = "DELETE_REMINDER"


class ProposalStatus(str, enum.Enum):
    """Lifecycle status of an ActionProposal.

    These are contract values only — the state machine is not yet implemented.
    """

    PROPOSED = "PROPOSED"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    NEEDS_CONFIRMATION = "NEEDS_CONFIRMATION"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"


class ResponseType(str, enum.Enum):
    """Discriminator for AgentResponse."""

    ACTION_PROPOSED = "ACTION_PROPOSED"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    REJECTED = "REJECTED"
    CONVERSATION = "CONVERSATION"


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ResourceReference(BaseModel):
    """Untrusted reference to a domain resource.

    At least one of ``id`` or ``natural_language`` must be provided.
    The application resolves ``natural_language`` into a canonical ID later.
    """

    id: UUID | None = None
    natural_language: str | None = None

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="after")
    def check_at_least_one_reference(self) -> ResourceReference:
        if self.id is None and self.natural_language is None:
            raise ValueError(
                "At least one of 'id' or 'natural_language' must be provided"
            )
        return self


class ActionProposal(BaseModel):
    """Untrusted action proposal produced by the Agent/LLM.

    ``parameters`` carries the action-specific data (e.g. task name, deadline).
    ``resource`` is the target resource reference for actions that act on an
    existing resource.  It is ``None`` for CREATE_TASK, which has no target
    resource.  The application validates parameters and resolves ``resource``
    against ``action_type`` later.
    """

    action_type: ActionType
    resource: ResourceReference | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    status: ProposalStatus = ProposalStatus.PROPOSED

    model_config = ConfigDict(populate_by_name=True)


class AgentRequest(BaseModel):
    """Structured input to the Agent — natural language from the user."""

    message: str = Field(min_length=1)

    model_config = ConfigDict(populate_by_name=True)


class AgentResponse(BaseModel):
    """Structured response from the Agent."""

    response_type: ResponseType
    message: str
    proposal: ActionProposal | None = None

    model_config = ConfigDict(populate_by_name=True)


# ---------------------------------------------------------------------------
# LLM output schema (Phase 4.3 — Agent Interpretation)
# ---------------------------------------------------------------------------

InterpretationResponseType = Literal[
    "ACTION_PROPOSED",
    "NEEDS_CLARIFICATION",
    "REJECTED",
    "CONVERSATION",
]


class InterpretationOutput(BaseModel):
    """Structured output produced by the LLM during intent interpretation.

    ``resource_id`` is a string because JSON does not have native UUID.
    The interpreter validates it into a UUID if present, or falls back to
    ``resource_description`` for a natural-language reference.  If the LLM
    supplies neither, the interpreter returns ``CLARIFICATION_REQUIRED``
    rather than fabricate a UUID.
    """

    response_type: InterpretationResponseType
    action_type: ActionType | None = None
    resource_id: str | None = None
    resource_description: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    message: str = Field(default="")
