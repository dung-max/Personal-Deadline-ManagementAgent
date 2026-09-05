"""Action validation service.

Validates the structural shape of an untrusted ``ActionProposal`` without
executing anything and without touching the database.  Resource *resolution*
(the mapping of a natural-language reference to a canonical ID) is handled by
:mod:`..resource_resolver`; this module only checks that a proposal is
well-formed enough to resolve and execute safely.

```text
ActionProposal (untrusted)
  ↓
ActionValidator.validate
  ↓
ValidationResult (VALID | CLARIFICATION_REQUIRED | REJECTED)
  ↓
ResourceResolver.resolve
  ↓
ValidatedAction (canonical, ready for execution)
```
"""

from __future__ import annotations

import enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..schemas.agent import ActionProposal, ActionType, ResourceReference


class ValidationStatus(str, enum.Enum):
    """Outcome of validating an ActionProposal."""

    VALID = "VALID"
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED"
    REJECTED = "REJECTED"


class ValidationResult(BaseModel):
    """Result of structural validation of an ActionProposal."""

    status: ValidationStatus
    message: str = ""

    model_config = ConfigDict(populate_by_name=True)


class ValidatedAction(BaseModel):
    """A validated, canonical action proposal ready for execution.

    Pure data — no DB access, no execution.  ``resource_id`` is ``None`` for
    CREATE_TASK, which has no target resource.  Parameters are carried through
    without LLM-driven business transformation.
    """

    action_type: ActionType
    resource_id: UUID | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(populate_by_name=True)


# Actions that act on an existing target resource.
_RESOURCE_REQUIRED_ACTIONS = {
    ActionType.UPDATE_TASK,
    ActionType.DELETE_TASK,
    ActionType.CREATE_REMINDER,
    ActionType.UPDATE_REMINDER,
    ActionType.DELETE_REMINDER,
}

# Minimal structural parameter requirements per action.  Only the presence of
# required keys is checked — business values (date ranges, priorities, etc.)
# are NOT validated here.
_REQUIRED_PARAMETERS: dict[ActionType, set[str]] = {
    ActionType.CREATE_TASK: {"taskName", "deadline"},
    ActionType.CREATE_REMINDER: {"remindAt"},
}

# Update actions must change at least one field.
_UPDATE_ACTIONS = {ActionType.UPDATE_TASK, ActionType.UPDATE_REMINDER}


class ActionValidator:
    """Validates the structural shape of an ``ActionProposal``."""

    def validate(self, proposal: ActionProposal) -> ValidationResult:
        # --- action type ---------------------------------------------------
        if not isinstance(proposal.action_type, ActionType):
            return ValidationResult(
                status=ValidationStatus.REJECTED,
                message="Unsupported action type.",
            )

        # --- resource requirement per action --------------------------------
        if proposal.action_type == ActionType.CREATE_TASK:
            if proposal.resource is not None:
                return ValidationResult(
                    status=ValidationStatus.CLARIFICATION_REQUIRED,
                    message="CREATE_TASK does not target an existing resource.",
                )
        elif proposal.resource is None:
            return ValidationResult(
                status=ValidationStatus.CLARIFICATION_REQUIRED,
                message="A target resource is required for this action.",
            )

        # --- resource reference form ----------------------------------------
        if proposal.resource is not None:
            if not self._has_usable_reference(proposal.resource):
                return ValidationResult(
                    status=ValidationStatus.CLARIFICATION_REQUIRED,
                    message="The resource reference is missing.",
                )

        # --- parameter structure --------------------------------------------
        missing = _REQUIRED_PARAMETERS.get(proposal.action_type, set()) - set(
            proposal.parameters
        )
        if missing:
            return ValidationResult(
                status=ValidationStatus.CLARIFICATION_REQUIRED,
                message="Missing required parameter(s): "
                f"{', '.join(sorted(missing))}.",
            )
        if proposal.action_type in _UPDATE_ACTIONS and not proposal.parameters:
            return ValidationResult(
                status=ValidationStatus.CLARIFICATION_REQUIRED,
                message="An update action must change at least one field.",
            )

        return ValidationResult(status=ValidationStatus.VALID)

    @staticmethod
    def _has_usable_reference(ref: ResourceReference) -> bool:
        """A reference is usable if it carries a canonical ID or a phrase."""
        return ref.id is not None or bool(ref.natural_language)
