"""Shared datetime policy helpers.

Application-wide timezone policy (Phase 4.9):

- All persisted Task.deadline / Reminder.remind_at values are timezone-aware.
- Internal comparisons and storage use UTC-aware datetimes.
- Naive datetimes are **rejected**, never silently assumed to be UTC.
- PostgreSQL continues using TIMESTAMPTZ (no schema changes).
- No user timezone preference is introduced (MVP has no User/Account model).

API/structured input must provide timezone-aware ISO-8601 datetimes.
The functions in this module are the single enforcement point shared by the
Direct API (via Pydantic field validators) and the Agent path (via
ActionExecutor datetime coercion).
"""

from __future__ import annotations

from datetime import datetime, timezone


class NaiveDateTimeError(ValueError):
    """Raised when a naive datetime is supplied where an aware one is required.

    This is the canonical rejection type for naive datetime input.  Callers
    may map this exception to an appropriate domain error (e.g. INVALID_INPUT).
    """


def require_aware_utc(value: datetime) -> datetime:
    """Reject naive datetimes; normalize aware datetimes to UTC.

    Preserves the represented instant: ``2026-10-01T19:00:00+07:00`` becomes
    ``2026-10-01T12:00:00+00:00``.

    Raises:
        NaiveDateTimeError: if ``value`` is a naive (tzinfo-less) datetime.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise NaiveDateTimeError(
            "datetime must be timezone-aware "
            "(e.g. '2026-10-01T12:00:00+07:00' or '2026-10-01T12:00:00Z'); "
            "naive datetimes are not accepted"
        )
    return value.astimezone(timezone.utc)


def parse_iso_datetime(value: str) -> datetime:
    """Parse an ISO-8601 string into an aware UTC datetime.

    Handles the trailing-``Z`` notation (equivalent to ``+00:00``) by
    normalizing before parsing for maximum compatibility.

    Raises:
        NaiveDateTimeError: if the string parses to a naive datetime.
        ValueError: if the string is not a valid ISO-8601 datetime.
    """
    # ``Z`` (Zulu / UTC) is handled explicitly because some parsers accept it
    # differently; explicit replacement keeps behaviour deterministic.
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    dt = datetime.fromisoformat(text)
    return require_aware_utc(dt)
