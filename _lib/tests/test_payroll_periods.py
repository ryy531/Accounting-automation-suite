"""Tests for payroll_periods.py -- pure date calculations, no mocks."""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.payroll_periods import (
    FREQ_MONTHLY, FREQ_SEMI_MONTHLY, FREQ_BI_WEEKLY,
    FREQ_QUARTERLY, FREQ_YEARLY,
    backfill_due_periods, calculate_period_start,
    format_engagement_name, next_due_period_end,
)

import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# next_due_period_end
# ═══════════════════════════════════════════════════════════════════════════════

class TestNextDueMonthly:
    def test_mid_month(self):
        assert next_due_period_end(FREQ_MONTHLY, date(2026, 5, 7)) == date(2026, 4, 30)

    def test_first_of_month(self):
        assert next_due_period_end(FREQ_MONTHLY, date(2026, 5, 1)) == date(2026, 4, 30)

    def test_month_end(self):
        assert next_due_period_end(FREQ_MONTHLY, date(2026, 5, 31)) == date(2026, 5, 31)

    def test_first_of_next_month(self):
        assert next_due_period_end(FREQ_MONTHLY, date(2026, 6, 1)) == date(2026, 5, 31)

    def test_february_leap(self):
        assert next_due_period_end(FREQ_MONTHLY, date(2028, 3, 1)) == date(2028, 2, 29)

    def test_february_non_leap(self):
        assert next_due_period_end(FREQ_MONTHLY, date(2027, 3, 1)) == date(2027, 2, 28)

    def test_december_to_january(self):
        assert next_due_period_end(FREQ_MONTHLY, date(2027, 1, 5)) == date(2026, 12, 31)


class TestNextDueSemiMonthly:
    def test_before_15(self):
        assert next_due_period_end(FREQ_SEMI_MONTHLY, date(2026, 5, 7)) == date(2026, 4, 30)

    def test_on_15(self):
        assert next_due_period_end(FREQ_SEMI_MONTHLY, date(2026, 5, 15)) == date(2026, 5, 15)

    def test_after_15(self):
        assert next_due_period_end(FREQ_SEMI_MONTHLY, date(2026, 5, 16)) == date(2026, 5, 15)

    def test_month_end(self):
        assert next_due_period_end(FREQ_SEMI_MONTHLY, date(2026, 5, 31)) == date(2026, 5, 31)

    def test_february_28(self):
        assert next_due_period_end(FREQ_SEMI_MONTHLY, date(2025, 2, 28)) == date(2025, 2, 28)

    def test_february_leap(self):
        assert next_due_period_end(FREQ_SEMI_MONTHLY, date(2024, 2, 29)) == date(2024, 2, 29)


class TestNextDueBiWeekly:
    def test_anchor_in_past(self):
        assert next_due_period_end(FREQ_BI_WEEKLY, date(2026, 5, 8),
                                   date(2026, 4, 24)) == date(2026, 5, 8)

    def test_crosses_month(self):
        assert next_due_period_end(FREQ_BI_WEEKLY, date(2026, 5, 22),
                                   date(2026, 4, 24)) == date(2026, 5, 22)

    def test_no_anchor(self):
        assert next_due_period_end(FREQ_BI_WEEKLY, date(2026, 5, 8), None) is None

    def test_anchor_after_today_steps_back(self):
        assert next_due_period_end(FREQ_BI_WEEKLY, date(2026, 5, 8),
                                   date(2026, 5, 15)) == date(2026, 5, 1)

    def test_anchor_near_future(self):
        assert next_due_period_end(FREQ_BI_WEEKLY, date(2026, 5, 7),
                                   date(2026, 5, 9)) == date(2026, 4, 25)

    def test_anchor_equals_today(self):
        assert next_due_period_end(FREQ_BI_WEEKLY, date(2026, 5, 8),
                                   date(2026, 5, 8)) == date(2026, 5, 8)

    def test_anchor_in_past_exact_match(self):
        assert next_due_period_end(FREQ_BI_WEEKLY, date(2026, 5, 7),
                                   date(2026, 4, 25)) == date(2026, 4, 25)

    def test_anchor_in_future_steps_back(self):
        assert next_due_period_end(FREQ_BI_WEEKLY, date(2026, 5, 7),
                                   date(2026, 5, 9)) == date(2026, 4, 25)

    def test_anchor_equals_today_may9(self):
        assert next_due_period_end(FREQ_BI_WEEKLY, date(2026, 5, 9),
                                   date(2026, 5, 9)) == date(2026, 5, 9)


