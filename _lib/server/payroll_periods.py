"""
payroll_periods.py -- Pure payroll period date calculations.

Zero I/O, zero external deps (stdlib only). Fully unit-testable.
"""

import calendar
from datetime import date, timedelta

FREQ_MONTHLY      = "Monthly"
FREQ_SEMI_MONTHLY = "Semi-monthly"
FREQ_BI_WEEKLY    = "Bi-weekly"
FREQ_QUARTERLY    = "Quarterly"
FREQ_YEARLY       = "Yearly"

VALID_FREQUENCIES = {FREQ_MONTHLY, FREQ_SEMI_MONTHLY, FREQ_BI_WEEKLY,
                     FREQ_QUARTERLY, FREQ_YEARLY}


# ── Internal helpers ─────────────────────────────────────────────────────────

def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _quarter_end(year: int, quarter: int) -> date:
    return _month_end(year, quarter * 3)


def _quarter_of(month: int) -> int:
    return (month - 1) // 3 + 1


def _prev_month(year: int, month: int) -> tuple[int, int]:
    if month == 1:
        return year - 1, 12
    return year, month - 1


# ── Public API ────────────────────────────────────────────────────────────────

def next_due_period_end(frequency: str, today: date,
                        last_period_end: date | None = None) -> date | None:
    """Return the most recent period-end that is <= today (i.e. "due").

    For Bi-Weekly, *last_period_end* is the anchor (latest engagement period-end
    or parsed from Pay Period End Rule).  Returns None when there is no due
    period (e.g. anchor is in the future, or Bi-Weekly with no anchor).
    """
    if frequency == FREQ_MONTHLY:
        cur = _month_end(today.year, today.month)
        if cur <= today:
            return cur
        py, pm = _prev_month(today.year, today.month)
        return _month_end(py, pm)

    if frequency == FREQ_SEMI_MONTHLY:
        candidates = [
            date(today.year, today.month, 15),
            _month_end(today.year, today.month),
        ]
        py, pm = _prev_month(today.year, today.month)
        candidates.append(_month_end(py, pm))
        due = [c for c in candidates if c <= today]
        return max(due) if due else None

    if frequency == FREQ_BI_WEEKLY:
        if last_period_end is None:
            return None
        pe = last_period_end
        # Step back until <= today
        while pe > today:
            pe -= timedelta(days=14)
        # Step forward to find the largest <= today
        while pe + timedelta(days=14) <= today:
            pe += timedelta(days=14)
        return pe

    if frequency == FREQ_QUARTERLY:
        q = _quarter_of(today.month)
        cur = _quarter_end(today.year, q)
        if cur <= today:
            return cur
        pq = q - 1
        py = today.year
        if pq == 0:
            pq, py = 4, py - 1
        return _quarter_end(py, pq)

    if frequency == FREQ_YEARLY:
        cur = date(today.year, 12, 31)
        if cur <= today:
            return cur
        return date(today.year - 1, 12, 31)

    raise ValueError(f"Unknown frequency: {frequency}")


def calculate_period_start(period_end: date, frequency: str) -> date:
    """Derive period-start from period-end for a given frequency."""
    if frequency == FREQ_MONTHLY:
        return date(period_end.year, period_end.month, 1)

    if frequency == FREQ_SEMI_MONTHLY:
        if period_end.day == 15:
            return date(period_end.year, period_end.month, 1)
        return date(period_end.year, period_end.month, 16)

    if frequency == FREQ_BI_WEEKLY:
        return period_end - timedelta(days=13)

    if frequency == FREQ_QUARTERLY:
        q = _quarter_of(period_end.month)
        return date(period_end.year, (q - 1) * 3 + 1, 1)

    if frequency == FREQ_YEARLY:
        return date(period_end.year, 1, 1)

    raise ValueError(f"Unknown frequency: {frequency}")


def format_engagement_name(legal_name: str, period_end: date) -> str:
    return f"{legal_name} {period_end:%Y-%m-%d} Payroll"


