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
from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ..schemas.agent import (
    ActionProposal,
    ActionType,
    DateRangeExpression,
    ResourceReference,
)
from ..utils.datetime_utils import parse_iso_datetime, require_aware_utc


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

# Actions that never target an existing resource.
_NO_RESOURCE_ACTIONS = {
    ActionType.CREATE_TASK,
    ActionType.ANALYZE_WORKLOAD,
}

# Minimal structural parameter requirements per action.  Only the presence of
# required keys is checked — business values (date ranges, priorities, etc.)
# are NOT validated here.
_REQUIRED_PARAMETERS: dict[ActionType, set[str]] = {
    ActionType.CREATE_TASK: {"taskName", "deadline"},
    ActionType.CREATE_REMINDER: {"remindAt"},
    ActionType.ANALYZE_WORKLOAD: {"date_range_expression"},
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
        if proposal.action_type in _NO_RESOURCE_ACTIONS:
            if proposal.resource is not None:
                return ValidationResult(
                    status=ValidationStatus.CLARIFICATION_REQUIRED,
                    message=(
                        f"{proposal.action_type.value} does not target an "
                        "existing resource."
                    ),
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

        # --- ANALYZE_WORKLOAD semantic validation ---------------------------
        if proposal.action_type == ActionType.ANALYZE_WORKLOAD:
            return self._validate_analyze_workload(proposal.parameters)

        return ValidationResult(status=ValidationStatus.VALID)

    @staticmethod
    def _validate_analyze_workload(parameters: dict[str, Any]) -> ValidationResult:
        """Validate ANALYZE_WORKLOAD parameters (semantic, no date arithmetic).

        Only checks that the expression is a known ``DateRangeExpression`` and
        that ``EXPLICIT_RANGE`` carries both explicit dates with ``start <= end``.
        Actual date-boundary calculation is owned by ``DateRangeResolver``.
        """
        expression = parameters.get("date_range_expression")

        if not isinstance(expression, DateRangeExpression):
            try:
                expression = DateRangeExpression(expression)
            except (ValueError, TypeError):
                return ValidationResult(
                    status=ValidationStatus.CLARIFICATION_REQUIRED,
                    message=(
                        "A valid date range is required for workload analysis "
                        f"(got {expression!r})."
                    ),
                )

        if expression is DateRangeExpression.EXPLICIT_RANGE:
            explicit_start = parameters.get("explicit_start")
            explicit_end = parameters.get("explicit_end")

            if explicit_start is None or explicit_end is None:
                return ValidationResult(
                    status=ValidationStatus.CLARIFICATION_REQUIRED,
                    message=(
                        "EXPLICIT_RANGE requires both explicit_start and "
                        "explicit_end."
                    ),
                )

            try:
                start = parse_iso_datetime(explicit_start)
                end = parse_iso_datetime(explicit_end)
            except (ValueError, TypeError) as exc:
                return ValidationResult(
                    status=ValidationStatus.CLARIFICATION_REQUIRED,
                    message=f"Invalid explicit date: {exc}",
                )

            if start > end:
                return ValidationResult(
                    status=ValidationStatus.CLARIFICATION_REQUIRED,
                    message="explicit_start must be <= explicit_end.",
                )

        return ValidationResult(status=ValidationStatus.VALID)

    @staticmethod
    def _has_usable_reference(ref: ResourceReference) -> bool:
        """A reference is usable if it carries a canonical ID or a phrase."""
        return ref.id is not None or bool(ref.natural_language)