class TestNextDueQuarterly:
    def test_q2_mid(self):
        assert next_due_period_end(FREQ_QUARTERLY, date(2026, 5, 7)) == date(2026, 3, 31)

    def test_q2_end(self):
        assert next_due_period_end(FREQ_QUARTERLY, date(2026, 6, 30)) == date(2026, 6, 30)

    def test_q4(self):
        assert next_due_period_end(FREQ_QUARTERLY, date(2026, 11, 7)) == date(2026, 9, 30)

    def test_q1_jan(self):
        assert next_due_period_end(FREQ_QUARTERLY, date(2026, 1, 15)) == date(2025, 12, 31)

    def test_q1_end(self):
        assert next_due_period_end(FREQ_QUARTERLY, date(2026, 3, 31)) == date(2026, 3, 31)


class TestNextDueYearly:
    def test_mid(self):
        assert next_due_period_end(FREQ_YEARLY, date(2026, 5, 7)) == date(2025, 12, 31)

    def test_end(self):
        assert next_due_period_end(FREQ_YEARLY, date(2026, 12, 31)) == date(2026, 12, 31)

    def test_jan_1(self):
        assert next_due_period_end(FREQ_YEARLY, date(2027, 1, 1)) == date(2026, 12, 31)


class TestNextDueUnknown:
    def test_raises(self):
        with pytest.raises(ValueError, match="Unknown frequency"):
            next_due_period_end("Weekly", date(2026, 5, 7))


# ═══════════════════════════════════════════════════════════════════════════════
# calculate_period_start
# ═══════════════════════════════════════════════════════════════════════════════

class TestPeriodStart:
    def test_monthly(self):
        assert calculate_period_start(date(2026, 5, 31), FREQ_MONTHLY) == date(2026, 5, 1)

    def test_semi_monthly_first_half(self):
        assert calculate_period_start(date(2026, 5, 15), FREQ_SEMI_MONTHLY) == date(2026, 5, 1)

    def test_semi_monthly_second_half(self):
        assert calculate_period_start(date(2026, 5, 31), FREQ_SEMI_MONTHLY) == date(2026, 5, 16)

    def test_bi_weekly(self):
        assert calculate_period_start(date(2026, 5, 8), FREQ_BI_WEEKLY) == date(2026, 4, 25)

    def test_quarterly(self):
        assert calculate_period_start(date(2026, 6, 30), FREQ_QUARTERLY) == date(2026, 4, 1)

    def test_yearly(self):
        assert calculate_period_start(date(2026, 12, 31), FREQ_YEARLY) == date(2026, 1, 1)


# ═══════════════════════════════════════════════════════════════════════════════
# format_engagement_name
# ═══════════════════════════════════════════════════════════════════════════════

class TestFormatName:
    def test_basic(self):
        result = format_engagement_name("Acme Corp", date(2026, 5, 31))
        assert result == "Acme Corp 2026-05-31 Payroll"


# ═══════════════════════════════════════════════════════════════════════════════
# backfill_due_periods
# ═══════════════════════════════════════════════════════════════════════════════

class TestBackfill:
    def test_monthly_3_periods(self):
        result = backfill_due_periods(FREQ_MONTHLY, date(2026, 5, 31), None)
        assert result == [date(2026, 3, 31), date(2026, 4, 30), date(2026, 5, 31)]

    def test_bi_weekly(self):
        result = backfill_due_periods(FREQ_BI_WEEKLY, date(2026, 5, 22),
                                      date(2026, 4, 10))
        assert result == [date(2026, 4, 24), date(2026, 5, 8), date(2026, 5, 22)]

    def test_no_due(self):
        result = backfill_due_periods(FREQ_BI_WEEKLY, date(2026, 5, 8), None)
        assert result == []

    def test_quarterly(self):
        result = backfill_due_periods(FREQ_QUARTERLY, date(2026, 6, 30), None)
        assert result == [date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30)]
