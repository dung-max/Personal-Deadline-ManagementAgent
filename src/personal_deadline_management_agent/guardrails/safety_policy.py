"""Application-level safety policy.

Deterministic guardrails shared by the Agent and the Direct API.  The LLM never
decides whether its own proposed action is safe; this policy is pure
application policy and performs no execution, no DB access, and no LLM calls.

```text
ValidatedAction
  ↓
SafetyPolicy.evaluate
  ↓
DecisionResult (AUTHORIZED | DENIED | CONFIRMATION_REQUIRED)
```
"""

from __future__ import annotations

import enum

from pydantic import BaseModel, ConfigDict

from ..schemas.agent import ActionType
from ..services.action_validator import ValidatedAction


class DecisionStatus(str, enum.Enum):
    """Outcome of the combined authorization + safety-policy decision."""

    AUTHORIZED = "AUTHORIZED"
    DENIED = "DENIED"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"


class DecisionResult(BaseModel):
    """Minimal application result after authorization and safety checks.

    This is pure data — it does NOT expose an ``execute()`` method and is not
    a command object.
    """

    status: DecisionStatus
    reason: str = ""
    action: ValidatedAction

    model_config = ConfigDict(populate_by_name=True)


SUPPORTED_ACTIONS = frozenset(
    {
        ActionType.CREATE_TASK,
        ActionType.UPDATE_TASK,
        ActionType.DELETE_TASK,
        ActionType.CREATE_REMINDER,
        ActionType.UPDATE_REMINDER,
        ActionType.DELETE_REMINDER,
        ActionType.ANALYZE_WORKLOAD,
    }
)

DESTRUCTIVE_ACTIONS = frozenset(
    {
        ActionType.DELETE_TASK,
        ActionType.DELETE_REMINDER,
    }
)


class SafetyPolicy:
    """Deterministic safety policy for validated actions."""

    def evaluate(self, action: ValidatedAction) -> DecisionResult:
        if action.action_type not in SUPPORTED_ACTIONS:
            return DecisionResult(
                status=DecisionStatus.DENIED,
                reason="Action type is not supported by the safety policy.",
                action=action,
            )

        if action.action_type in DESTRUCTIVE_ACTIONS:
            return DecisionResult(
                status=DecisionStatus.CONFIRMATION_REQUIRED,
                reason="Destructive action requires confirmation before execution.",
                action=action,
            )

        return DecisionResult(
            status=DecisionStatus.AUTHORIZED,
            reason="Action is permitted by the safety policy.",
            action=action,
        )