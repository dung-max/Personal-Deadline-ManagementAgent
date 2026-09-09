"""Combined authorization + safety-policy decision boundary.

Composes :class:`..services.authorization_service.AuthorizationService` and
:class:`..safety_policy.SafetyPolicy` into the single decision consumed by the
Agent (and later the Direct API).  No execution, no DB access, no LLM calls.

```text
ValidatedAction
  ↓
AuthorizationService.authorize
  ↓
SafetyPolicy.evaluate
  ↓
DecisionResult
```
"""

from __future__ import annotations

from ..services.action_validator import ValidatedAction
from ..services.authorization_service import (
    AuthorizationContext,
    AuthorizationService,
    AuthorizationStatus,
)
from .safety_policy import DecisionResult, DecisionStatus, SafetyPolicy


class DecisionService:
    """Applies authorization first, then the safety policy."""

    def __init__(
        self,
        *,
        authorization_service: AuthorizationService,
        safety_policy: SafetyPolicy,
    ) -> None:
        self._authorization = authorization_service
        self._safety_policy = safety_policy

    def decide(
        self,
        action: ValidatedAction,
        context: AuthorizationContext | None,
    ) -> DecisionResult:
        authorization = self._authorization.authorize(action, context)
        if authorization.status is AuthorizationStatus.NOT_CONFIGURED:
            return DecisionResult(
                status=DecisionStatus.NOT_CONFIGURED,
                reason=authorization.reason,
                action=action,
            )
        if authorization.status is AuthorizationStatus.DENIED:
            return DecisionResult(
                status=DecisionStatus.DENIED,
                reason=authorization.reason,
                action=action,
            )
        return self._safety_policy.evaluate(action)