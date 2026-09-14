"""Tests for DateRangeResolver (Phase 7 — PDMA-72).

Verifies deterministic conversion of semantic date-range expressions
into UTC-aware (start, end) datetime ranges.

All tests inject a fixed ``reference_time`` so results are deterministic.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from personal_deadline_management_agent.schemas.agent import DateRangeExpression
from personal_deadline_management_agent.services.date_range_resolver import (
    DateRangeResolver,
)

# Fixed reference time: Thursday 2026-09-10 15:30:00 UTC
# (2026-09-10 is a Thursday; weekday() == 3)
REF = datetime(2026, 9, 10, 15, 30, 0, tzinfo=timezone.utc)


def utc(y, mo, d, h=0, mi=0, s=0, us=0):
    return datetime(y, mo, d, h, mi, s, us, tzinfo=timezone.utc)


# ==========================================================================
# TODAY
# ==========================================================================


class TestToday:
    def test_today_full_day_range(self):
        start, end = DateRangeResolver.resolve(
            DateRangeExpression.TODAY, reference_time=REF
        )
        assert start == utc(2026, 9, 10, 0, 0, 0, 0)
        assert end == utc(2026, 9, 10, 23, 59, 59, 999999)

    def test_today_ignores_time_component_of_reference(self):
        late_ref = datetime(2026, 9, 10, 23, 59, 59, 999999, tzinfo=timezone.utc)
        start, end = DateRangeResolver.resolve(
            DateRangeExpression.TODAY, reference_time=late_ref
        )
        assert start == utc(2026, 9, 10, 0, 0, 0, 0)
        assert end == utc(2026, 9, 10, 23, 59, 59, 999999)


# ==========================================================================
# TOMORROW
# ==========================================================================


class TestTomorrow:
    def test_tomorrow_full_day_range(self):
        start, end = DateRangeResolver.resolve(
            DateRangeExpression.TOMORROW, reference_time=REF
        )
        assert start == utc(2026, 9, 11, 0, 0, 0, 0)
        assert end == utc(2026, 9, 11, 23, 59, 59, 999999)

    def test_tomorrow_handles_month_boundary(self):
        ref = datetime(2026, 9, 30, 10, 0, 0, tzinfo=timezone.utc)
        start, end = DateRangeResolver.resolve(
            DateRangeExpression.TOMORROW, reference_time=ref
        )
        assert start == utc(2026, 10, 1, 0, 0, 0, 0)
        assert end == utc(2026, 10, 1, 23, 59, 59, 999999)

    def test_tomorrow_handles_year_boundary(self):
        ref = datetime(2026, 12, 31, 10, 0, 0, tzinfo=timezone.utc)
        start, end = DateRangeResolver.resolve(
            DateRangeExpression.TOMORROW, reference_time=ref
        )
        assert start == utc(2027, 1, 1, 0, 0, 0, 0)
        assert end == utc(2027, 1, 1, 23, 59, 59, 999999)


# ==========================================================================
# THIS_WEEK (Monday start)
# ==========================================================================


class TestThisWeek:
    def test_this_week_monday_to_sunday(self):
        # 2026-09-10 is Thursday → week is Mon 09-07 → Sun 09-13
        start, end = DateRangeResolver.resolve(
            DateRangeExpression.THIS_WEEK, reference_time=REF
        )
        assert start == utc(2026, 9, 7, 0, 0, 0, 0)
        assert end == utc(2026, 9, 13, 23, 59, 59, 999999)

    def test_this_week_when_reference_is_monday(self):
        monday = datetime(2026, 9, 7, 8, 0, 0, tzinfo=timezone.utc)
        start, end = DateRangeResolver.resolve(
            DateRangeExpression.THIS_WEEK, reference_time=monday
        )
        assert start == utc(2026, 9, 7, 0, 0, 0, 0)
        assert end == utc(2026, 9, 13, 23, 59, 59, 999999)

    def test_this_week_when_reference_is_sunday(self):
        sunday = datetime(2026, 9, 13, 20, 0, 0, tzinfo=timezone.utc)
        start, end = DateRangeResolver.resolve(
            DateRangeExpression.THIS_WEEK, reference_time=sunday
        )
        assert start == utc(2026, 9, 7, 0, 0, 0, 0)
        assert end == utc(2026, 9, 13, 23, 59, 59, 999999)

    def test_this_week_crosses_month_boundary(self):
        # 2026-09-30 is Wednesday → week is Mon 09-28 → Sun 10-04
        ref = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)
        start, end = DateRangeResolver.resolve(
            DateRangeExpression.THIS_WEEK, reference_time=ref
        )
        assert start == utc(2026, 9, 28, 0, 0, 0, 0)
        assert end == utc(2026, 10, 4, 23, 59, 59, 999999)

    def test_this_week_crosses_year_boundary(self):
        # 2026-12-31 is Thursday → week is Mon 12-28 → Sun 01-03
        ref = datetime(2026, 12, 31, 12, 0, 0, tzinfo=timezone.utc)
        start, end = DateRangeResolver.resolve(
            DateRangeExpression.THIS_WEEK, reference_time=ref
        )
        assert start == utc(2026, 12, 28, 0, 0, 0, 0)
        assert end == utc(2027, 1, 3, 23, 59, 59, 999999)


# ==========================================================================
# NEXT_WEEK
# ==========================================================================


class TestNextWeek:
    def test_next_week_follows_current_week(self):
        # Current week Mon 09-07 → Sun 09-13; next week Mon 09-14 → Sun 09-20
        start, end = DateRangeResolver.resolve(
            DateRangeExpression.NEXT_WEEK, reference_time=REF
        )
        assert start == utc(2026, 9, 14, 0, 0, 0, 0)
        assert end == utc(2026, 9, 20, 23, 59, 59, 999999)

    def test_next_week_from_sunday(self):
        sunday = datetime(2026, 9, 13, 20, 0, 0, tzinfo=timezone.utc)
        start, end = DateRangeResolver.resolve(
            DateRangeExpression.NEXT_WEEK, reference_time=sunday
        )
        assert start == utc(2026, 9, 14, 0, 0, 0, 0)
        assert end == utc(2026, 9, 20, 23, 59, 59, 999999)

    def test_next_week_crosses_year_boundary(self):
        # 2026-12-31 is Thursday → next week Mon 01-04 → Sun 01-10
        ref = datetime(2026, 12, 31, 12, 0, 0, tzinfo=timezone.utc)
        start, end = DateRangeResolver.resolve(
            DateRangeExpression.NEXT_WEEK, reference_time=ref
        )
        assert start == utc(2027, 1, 4, 0, 0, 0, 0)
        assert end == utc(2027, 1, 10, 23, 59, 59, 999999)


# ==========================================================================
# THIS_MONTH
# ==========================================================================


class TestThisMonth:
    def test_this_month_first_to_last(self):
        # September has 30 days
        start, end = DateRangeResolver.resolve(
            DateRangeExpression.THIS_MONTH, reference_time=REF
        )
        assert start == utc(2026, 9, 1, 0, 0, 0, 0)
        assert end == utc(2026, 9, 30, 23, 59, 59, 999999)

    def test_this_month_31_day_month(self):
        ref = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        start, end = DateRangeResolver.resolve(
            DateRangeExpression.THIS_MONTH, reference_time=ref
        )
        assert start == utc(2026, 1, 1, 0, 0, 0, 0)
        assert end == utc(2026, 1, 31, 23, 59, 59, 999999)

    def test_this_month_february_non_leap(self):
        ref = datetime(2026, 2, 10, 10, 0, 0, tzinfo=timezone.utc)
        start, end = DateRangeResolver.resolve(
            DateRangeExpression.THIS_MONTH, reference_time=ref
        )
        assert start == utc(2026, 2, 1, 0, 0, 0, 0)
        assert end == utc(2026, 2, 28, 23, 59, 59, 999999)

    def test_this_month_february_leap_year(self):
        ref = datetime(2028, 2, 10, 10, 0, 0, tzinfo=timezone.utc)
        start, end = DateRangeResolver.resolve(
            DateRangeExpression.THIS_MONTH, reference_time=ref
        )
        assert start == utc(2028, 2, 1, 0, 0, 0, 0)
        assert end == utc(2028, 2, 29, 23, 59, 59, 999999)


# ==========================================================================
# NEXT_MONTH
# ==========================================================================


class TestNextMonth:
    def test_next_month_after_30_day_month(self):
        # September (30 days) → October (31 days)
        start, end = DateRangeResolver.resolve(
            DateRangeExpression.NEXT_MONTH, reference_time=REF
        )
        assert start == utc(2026, 10, 1, 0, 0, 0, 0)
        assert end == utc(2026, 10, 31, 23, 59, 59, 999999)

    def test_next_month_after_31_day_month(self):
        ref = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        start, end = DateRangeResolver.resolve(
            DateRangeExpression.NEXT_MONTH, reference_time=ref
        )
        assert start == utc(2026, 2, 1, 0, 0, 0, 0)
        assert end == utc(2026, 2, 28, 23, 59, 59, 999999)

    def test_next_month_february_leap_year(self):
        ref = datetime(2028, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        start, end = DateRangeResolver.resolve(
            DateRangeExpression.NEXT_MONTH, reference_time=ref
        )
        assert start == utc(2028, 2, 1, 0, 0, 0, 0)
        assert end == utc(2028, 2, 29, 23, 59, 59, 999999)

    def test_next_month_crosses_year_boundary(self):
        ref = datetime(2026, 12, 15, 10, 0, 0, tzinfo=timezone.utc)
        start, end = DateRangeResolver.resolve(
            DateRangeExpression.NEXT_MONTH, reference_time=ref
        )
        assert start == utc(2027, 1, 1, 0, 0, 0, 0)
        assert end == utc(2027, 1, 31, 23, 59, 59, 999999)


# ==========================================================================
# EXPLICIT_RANGE
# ==========================================================================


class TestExplicitRange:
    def test_explicit_range_passthrough(self):
        start_in = utc(2026, 9, 15, 9, 0, 0, 0)
        end_in = utc(2026, 9, 21, 17, 0, 0, 0)
        start, end = DateRangeResolver.resolve(
            DateRangeExpression.EXPLICIT_RANGE,
            explicit_start=start_in,
            explicit_end=end_in,
            reference_time=REF,
        )
        assert start == start_in
        assert end == end_in

    def test_explicit_range_normalizes_non_utc_to_utc(self):
        offset = timezone(timedelta(hours=7))
        start_in = datetime(2026, 9, 15, 9, 0, 0, tzinfo=offset)  # 02:00 UTC
        end_in = datetime(2026, 9, 21, 17, 0, 0, tzinfo=offset)  # 10:00 UTC
        start, end = DateRangeResolver.resolve(
            DateRangeExpression.EXPLICIT_RANGE,
            explicit_start=start_in,
            explicit_end=end_in,
            reference_time=REF,
        )
        assert start == utc(2026, 9, 15, 2, 0, 0, 0)
        assert end == utc(2026, 9, 21, 10, 0, 0, 0)

    def test_explicit_range_equal_start_end_allowed(self):
        same = utc(2026, 9, 15, 9, 0, 0, 0)
        start, end = DateRangeResolver.resolve(
            DateRangeExpression.EXPLICIT_RANGE,
            explicit_start=same,
            explicit_end=same,
            reference_time=REF,
        )
        assert start == same
        assert end == same

    def test_explicit_range_missing_start_raises(self):
        with pytest.raises(ValueError, match="EXPLICIT_RANGE requires both"):
            DateRangeResolver.resolve(
                DateRangeExpression.EXPLICIT_RANGE,
                explicit_start=None,
                explicit_end=utc(2026, 9, 21, 17, 0, 0, 0),
                reference_time=REF,
            )

    def test_explicit_range_missing_end_raises(self):
        with pytest.raises(ValueError, match="EXPLICIT_RANGE requires both"):
            DateRangeResolver.resolve(
                DateRangeExpression.EXPLICIT_RANGE,
                explicit_start=utc(2026, 9, 15, 9, 0, 0, 0),
                explicit_end=None,
                reference_time=REF,
            )

    def test_explicit_range_start_after_end_raises(self):
        with pytest.raises(ValueError, match="must be <= explicit_end"):
            DateRangeResolver.resolve(
                DateRangeExpression.EXPLICIT_RANGE,
                explicit_start=utc(2026, 9, 21, 17, 0, 0, 0),
                explicit_end=utc(2026, 9, 15, 9, 0, 0, 0),
                reference_time=REF,
            )

    def test_explicit_range_naive_start_raises(self):
        naive = datetime(2026, 9, 15, 9, 0, 0)
        with pytest.raises(ValueError, match="timezone-aware"):
            DateRangeResolver.resolve(
                DateRangeExpression.EXPLICIT_RANGE,
                explicit_start=naive,
                explicit_end=utc(2026, 9, 21, 17, 0, 0, 0),
                reference_time=REF,
            )

    def test_explicit_range_naive_end_raises(self):
        naive = datetime(2026, 9, 21, 17, 0, 0)
        with pytest.raises(ValueError, match="timezone-aware"):
            DateRangeResolver.resolve(
                DateRangeExpression.EXPLICIT_RANGE,
                explicit_start=utc(2026, 9, 15, 9, 0, 0, 0),
                explicit_end=naive,
                reference_time=REF,
            )


# ==========================================================================
# Invalid expression / reference_time
# ==========================================================================


class TestInvalidInput:
    def test_unsupported_expression_raises(self):
        with pytest.raises(ValueError, match="Unsupported expression"):
            DateRangeResolver.resolve("NOT_A_REAL_EXPRESSION", reference_time=REF)

    def test_naive_reference_time_raises(self):
        naive = datetime(2026, 9, 10, 15, 30, 0)
        with pytest.raises(ValueError, match="timezone-aware"):
            DateRangeResolver.resolve(
                DateRangeExpression.TODAY, reference_time=naive
            )

    def test_non_utc_reference_time_normalized(self):
        offset = timezone(timedelta(hours=7))
        ref = datetime(2026, 9, 10, 22, 30, 0, tzinfo=offset)  # 15:30 UTC
        start, end = DateRangeResolver.resolve(
            DateRangeExpression.TODAY, reference_time=ref
        )
        # Same calendar day in UTC (15:30 UTC is still Sep 10)
        assert start == utc(2026, 9, 10, 0, 0, 0, 0)
        assert end == utc(2026, 9, 10, 23, 59, 59, 999999)


# ==========================================================================
# Determinism
# ==========================================================================


class TestDeterminism:
    def test_same_input_same_output(self):
        args = dict(
            expression=DateRangeExpression.THIS_WEEK, reference_time=REF
        )
        r1 = DateRangeResolver.resolve(**args)
        r2 = DateRangeResolver.resolve(**args)
        assert r1 == r2

    def test_outputs_are_utc_aware(self):
        for expr in (
            DateRangeExpression.TODAY,
            DateRangeExpression.TOMORROW,
            DateRangeExpression.THIS_WEEK,
            DateRangeExpression.NEXT_WEEK,
            DateRangeExpression.THIS_MONTH,
            DateRangeExpression.NEXT_MONTH,
        ):
            start, end = DateRangeResolver.resolve(expr, reference_time=REF)
            assert start.tzinfo is not None
            assert end.tzinfo is not None
            assert start.utcoffset() == timedelta(0)
            assert end.utcoffset() == timedelta(0)

    def test_start_always_before_or_equal_end(self):
        for expr in (
            DateRangeExpression.TODAY,
            DateRangeExpression.TOMORROW,
            DateRangeExpression.THIS_WEEK,
            DateRangeExpression.NEXT_WEEK,
            DateRangeExpression.THIS_MONTH,
            DateRangeExpression.NEXT_MONTH,
        ):
            start, end = DateRangeResolver.resolve(expr, reference_time=REF)
            assert start <= end