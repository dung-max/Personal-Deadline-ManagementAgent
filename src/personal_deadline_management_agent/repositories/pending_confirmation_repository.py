"""Pending confirmation repository.

Persistence layer for PendingConfirmation entities.  Transaction ownership
remains with UnitOfWork / PendingConfirmationModule; this repository does
NOT commit or rollback.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..models import PendingConfirmation, PendingConfirmationStatus


class PendingConfirmationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, confirmation: PendingConfirmation) -> PendingConfirmation:
        self._session.add(confirmation)
        self._session.flush()
        self._session.refresh(confirmation)
        return confirmation

    def get_by_id(self, confirmation_id: UUID) -> PendingConfirmation | None:
        return self._session.get(PendingConfirmation, confirmation_id)

    def find_latest_pending_by_user(
        self, user_id: UUID | None
    ) -> PendingConfirmation | None:
        """Return the most recent PENDING confirmation for a user.

        ``user_id=None`` matches rows where user_id IS NULL (single-user
        mode with no auth context).
        """
        stmt = (
            select(PendingConfirmation)
            .where(
                PendingConfirmation.user_id == user_id,
                PendingConfirmation.status
                == PendingConfirmationStatus.PENDING.value,
            )
            .order_by(
                PendingConfirmation.created_at.desc(),
                PendingConfirmation.id.desc(),
            )
            .limit(1)
        )
        return self._session.scalars(stmt).first()

    def mark_expired(self, now: datetime) -> int:
        """Mark all PENDING confirmations that have passed their TTL.

        Returns the number of rows updated.
        """
        stmt = (
            update(PendingConfirmation)
            .where(
                PendingConfirmation.status
                == PendingConfirmationStatus.PENDING.value,
                PendingConfirmation.expires_at < now,
            )
            .values(status=PendingConfirmationStatus.EXPIRED.value)
        )
        result = self._session.execute(stmt)
        return result.rowcount or 0

    def mark_confirmed(
        self, confirmation_id: UUID, user_id: UUID | None, now: datetime
    ) -> int:
        """Atomically transition a PENDING confirmation to CONFIRMED.

        The WHERE clause (id + user + status='PENDING' + not expired) makes
        the update conditional: exactly one concurrent caller wins, mirroring
        the optimistic-locking pattern used by the Scheduler.  Returns the
        number of rows updated (0 or 1).
        """
        stmt = (
            update(PendingConfirmation)
            .where(
                PendingConfirmation.id == confirmation_id,
                PendingConfirmation.user_id == user_id,
                PendingConfirmation.status
                == PendingConfirmationStatus.PENDING.value,
                PendingConfirmation.expires_at > now,
            )
            .values(status=PendingConfirmationStatus.CONFIRMED.value)
        )
        result = self._session.execute(stmt)
        return result.rowcount or 0

    def refresh(self, confirmation: PendingConfirmation) -> PendingConfirmation:
        """Reload an entity from the database (bulk UPDATEs bypass the
        session identity map, so a previously loaded object can be stale)."""
        self._session.refresh(confirmation)
        return confirmation