def _previous_period_end(period_end: date, frequency: str) -> date:
    """Step backwards one period from the given period-end."""
    if frequency == FREQ_MONTHLY:
        py, pm = _prev_month(period_end.year, period_end.month)
        return _month_end(py, pm)

    if frequency == FREQ_SEMI_MONTHLY:
        if period_end.day == 15:
            py, pm = _prev_month(period_end.year, period_end.month)
            return _month_end(py, pm)
        return date(period_end.year, period_end.month, 15)

    if frequency == FREQ_BI_WEEKLY:
        return period_end - timedelta(days=14)

    if frequency == FREQ_QUARTERLY:
        q = _quarter_of(period_end.month)
        pq = q - 1
        py = period_end.year
        if pq == 0:
            pq, py = 4, py - 1
        return _quarter_end(py, pq)

    if frequency == FREQ_YEARLY:
        return date(period_end.year - 1, 12, 31)

    raise ValueError(f"Unknown frequency: {frequency}")


def backfill_due_periods(frequency: str, today: date,
                         last_period_end: date | None,
                         max_lookback: int = 3) -> list[date]:
    """Return up to *max_lookback* due period-ends (old → new).

    Covers the case where the cron missed a few runs: creates multiple
    engagements in one pass.
    """
    most_recent = next_due_period_end(frequency, today, last_period_end)
    if most_recent is None:
        return []

    periods = [most_recent]
    cur = most_recent
    while len(periods) < max_lookback:
        cur = _previous_period_end(cur, frequency)
        periods.append(cur)

    periods.reverse()  # old → new
    return periods


def upcoming_due_periods(frequency: str, today: date,
                         last_period_end: date | None,
                         lookahead_days: int = 7) -> list[date]:
    """Return all period-ends within the next *lookahead_days* days.

    For daily prepare cron: find which periods will need preparation soon.
    Returns dates sorted ascending (near → far).

    Returns empty list if:
    - No anchor available (Bi-weekly with no last_period_end)
    - No periods within lookahead window
    """
    if frequency == FREQ_MONTHLY:
        candidates = []
        # Check this month and next 2 months (should cover 7 days)
        for month_offset in range(3):
            target_month = today.month + month_offset
            target_year = today.year
            if target_month > 12:
                target_year += 1
                target_month -= 12
            month_end = _month_end(target_year, target_month)
            if month_end >= today and month_end <= today + timedelta(days=lookahead_days):
                candidates.append(month_end)
        return sorted(candidates)

    if frequency == FREQ_SEMI_MONTHLY:
        candidates = []
        # Check this month's 15th and end, next month's 15th and end
        for month_offset in range(2):
            target_month = today.month + month_offset
            target_year = today.year
            if target_month > 12:
                target_year += 1
                target_month -= 12

            # 15th of month
            mid_month = date(target_year, target_month, 15)
            if mid_month >= today and mid_month <= today + timedelta(days=lookahead_days):
                candidates.append(mid_month)

            # End of month
            month_end = _month_end(target_year, target_month)
            if month_end >= today and month_end <= today + timedelta(days=lookahead_days):
                candidates.append(month_end)

        return sorted(candidates)

    if frequency == FREQ_BI_WEEKLY:
        if last_period_end is None:
            return []

        candidates = []
        current = last_period_end

        # Move forward until we pass today
        while current < today:
            current += timedelta(days=14)

        # Collect all periods within lookahead window
        while current <= today + timedelta(days=lookahead_days):
            candidates.append(current)
            current += timedelta(days=14)

        return candidates

    if frequency == FREQ_QUARTERLY:
        candidates = []
        # Check this quarter and next few quarters
        for quarter_offset in range(4):
            q = _quarter_of(today.month) + quarter_offset
            target_year = today.year
            if q > 4:
                target_year += 1
                q -= 4
            quarter_end = _quarter_end(target_year, q)
            if quarter_end >= today and quarter_end <= today + timedelta(days=lookahead_days):
                candidates.append(quarter_end)
        return sorted(candidates)

    if frequency == FREQ_YEARLY:
        candidates = []
        # Check this year and next year
        for year_offset in range(2):
            target_year = today.year + year_offset
            year_end = date(target_year, 12, 31)
            if year_end >= today and year_end <= today + timedelta(days=lookahead_days):
                candidates.append(year_end)
        return sorted(candidates)

    raise ValueError(f"Unknown frequency: {frequency}")
