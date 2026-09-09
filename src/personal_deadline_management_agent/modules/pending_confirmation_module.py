"""Pending confirmation module.

Application use-case orchestration layer for pending-confirmation operations.
Owns commit/rollback for write use cases using UnitOfWork.

```text
ExecutionCommand (pending confirm)
  ↓
PendingConfirmationModule.create
  ↓
PendingConfirmationRepository
  ↓
UoW
  ↓
Database
```
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from uuid import UUID

from ..models import PendingConfirmation, PendingConfirmationStatus
from ..services.execution_command import ExecutionCommand
from ..uow import UnitOfWork


class ConfirmOutcome(str, Enum):
    """Why a confirm() attempt ended the way it did.

    Distinguishes a fresh transition (CONFIRMED) from the cases where the
    caller must NOT treat the record as confirmed: it was already processed
    (ALREADY_CONFIRMED), expired, cancelled, or never existed / belongs to
    another user (NOT_FOUND).
    """

    CONFIRMED = "CONFIRMED"
    ALREADY_CONFIRMED = "ALREADY_CONFIRMED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    NOT_FOUND = "NOT_FOUND"


@dataclass(frozen=True)
class ConfirmResult:
    """Result of a confirm() attempt.

    ``confirmation`` carries the entity when the record exists and belongs
    to the caller — for CONFIRMED it holds the stored execution_command the
    handler needs to execute.
    """

    outcome: ConfirmOutcome
    confirmation: PendingConfirmation | None = None


class PendingConfirmationModule:
    """Manages the lifecycle of pending confirmations.

    Creates, confirms, expires, and cleans up confirmation records.  Every
    write operation commits through UnitOfWork; every read operation is
    transaction-free.
    """

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def create(
        self,
        *,
        user_id: UUID | None,
        command: ExecutionCommand,
        ttl_seconds: int,
    ) -> PendingConfirmation:
        """Persist a new pending confirmation and commit."""
        now = datetime.now(timezone.utc)
        confirmation = PendingConfirmation(
            user_id=user_id,
            execution_command=command.model_dump_json(),
            created_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
            status=PendingConfirmationStatus.PENDING.value,
        )
        try:
            self._uow.pending_confirmations.create(confirmation)
            self._uow.commit()
            return confirmation
        except Exception:
            self._uow.rollback()
            raise

    def get_latest_pending(
        self, user_id: UUID | None
    ) -> PendingConfirmation | None:
        """Return the most recent PENDING confirmation for a user (read-only)."""
        return self._uow.pending_confirmations.find_latest_pending_by_user(
            user_id
        )

    def confirm(
        self, confirmation_id: UUID, user_id: UUID | None
    ) -> ConfirmResult:
        """Validate and atomically transition a pending confirmation to CONFIRMED.

        Uses a conditional UPDATE (WHERE status='PENDING' + user + expiry) so
        that exactly one concurrent caller wins the transition — the same
        optimistic-locking pattern applied by the Scheduler.  The result
        outcome always distinguishes the reason the transition failed, so the
        handler can show a different message for 'already processed' vs 'not
        found' vs 'expired' without ever calling ActionExecutor.execute()
        twice.
        """
        now = datetime.now(timezone.utc)

        # --- Atomic conditional update (single SQL round-trip) ---------------
        updated = self._uow.pending_confirmations.mark_confirmed(
            confirmation_id, user_id, now
        )

        if updated == 1:
            # We won the transition — reload the entity (bulk UPDATE bypasses
            # the session identity map, so a stale object could be in memory).
            confirmation = self._uow.pending_confirmations.get_by_id(
                confirmation_id
            )
            self._uow.pending_confirmations.refresh(confirmation)
            try:
                self._uow.commit()
            except Exception:
                self._uow.rollback()
                raise
            return ConfirmResult(
                outcome=ConfirmOutcome.CONFIRMED, confirmation=confirmation
            )

        # --- Zero rows matched — diagnose the reason ------------------------

        confirmation = self._uow.pending_confirmations.get_by_id(
            confirmation_id
        )

        if confirmation is None or confirmation.user_id != user_id:
            return ConfirmResult(outcome=ConfirmOutcome.NOT_FOUND)

        if confirmation.status == PendingConfirmationStatus.CONFIRMED.value:
            return ConfirmResult(
                outcome=ConfirmOutcome.ALREADY_CONFIRMED,
                confirmation=confirmation,
            )

        if confirmation.status == PendingConfirmationStatus.CANCELLED.value:
            return ConfirmResult(
                outcome=ConfirmOutcome.CANCELLED, confirmation=confirmation
            )

        if confirmation.status == PendingConfirmationStatus.EXPIRED.value:
            return ConfirmResult(
                outcome=ConfirmOutcome.EXPIRED, confirmation=confirmation
            )

        # Still PENDING — the conditional update only fails for an expired
        # row (id + user + status all matched but expires_at <= now), so mark
        # it EXPIRED to keep the record consistent.
        confirmation.status = PendingConfirmationStatus.EXPIRED.value
        try:
            self._uow.commit()
        except Exception:
            self._uow.rollback()
            raise
        return ConfirmResult(
            outcome=ConfirmOutcome.EXPIRED, confirmation=confirmation
        )

    def cleanup_expired(self) -> int:
        """Mark all expired PENDING confirmations as EXPIRED and commit.

        Runs once at the start of each Agent chat request.  Returns the
        number of records marked.
        """
        count = self._uow.pending_confirmations.mark_expired(
            datetime.now(timezone.utc)
        )
        if count:
            try:
                self._uow.commit()
            except Exception:
                self._uow.rollback()
                raise
        return count
