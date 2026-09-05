"""Resource resolution service.

Resolves a validated ``ActionProposal``'s resource reference into a canonical
resource ID.  The structural validity of the proposal is assumed to have been
checked by :class:`..action_validator.ActionValidator` first; this service
performs no business validation and never executes actions.

```text
ResourceReference
  ├─ id (canonical UUID)        → used directly, no query
  └─ natural_language           → repository query
                                   ├─ 0 matches  → clarification
                                   ├─ 1 match    → resolved resource
                                   └─ many matches → clarification
```

No arbitrary fuzzy matching is performed — only the deterministic repository
queries below.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from ..schemas.agent import ActionProposal, ActionType
from .action_validator import ValidationStatus, ValidatedAction


class ResolutionResult(BaseModel):
    """Result of resolving a proposal's resource into a canonical ID."""

    status: ValidationStatus
    validated_action: ValidatedAction | None = None
    message: str = ""

    model_config = ConfigDict(populate_by_name=True)


# Actions whose target resource is a Task.
_TASK_RESOLVED_ACTIONS = {
    ActionType.UPDATE_TASK,
    ActionType.DELETE_TASK,
    ActionType.CREATE_REMINDER,  # parent task
}

# Actions whose target resource is a Reminder.
_REMINDER_RESOLVED_ACTIONS = {
    ActionType.UPDATE_REMINDER,
    ActionType.DELETE_REMINDER,
}


class ResourceResolver:
    """Resolves a proposal's resource reference to a canonical resource ID.

    Depends on task and reminder repositories for natural-language lookups.
    A fake implementation of each repository's lookup method may be injected
    in tests.
    """

    def __init__(self, *, task_repository, reminder_repository) -> None:
        self._tasks = task_repository
        self._reminders = reminder_repository

    def resolve(self, proposal: ActionProposal) -> ResolutionResult:
        if proposal.action_type == ActionType.CREATE_TASK:
            if proposal.resource is not None:
                return ResolutionResult(
                    status=ValidationStatus.CLARIFICATION_REQUIRED,
                    message="CREATE_TASK does not target an existing resource.",
                )
            return self._resolved(proposal, resource_id=None)

        if proposal.resource is None:
            return ResolutionResult(
                status=ValidationStatus.CLARIFICATION_REQUIRED,
                message="A target resource is required for this action.",
            )

        ref = proposal.resource
        if ref.id is not None:
            # A canonical UUID is authoritative — no query needed.
            return self._resolved(proposal, resource_id=ref.id)

        return self._resolve_natural_language(proposal)

    # ------------------------------------------------------------------

    def _resolve_natural_language(self, proposal: ActionProposal) -> ResolutionResult:
        phrase = proposal.resource.natural_language

        if proposal.action_type in _TASK_RESOLVED_ACTIONS:
            matches = self._tasks.find_by_name(phrase)
        elif proposal.action_type in _REMINDER_RESOLVED_ACTIONS:
            matches = self._reminders.find_by_task_name(phrase)
        else:
            return ResolutionResult(
                status=ValidationStatus.CLARIFICATION_REQUIRED,
                message="This action has no resolvable resource reference.",
            )

        if len(matches) == 0:
            return ResolutionResult(
                status=ValidationStatus.CLARIFICATION_REQUIRED,
                message="No resource matches that reference.",
            )
        if len(matches) > 1:
            return ResolutionResult(
                status=ValidationStatus.CLARIFICATION_REQUIRED,
                message="That reference matches more than one resource.",
            )

        return self._resolved(proposal, resource_id=matches[0].id)

    @staticmethod
    def _resolved(proposal: ActionProposal, *, resource_id) -> ResolutionResult:
        return ResolutionResult(
            status=ValidationStatus.VALID,
            validated_action=ValidatedAction(
                action_type=proposal.action_type,
                resource_id=resource_id,
                parameters=proposal.parameters,
            ),
        )
