"""Authorization service.

Answers "can this actor perform this validated action?" after validation and
resource resolution.  The MVP has no user/account/ownership model, task
ownership model, reminder ownership model, or authentication system.  Because
resource authorization cannot yet be verified, this service reports that the
boundary is NOT_CONFIGURED instead of inventing a fake authorization mechanism.

```text
ValidatedAction
  ↓
AuthorizationService.authorize
  ↓
AuthorizationResult (AUTHORIZED | DENIED | NOT_CONFIGURED)
```
"""

from __future__ import annotations

import enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from .action_validator import ValidatedAction


class AuthorizationContext(BaseModel):
    """Minimal actor identity for the MVP authorization boundary."""

    actor_id: UUID

    model_config = ConfigDict(populate_by_name=True)


class AuthorizationStatus(str, enum.Enum):
    """Outcome of an authorization check."""

    AUTHORIZED = "AUTHORIZED"
    DENIED = "DENIED"
    NOT_CONFIGURED = "NOT_CONFIGURED"


class AuthorizationResult(BaseModel):
    """Result of an authorization check for a validated action."""

    status: AuthorizationStatus
    reason: str = ""
    action: ValidatedAction

    model_config = ConfigDict(populate_by_name=True)


class AuthorizationService:
    """Deterministic application-level authorization boundary.

    TODO(MVP): This is a temporary single-user authorization implementation.
    The MVP has no user/account model, task ownership model, reminder ownership
    model, or authentication system.  When a real multi-user system is needed,
    replace this service with one backed by an ownership model — the rest of
    the pipeline (SafetyPolicy, DecisionService, ActionExecutor) stays unchanged.

    Current behavior:
    - ``single_user_mode=True``  → every request is AUTHORIZED (skip check).
    - ``default_user_id`` matches the actor → AUTHORIZED.
    - Otherwise → NOT_CONFIGURED (cannot verify ownership).
    """

    # TODO(MVP): replace with real ownership/permission model for multi-user.
    _LOCAL_DEV_DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"

    def __init__(
        self,
        *,
        default_user_id: str = _LOCAL_DEV_DEFAULT_USER_ID,
        single_user_mode: bool = False,
    ) -> None:
        self._default_user_id = default_user_id
        self._single_user_mode = single_user_mode

    def authorize(
        self,
        action: ValidatedAction,
        context: AuthorizationContext | None,
    ) -> AuthorizationResult:
        """Determine whether the actor may perform the validated action.

        In single-user mode all requests are authorized.  When a real
        ownership model exists this method should verify that *context*
        actually owns the target resource.
        """
        # TODO(MVP): skip check entirely when SINGLE_USER_MODE is enabled.
        if self._single_user_mode:
            return AuthorizationResult(
                status=AuthorizationStatus.AUTHORIZED,
                reason=(
                    "Single-user mode is enabled; all requests are authorized."
                ),
                action=action,
            )

        # TODO(MVP): replace with real ownership check once user/account and
        # task/reminder ownership models exist.
        if (
            isinstance(context, AuthorizationContext)
            and self._default_user_id
            and str(context.actor_id) == self._default_user_id
        ):
            return AuthorizationResult(
                status=AuthorizationStatus.AUTHORIZED,
                reason=(
                    "Actor matches the configured default user."
                ),
                action=action,
            )

        return AuthorizationResult(
            status=AuthorizationStatus.NOT_CONFIGURED,
            reason=(
                "Authorization is not configured: the MVP has no user/account "
                "model, task ownership model, reminder ownership model, or "
                "authentication system, so resource ownership cannot be verified."
            ),
            action=action,
        )