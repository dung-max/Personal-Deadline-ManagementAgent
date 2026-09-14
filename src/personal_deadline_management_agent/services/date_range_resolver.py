"""Deterministic date-range resolver.

Converts a semantic ``DateRangeExpression`` produced by the LLM into
an actual UTC-aware ``(start, end)`` datetime range.  The LLM is only
responsible for intent interpretation (``"this week"`` → ``THIS_WEEK``);
all calendar arithmetic lives here.

```text
DateRangeExpression + optional explicit dates + reference_time
  ↓
DateRangeResolver.resolve
  ↓
(start: datetime[UTC], end: datetime[UTC])
```

Design constraints:

- Pure deterministic logic — no LLM, no DB, no Scheduler.
- All output datetimes are timezone-aware UTC.
- ``reference_time`` is injectable for deterministic testing.
- When ``reference_time`` is ``None``, the current UTC time is used.
"""

from __future__ import annotations

import calendar
from datetime import datetime, timedelta, timezone

from ..schemas.agent import DateRangeExpression


class DateRangeResolver:
    """Resolves semantic date-range expressions to concrete UTC datetime ranges."""

    @staticmethod
    def resolve(
        expression: DateRangeExpression,
        *,
        explicit_start: datetime | None = None,
        explicit_end: datetime | None = None,
        reference_time: datetime | None = None,
    ) -> tuple[datetime, datetime]:
        """Resolve a date-range expression to (start, end) in UTC.

        Args:
            expression: Semantic keyword (TODAY, THIS_WEEK, etc.).
            explicit_start: Required only when ``expression`` is
                ``EXPLICIT_RANGE``.
            explicit_end: Required only when ``expression`` is
                ``EXPLICIT_RANGE``.
            reference_time: Injected UTC datetime for deterministic
                testing.  Defaults to ``datetime.now(timezone.utc)``.

        Returns:
            ``(start, end)`` both timezone-aware UTC.  ``end`` always
            represents the last inclusive microsecond of the range.

        Raises:
            ValueError: If ``EXPLICIT_RANGE`` is used without both dates,
                or if ``explicit_start > explicit_end``.
        """
        ref = reference_time or datetime.now(timezone.utc)
        ref = _ensure_utc(ref)

        if expression is DateRangeExpression.TODAY:
            return _same_day_range(ref)

        if expression is DateRangeExpression.TOMORROW:
            tomorrow = ref + timedelta(days=1)
            return _same_day_range(tomorrow)

        if expression is DateRangeExpression.THIS_WEEK:
            return _week_range(ref, weeks_offset=0)

        if expression is DateRangeExpression.NEXT_WEEK:
            return _week_range(ref, weeks_offset=1)

        if expression is DateRangeExpression.THIS_MONTH:
            return _month_range(ref, months_offset=0)

        if expression is DateRangeExpression.NEXT_MONTH:
            return _month_range(ref, months_offset=1)

        if expression is DateRangeExpression.EXPLICIT_RANGE:
            return _explicit_range(explicit_start, explicit_end)

        raise ValueError(f"Unsupported expression: {expression!r}")


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _ensure_utc(dt: datetime) -> datetime:
    """Normalise an aware datetime to UTC.  Raises for naive datetimes."""
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(
            "reference_time must be timezone-aware; "
            "naive datetimes are not accepted"
        )
    return dt.astimezone(timezone.utc)


def _day_start(dt: datetime) -> datetime:
    """Return 00:00:00.000000 UTC of the same calendar day."""
    return dt.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)


def _day_end(dt: datetime) -> datetime:
    """Return 23:59:59.999999 UTC of the same calendar day."""
    return dt.replace(hour=23, minute=59, second=59, microsecond=999999, tzinfo=timezone.utc)


def _same_day_range(dt: datetime) -> tuple[datetime, datetime]:
    """Full calendar day range for the day of *dt*."""
    return (_day_start(dt), _day_end(dt))


def _week_range(ref: datetime, *, weeks_offset: int) -> tuple[datetime, datetime]:
    """Monday 00:00 → Sunday 23:59:59.999999 UTC.

    Week starts on Monday (ISO 8601).
    ``weeks_offset=0`` → current week; ``weeks_offset=1`` → next week.
    """
    monday = ref - timedelta(days=ref.weekday()) + timedelta(weeks=weeks_offset)
    start = _day_start(monday)
    end = _day_end(monday + timedelta(days=6))
    return (start, end)


def _month_range(ref: datetime, *, months_offset: int) -> tuple[datetime, datetime]:
    """First day 00:00 → last day 23:59:59.999999 UTC.

    Handles February (including leap years), 30-day, and 31-day months
    correctly via ``calendar.monthrange``.
    """
    # Calculate target year/month
    total_months = ref.month - 1 + months_offset
    target_year = ref.year + total_months // 12
    target_month = total_months % 12 + 1

    # First day of target month
    start = datetime(target_year, target_month, 1, 0, 0, 0, 0, tzinfo=timezone.utc)

    # Last day of target month
    _last_day = calendar.monthrange(target_year, target_month)[1]
    end = datetime(target_year, target_month, _last_day, 23, 59, 59, 999999, tzinfo=timezone.utc)

    return (start, end)


def _explicit_range(
    start: datetime | None, end: datetime | None
) -> tuple[datetime, datetime]:
    """Passthrough for explicit start/end with validation."""
    if start is None or end is None:
        raise ValueError(
            "EXPLICIT_RANGE requires both explicit_start and explicit_end"
        )
    start = _ensure_utc(start)
    end = _ensure_utc(end)
    if start > end:
        raise ValueError(
            f"explicit_start ({start}) must be <= explicit_end ({end})"
        )
    return (start, end)
