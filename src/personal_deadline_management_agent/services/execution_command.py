"""Execution command contract.

A pure data object representing an action that has already passed validation,
resource resolution, authorization, and the safety policy.  It carries only the
canonical action type, the resolved resource ID, and the validated parameters —
no repository, session, or UoW objects; no ``execute()`` method.

```text
DecisionResult (AUTHORIZED)
  ↓
ExecutionCommand (pure data)
  ↓
ActionExecutor
  ↓
TaskModule / ReminderModule
```
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..guardrails.safety_policy import DecisionResult
from ..schemas.agent import ActionType


class ExecutionCommand(BaseModel):
    """An authorized action ready for execution.

    Pure data — it does NOT execute anything, does not carry database objects,
    and has no ``execute()`` or ``confirm()`` method.  It represents an action
    that has already passed validation and authorization.
    """

    action_type: ActionType
    resource_id: UUID | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(populate_by_name=True)

    @classmethod
    def from_decision(cls, decision: DecisionResult) -> ExecutionCommand:
        """Build the execution command from an authorized decision.

        This is the ONLY supported way to create an ExecutionCommand from the
        pipeline — the command is derived from ``decision.action`` (the
        ValidatedAction), never hand-assembled by callers.
        """
        action = decision.action
        return cls(
            action_type=action.action_type,
            resource_id=action.resource_id,
            parameters=action.parameters,
        )
