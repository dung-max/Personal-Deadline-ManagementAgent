"""Execution result contract.

Distinguishes:

* ``EXECUTED`` — the underlying application use case completed successfully.
* ``NOT_EXECUTABLE`` — the decision was not AUTHORIZED, so nothing ran.
* ``EXECUTION_FAILED`` — the underlying application operation failed.

``error_code`` categorizes a failure (NOT_FOUND / INVALID_INPUT /
INFRASTRUCTURE) so the Agent can respond differently without exposing
infrastructure details.  ``result_id``/``result_name`` carry the minimal
payload of a created/updated resource — never the full ORM object.

No database, session, or infrastructure internals are exposed.  Error
messages are deterministic and safe.
"""

from __future__ import annotations

import enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..schemas.agent import ActionType


class ExecutionStatus(str, enum.Enum):
    """Outcome of an execution attempt."""

    EXECUTED = "EXECUTED"
    NOT_EXECUTABLE = "NOT_EXECUTABLE"
    EXECUTION_FAILED = "EXECUTION_FAILED"


class ExecutionErrorCode(str, enum.Enum):
    """Safe, deterministic category of an execution failure."""

    NOT_FOUND = "NOT_FOUND"
    INVALID_INPUT = "INVALID_INPUT"
    INFRASTRUCTURE = "INFRASTRUCTURE"


class ExecutionResult(BaseModel):
    """Structured result of an execution attempt.

    Pure data — no ``execute()`` method, no database/session internals, no
    stack traces.  The ``message`` field carries a deterministic, safe
    description suitable for the Agent contract.
    """

    status: ExecutionStatus
    action_type: ActionType | str = Field(alias="actionType")
    resource_id: UUID | None = Field(default=None, alias="resourceId")
    message: str = ""
    error_code: ExecutionErrorCode | None = Field(default=None, alias="errorCode")
    result_id: UUID | None = Field(default=None, alias="resultId")
    result_name: str | None = Field(default=None, alias="resultName")
    result_payload: dict[str, Any] | None = Field(default=None, alias="resultPayload")

    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)
