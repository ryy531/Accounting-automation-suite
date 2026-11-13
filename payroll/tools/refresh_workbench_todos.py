#!/usr/bin/env python3
"""
refresh_workbench_todos.py -- Phase 1.5 Workbench Refresh (dry-run by default)

Calculates workbench fields from current cursor fields:
- Next Period End
- Urgency (Needs Review, Overdue, Due This Week, Due Soon, OK, Blocked)
- Workbench Priority (P0-P4 + Blocked)
- Workbench Next Action
- Workbench Next Action Date

DOES NOT:
- Modify Master Excel
- Write Engagements/Events
- Send email/CRA/invoice/outbound actions
- Auto-advance Filed Until/Done Until
- Auto-overwrite GST/PST/T2 Due Next Period (only dry-run difference report)

Safety:
- Default mode is dry-run (no NocoDB writes)
- Apply mode requires explicit --apply flag
- All calculations are read-only until apply is confirmed

Author: Phase 1.5 Workbench Team
Date: 2026-06-09
"""

import argparse
import calendar
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

# Add _lib to path for imports
SCRIPT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(SCRIPT_ROOT / "_lib"))

try:
    from dateutil.relativedelta import relativedelta
except ImportError:
    raise SystemExit(
        "[refresh] python-dateutil is not installed.\n"
        "  Run: pip install python-dateutil"
    )

try:
    from config.settings import (
        NOCODB_URL, NOCODB_TOKEN, NOCODB_COMPANIES_TABLE_ID
    )
    from connectors.nocodb_client import NocoDBClient, NocoDBError
except ImportError as e:
    raise SystemExit(
        f"[refresh] Failed to import required modules: {e}\n"
        "  Ensure _lib is in the script root."
    )


# =============================================================================
# CONSTANTS
# =============================================================================

# Frequency values (from NocoDB select options)
FREQ_MONTHLY = "Monthly"
FREQ_QUARTERLY = "Quarterly"
FREQ_ANNUAL = "Annual"
FREQ_UNKNOWN = "Unknown"

# Urgency levels
URGENCY_NEEDS_REVIEW = "Needs Review"
URGENCY_OVERDUE = "Overdue"
URGENCY_DUE_THIS_WEEK = "Due This Week"
URGENCY_DUE_SOON = "Due Soon"
URGENCY_OK = "OK"
URGENCY_BLOCKED = "Blocked"

URGENCY_LEVELS = {
    URGENCY_NEEDS_REVIEW: 0,
    URGENCY_OVERDUE: 1,
    URGENCY_DUE_THIS_WEEK: 2,
    URGENCY_DUE_SOON: 3,
    URGENCY_OK: 4,
    URGENCY_BLOCKED: -1,
}

# Priority levels
PRIORITY_P0 = "P0 Needs Review"
PRIORITY_P1 = "P1 Overdue"
PRIORITY_P2 = "P2 Due This Week"
PRIORITY_P3 = "P3 Due Soon"
PRIORITY_P4 = "P4 OK"
PRIORITY_BLOCKED = "Blocked"

# Thresholds
DUE_THIS_WEEK_DAYS = 7
DUE_SOON_DAYS = 30

# Field names (must match NocoDB schema)
FIELD_GST_FREQUENCY = "GST Frequency"
FIELD_GST_FILED_UNTIL = "GST Filed Until"
FIELD_GST_DUE_NEXT_PERIOD = "GST Due Next Period"
FIELD_GST_NEXT_PERIOD_END = "GST Next Period End"
FIELD_GST_URGENCY = "GST Urgency"

FIELD_PST_FREQUENCY = "PST Frequency"
FIELD_PST_FILED_UNTIL = "PST Filed Until"
FIELD_PST_DUE_NEXT_PERIOD = "PST Due Next Period"
FIELD_PST_NEXT_PERIOD_END = "PST Next Period End"
FIELD_PST_URGENCY = "PST Urgency"
FIELD_PST_NO = "PST No."

FIELD_T2_FILED_UNTIL = "T2 Filed Until"
FIELD_T2_DUE_NEXT_PERIOD = "T2 Due Next Period"
FIELD_T2_URGENCY = "T2 Urgency"

FIELD_BOOKKEEPING_FILED_UNTIL = "Bookkeeping Filed Until"
FIELD_BOOKKEEPING_URGENCY = "Bookkeeping Urgency"

FIELD_WORKBENCH_NEXT_ACTION = "Workbench Next Action"
FIELD_WORKBENCH_NEXT_ACTION_DATE = "Workbench Next Action Date"
FIELD_WORKBENCH_PRIORITY = "Workbench Priority"
FIELD_WORKBENCH_NEEDS_REVIEW = "Workbench Needs Review"
FIELD_WORKBENCH_REVIEW_REASON = "Workbench Review Reason"
FIELD_WORKBENCH_LAST_UPDATED_AT = "Workbench Last Updated At"
FIELD_LEGAL_NAME = "Legal Name"


# =============================================================================
# DATE CALCULATION UTILS (pure functions, testable)
# =============================================================================

def parse_date(value: Any) -> date | None:
    """Parse date from various formats (string, Excel serial, etc.)."""
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None

    # String formats
    if isinstance(value, str):
        value = value.strip()
        # Common formats
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%m/%d/%Y"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        # Try Excel-style
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            pass

    # Excel serial date (number of days since 1900-01-01, with 1900 being a leap year bug)
    if isinstance(value, (int, float)):
        # Excel epoch is 1899-12-30
        excel_epoch = date(1899, 12, 30)
        try:
            return excel_epoch + timedelta(days=int(value))
        except (ValueError, OverflowError):
            return None

    return None


def calculate_next_period_end(
    filed_until: date | None,
    frequency: str | None
) -> tuple[date | None, date | None, str | None]:
    """
    Calculate next period start and end from filed_until + frequency.

    Returns: (period_start, period_end, error_reason)
    - period_start: First day of next period (None if error)
    - period_end: Last day of next period (None if error)
    - error_reason: String explaining why calculation failed (None if OK)
    """
    if filed_until is None:
        return None, None, "Filed Until is blank"

    if frequency is None:
        frequency = FREQ_UNKNOWN

    freq = frequency.strip().lower() if frequency else ""

    # Start = Filed Until + 1 day
    period_start = filed_until + timedelta(days=1)

    if freq == FREQ_MONTHLY.lower():
        # End = Start + 1 month - 1 day
        period_end = period_start + relativedelta(months=1) - timedelta(days=1)
        return period_start, period_end, None

    if freq == FREQ_QUARTERLY.lower():
        # End = Start + 3 months - 1 day
        period_end = period_start + relativedelta(months=3) - timedelta(days=1)
        return period_start, period_end, None

    if freq == FREQ_ANNUAL.lower():
        # End = Start + 12 months - 1 day
        period_end = period_start + relativedelta(months=12) - timedelta(days=1)
        return period_start, period_end, None

    return None, None, f"Unknown frequency: {frequency}"


def calculate_gst_due(period_start: date, frequency: str) -> date:
    """
    Calculate GST due date from period start + frequency.

    Rules (CRA):
    - Monthly: Start + 2 months - 1 day
    - Quarterly: Start + 4 months - 1 day
    - Annual: Start + 15 months - 1 day
    """
    freq = frequency.strip().lower() if frequency else ""

    if freq == FREQ_MONTHLY.lower():
        return period_start + relativedelta(months=2) - timedelta(days=1)

    if freq == FREQ_QUARTERLY.lower():
        return period_start + relativedelta(months=4) - timedelta(days=1)

    if freq == FREQ_ANNUAL.lower():
        return period_start + relativedelta(months=15) - timedelta(days=1)

    # Default to quarterly if unknown
    return period_start + relativedelta(months=4) - timedelta(days=1)


def calculate_pst_due(period_end: date) -> date:
    """
    Calculate PST due date from period end.

    Rule (Saskatchewan): Due = end of next month after period end.
    """
    # Go to first day of next month, then subtract 1 day (end of current month)
    # Then add 1 month (end of next month)
    next_month_first = period_end.replace(day=1) + relativedelta(months=1)
    next_month_end = (next_month_first + relativedelta(months=1)) - timedelta(days=1)
    return next_month_end


def classify_urgency(
    due_date: date | None,
    today: date,
    needs_review: bool = False,
    is_blocked: bool = False
) -> str:
    """Classify urgency level based on due date and flags."""
    if is_blocked:
        return URGENCY_BLOCKED

    if needs_review:
        return URGENCY_NEEDS_REVIEW

    if due_date is None:
        return URGENCY_NEEDS_REVIEW

    days_until = (due_date - today).days

    if days_until < 0:
        return URGENCY_OVERDUE

    if days_until <= DUE_THIS_WEEK_DAYS:
        return URGENCY_DUE_THIS_WEEK

    if days_until <= DUE_SOON_DAYS:
        return URGENCY_DUE_SOON

    return URGENCY_OK


def urgency_to_priority(urgency: str) -> str:
    """Map urgency to Workbench Priority."""
    mapping = {
        URGENCY_NEEDS_REVIEW: PRIORITY_P0,
        URGENCY_OVERDUE: PRIORITY_P1,
        URGENCY_DUE_THIS_WEEK: PRIORITY_P2,
        URGENCY_DUE_SOON: PRIORITY_P3,
        URGENCY_OK: PRIORITY_P4,
        URGENCY_BLOCKED: PRIORITY_BLOCKED,
    }
    return mapping.get(urgency, PRIORITY_P0)


def format_next_action(
    tax_type: str,
    period_end: date | None = None,
    due_date: date | None = None,
    is_bookkeeping: bool = False,
    blocking_tax: str | None = None,
    blocking_due: date | None = None
) -> str:
    """
    Format Next Action text description.

    Examples:
    - "File GST for 2026-03-31 by 2026-04-30"
    - "Finish bookkeeping through 2026-03-31 before GST due 2026-04-30"
    """
    if is_bookkeeping:
        if blocking_tax and period_end and blocking_due:
            return (
                f"Finish bookkeeping through {period_end} "
                f"before {blocking_tax} due {blocking_due}"
            )
        return "Complete bookkeeping"

    # Regular tax filing
    if period_end and due_date:
        return f"File {tax_type} for {period_end} by {due_date}"

    if due_date:
        return f"File {tax_type} by {due_date}"

    return f"File {tax_type}"


def get_most_urgent_item(items: list[dict]) -> dict | None:
    """
    Find the most urgent tax item from a list.

    Args:
        items: List of dicts with 'urgency' and 'due_date' keys

    Returns:
        The item with lowest urgency level (most urgent)
    """
    if not items:
        return None

    # Filter out items without urgency
    valid_items = [i for i in items if i.get("urgency")]

    # Sort by urgency level
    valid_items.sort(key=lambda x: URGENCY_LEVELS.get(x["urgency"], 999))

    return valid_items[0] if valid_items else None


def check_bookkeeping_block(
    bookkeeping_until: date | None,
    tax_period_end: date | None,
    tax_due: date | None
) -> tuple[bool, date | None]:
    """
    Check if bookkeeping blocks a tax filing.

    Returns: (is_blocked, required_through_date)
    """
    if bookkeeping_until is None or tax_period_end is None:
        return False, None

    # If bookkeeping is behind the tax period end, it blocks
    if bookkeeping_until < tax_period_end:
        return True, tax_period_end

    return False, None


# =============================================================================
# REPORT GENERATION
# =============================================================================

def generate_report(
    planned_updates: list[dict],
    due_differences: list[dict],
    needs_review_rows: list[dict],
    no_action_rows: list[dict],
    total_companies: int,
    apply_performed: bool,
    report_dir: Path
) -> None:
    """Generate JSON and Markdown reports."""

    # Calculate review reason counts
    review_reason_counts = {}
    for row in needs_review_rows:
        for reason in row.get("review_reasons", []):
            review_reason_counts[reason] = review_reason_counts.get(reason, 0) + 1

    summary = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": "apply" if apply_performed else "dry-run",
        "total_companies_rows": total_companies,
        "planned_update_rows": len(planned_updates),
        "due_difference_rows": len(due_differences),
        "needs_review_rows": len(needs_review_rows),
        "no_action_rows": len(no_action_rows),
        "review_reason_counts": review_reason_counts,
        "apply_performed": apply_performed,
        "engagement_writes": 0,
        "events_writes": 0,
        "master_excel_modifications": 0,
        "outbound_actions": 0,
    }

    # Write JSON files
    (report_dir / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    (report_dir / "planned_updates.json").write_text(
        json.dumps(planned_updates, indent=2, default=str), encoding="utf-8"
    )
    (report_dir / "due_differences.json").write_text(
        json.dumps(due_differences, indent=2, default=str), encoding="utf-8"
    )
    (report_dir / "needs_review.json").write_text(
        json.dumps(needs_review_rows, indent=2, default=str), encoding="utf-8"
    )
    (report_dir / "no_action_rows.json").write_text(
        json.dumps(no_action_rows, indent=2, default=str), encoding="utf-8"
    )

    # Write Markdown summary
    md_content = f"""# Phase 1.5 Workbench Refresh Report

| Item | Value |
| --- | ---: |
| Generated At | {summary['generated_at']} |
| Mode | {summary['mode']} |
| Total Companies Rows | {summary['total_companies_rows']} |
| Planned Update Rows | {summary['planned_update_rows']} |
| Due Difference Rows | {summary['due_difference_rows']} |
| Needs Review Rows | {summary['needs_review_rows']} |
| No Action Rows | {summary['no_action_rows']} |
| Apply Performed | {summary['apply_performed']} |

## Safety Confirmation

- Engagement writes: {summary['engagement_writes']}
- Events writes: {summary['events_writes']}
- Master Excel modifications: {summary['master_excel_modifications']}
- Outbound actions: {summary['outbound_actions']}

## Review Reason Counts

"""

    # Add review reason counts to markdown
    if summary.get("review_reason_counts"):
        for reason, count in sorted(summary["review_reason_counts"].items(), key=lambda x: -x[1]):
            md_content += f"- {reason}: {count}\n"
    else:
        md_content += "No review reasons found.\n"

    md_content += """

## Field Changes Summary

### Planned Updates

Rows with calculated workbench values ready to apply.

### Due Differences

Rows where calculated due date differs from existing Due Next Period.

**IMPORTANT**: Review these differences before applying. They may indicate:
- Excel manual entry errors
- Different calculation assumptions
- Valid special cases

### Needs Review

Rows requiring manual attention (missing fields, invalid dates, etc.).

### No Action

Rows with no changes needed (all values up to date).
"""

    (report_dir / "summary.md").write_text(md_content, encoding="utf-8")

    print(f"\n[refresh] Report generated: {report_dir}")
    print(f"[refresh]   - summary.json")
    print(f"[refresh]   - summary.md")
    print(f"[refresh]   - planned_updates.json ({len(planned_updates)} rows)")
    print(f"[refresh]   - due_differences.json ({len(due_differences)} rows)")
    print(f"[refresh]   - needs_review.json ({len(needs_review_rows)} rows)")
    print(f"[refresh]   - no_action_rows.json ({len(no_action_rows)} rows)")


# =============================================================================
# MAIN CALCULATION LOGIC
# =============================================================================

def calculate_workbench_for_row(row: dict, today: date) -> dict:
    """
    Calculate all workbench fields for a single company row.

    Returns: dict with calculated values and metadata
    """
    company_id = row.get("Id")
    legal_name = row.get(FIELD_LEGAL_NAME, "")

    # Parse source fields
    gst_frequency = row.get(FIELD_GST_FREQUENCY)
    gst_filed_until = parse_date(row.get(FIELD_GST_FILED_UNTIL))
    gst_due_next = parse_date(row.get(FIELD_GST_DUE_NEXT_PERIOD))

    # PST applicability: only process if any PST field exists
    pst_no = row.get(FIELD_PST_NO)
    pst_frequency = row.get(FIELD_PST_FREQUENCY)
    pst_filed_until = parse_date(row.get(FIELD_PST_FILED_UNTIL))
    pst_due_next = parse_date(row.get(FIELD_PST_DUE_NEXT_PERIOD))

    pst_applicable = bool(
        (pst_no and str(pst_no).strip()) or
        (pst_frequency and str(pst_frequency).strip()) or
        pst_filed_until or
        pst_due_next
    )

    t2_filed_until = parse_date(row.get(FIELD_T2_FILED_UNTIL))
    t2_due_next = parse_date(row.get(FIELD_T2_DUE_NEXT_PERIOD))

    bookkeeping_until = parse_date(row.get(FIELD_BOOKKEEPING_FILED_UNTIL))

    result = {
        "company_id": company_id,
        "legal_name": legal_name,
        "fields": {},
        "due_differences": [],
        "review_reasons": [],
        "tax_items": [],  # For tracking all tax urgencies
        "due_mismatch_existing_due": None,  # Track existing due from mismatch for Next Action Date
    }

    # --- GST Calculations ---

    gst_needs_review = False
    gst_review_reason = None
    gst_period_end = None
    gst_calculated_due = None

    if gst_filed_until is None and gst_frequency:
        gst_needs_review = True
        gst_review_reason = "GST Filed Until is blank"
    elif gst_frequency is None or gst_frequency.strip() == "":
        gst_needs_review = True
        gst_review_reason = "GST Frequency is blank"
    elif gst_filed_until:
        gst_period_start, gst_period_end, gst_error = calculate_next_period_end(
            gst_filed_until, gst_frequency
        )
        if gst_error:
            gst_needs_review = True
            gst_review_reason = gst_error
        else:
            gst_period_end = gst_period_end
            gst_calculated_due = calculate_gst_due(gst_period_start, gst_frequency)

            # Check if calculated due matches existing due
            if gst_due_next and gst_calculated_due != gst_due_next:
                result["due_differences"].append({
                    "tax": "GST",
                    "existing_due": str(gst_due_next),
                    "calculated_due": str(gst_calculated_due),
                })
                result["review_reasons"].append(
                    f"GST due mismatch: existing {gst_due_next}, calculated {gst_calculated_due}"
                )
                # Track existing due for Next Action Date (use existing, not calculated)
                result["due_mismatch_existing_due"] = gst_due_next

    gst_urgency = classify_urgency(gst_due_next, today, gst_needs_review)

    result["fields"][FIELD_GST_NEXT_PERIOD_END] = (
        gst_period_end.isoformat() if gst_period_end else None
    )
    result["fields"][FIELD_GST_URGENCY] = gst_urgency

    result["tax_items"].append({
        "type": "GST",
        "urgency": gst_urgency,
        "due_date": gst_due_next,
        "period_end": gst_period_end,
        "calculated_due": gst_calculated_due,
    })

    if gst_review_reason:
        result["review_reasons"].append(gst_review_reason)

    # --- PST Calculations ---

    pst_needs_review = False
    pst_review_reason = None
    pst_period_end = None
    pst_calculated_due = None

    # Check PST applicability first
    if not pst_applicable:
        # PST not applicable for this company
        pst_urgency = URGENCY_OK  # Safe value, not P0
        pst_period_end = None
        pst_calculated_due = None
    else:
        # PST is applicable, do normal calculations
        if pst_filed_until is None and pst_frequency:
            pst_needs_review = True
            pst_review_reason = "PST Filed Until is blank"
        elif pst_frequency is None or pst_frequency.strip() == "":
            pst_needs_review = True
            pst_review_reason = "PST Frequency is blank"
        elif pst_filed_until:
            pst_period_start, pst_period_end, pst_error = calculate_next_period_end(
                pst_filed_until, pst_frequency
            )
            if pst_error:
                pst_needs_review = True
                pst_review_reason = pst_error
            else:
                pst_period_end = pst_period_end
                pst_calculated_due = calculate_pst_due(pst_period_end)

                # Check if calculated due matches existing due
                if pst_due_next and pst_calculated_due != pst_due_next:
                    result["due_differences"].append({
                        "tax": "PST",
                        "existing_due": str(pst_due_next),
                        "calculated_due": str(pst_calculated_due),
                    })
                    result["review_reasons"].append(
                        f"PST due mismatch: existing {pst_due_next}, calculated {pst_calculated_due}"
                    )
                    # Track existing due for Next Action Date (use existing, not calculated)
                    result["due_mismatch_existing_due"] = pst_due_next

        pst_urgency = classify_urgency(pst_due_next, today, pst_needs_review)

    result["fields"][FIELD_PST_NEXT_PERIOD_END] = (
        pst_period_end.isoformat() if pst_period_end else None
    )
    result["fields"][FIELD_PST_URGENCY] = pst_urgency

    result["tax_items"].append({
        "type": "PST",
        "urgency": pst_urgency,
        "due_date": pst_due_next,
        "period_end": pst_period_end,
        "calculated_due": pst_calculated_due,
    })

    if pst_review_reason:
        result["review_reasons"].append(pst_review_reason)

    # --- T2 Calculations (Master-derived due) ---

    t2_needs_review = False
    t2_review_reason = None

    if t2_filed_until is None:
        t2_needs_review = True
        t2_review_reason = "T2 Filed Until is blank"
    elif t2_due_next is None:
        t2_needs_review = True
        t2_review_reason = "T2 Due Next Period is blank"

    t2_urgency = classify_urgency(t2_due_next, today, t2_needs_review)
    result["fields"][FIELD_T2_URGENCY] = t2_urgency

    result["tax_items"].append({
        "type": "T2",
        "urgency": t2_urgency,
        "due_date": t2_due_next,
        "period_end": None,
        "calculated_due": None,  # T2 uses Master-derived due
    })

    if t2_review_reason:
        result["review_reasons"].append(t2_review_reason)

    # --- Bookkeeping Calculations ---

    bookkeeping_needs_review = False
    bookkeeping_review_reason = None
    bookkeeping_period_end = None
    bookkeeping_blocking_tax = None
    bookkeeping_blocking_due = None

    if bookkeeping_until is None:
        bookkeeping_needs_review = True
        bookkeeping_review_reason = "Bookkeeping Filed Until is blank"

    # Check if bookkeeping blocks any tax filing
    # Find all blocked tax items and select the most urgent one
    # Phase 1.5: Only block for urgent taxes (Overdue, Due This Week, Due Soon)
    # Do NOT block for OK / Needs Review / Blocked
    BLOCKING_URGENCIES = {URGENCY_OVERDUE, URGENCY_DUE_THIS_WEEK, URGENCY_DUE_SOON}

    blocked_items = []
    for item in result["tax_items"]:
        # Skip items with non-urgent urgencies
        if item.get("urgency") not in BLOCKING_URGENCIES:
            continue
        if item["period_end"] and item["due_date"]:
            is_blocked, required_through = check_bookkeeping_block(
                bookkeeping_until, item["period_end"], item["due_date"]
            )
            if is_blocked:
                blocked_items.append({
                    "type": item["type"],
                    "required_through": required_through,
                    "due_date": item["due_date"],
                    "urgency": item["urgency"],
                    "period_end": item["period_end"],
                })

    if blocked_items:
        # Sort by urgency level and select most urgent
        blocked_items.sort(key=lambda x: URGENCY_LEVELS.get(x["urgency"], 999))
        most_blocked = blocked_items[0]
        bookkeeping_period_end = most_blocked["required_through"]
        bookkeeping_blocking_tax = most_blocked["type"]
        bookkeeping_blocking_due = most_blocked["due_date"]
        bookkeeping_urgency = most_blocked["urgency"]
    else:
        # No blocking, bookkeeping is OK or not applicable
        # Only set to OK if bookkeeping_until exists and no blocks
        if bookkeeping_until:
            bookkeeping_urgency = URGENCY_OK
        else:
            bookkeeping_urgency = URGENCY_NEEDS_REVIEW

    if bookkeeping_review_reason:
        result["review_reasons"].append(bookkeeping_review_reason)

    result["fields"][FIELD_BOOKKEEPING_URGENCY] = bookkeeping_urgency

    # --- Workbench Priority (most urgent across all) ---

    most_urgent = get_most_urgent_item(result["tax_items"])
    if most_urgent:
        base_urgency = most_urgent["urgency"]
    else:
        base_urgency = URGENCY_NEEDS_REVIEW

    # If bookkeeping blocks, bookkeeping urgency takes priority
    if bookkeeping_period_end:
        workbench_priority = urgency_to_priority(bookkeeping_urgency)
    else:
        workbench_priority = urgency_to_priority(base_urgency)

    result["fields"][FIELD_WORKBENCH_PRIORITY] = workbench_priority

    # --- Workbench Next Action ---

    if bookkeeping_period_end and bookkeeping_blocking_tax and bookkeeping_blocking_due:
        # Bookkeeping blocks the most urgent tax
        next_action = format_next_action(
            "Bookkeeping",
            period_end=bookkeeping_period_end,
            is_bookkeeping=True,
            blocking_tax=bookkeeping_blocking_tax,
            blocking_due=bookkeeping_blocking_due,
        )
        next_action_date = bookkeeping_blocking_due
    elif most_urgent:
        # Most urgent tax item
        if most_urgent["urgency"] == URGENCY_NEEDS_REVIEW:
            # Needs Review items get review actions, not filing actions
            review_items = []
            if result["review_reasons"]:
                review_items.extend(result["review_reasons"][:2])  # First 2 reasons
            next_action = f"Review workbench data: {'; '.join(review_items) if review_items else 'missing fields'}"
            next_action_date = most_urgent["due_date"]  # May be None
        else:
            next_action = format_next_action(
                most_urgent["type"],
                period_end=most_urgent["period_end"],
                due_date=most_urgent["due_date"],
            )
            next_action_date = most_urgent["due_date"]
    else:
        next_action = "Review client details"
        next_action_date = None

    result["fields"][FIELD_WORKBENCH_NEXT_ACTION] = next_action
    result["fields"][FIELD_WORKBENCH_NEXT_ACTION_DATE] = (
        next_action_date.isoformat() if next_action_date else None
    )

    # --- Phase 1.5 Rule: Review Reasons Override ---
    # If there are ANY review_reasons (due mismatch, missing fields, etc.),
    # Workbench Priority must be P0 Needs Review and Next Action must be review action.
    # This overrides filing actions to prevent mistaken execution before review.
    if result["review_reasons"]:
        result["fields"][FIELD_WORKBENCH_PRIORITY] = PRIORITY_P0
        review_items = result["review_reasons"][:2]  # First 2 reasons
        result["fields"][FIELD_WORKBENCH_NEXT_ACTION] = (
            f"Review workbench data: {'; '.join(review_items) if review_items else 'see reasons'}"
        )
        # Use existing due from mismatch for Next Action Date, otherwise None
        mismatch_due = result.get("due_mismatch_existing_due")
        result["fields"][FIELD_WORKBENCH_NEXT_ACTION_DATE] = (
            mismatch_due.isoformat() if mismatch_due else None
        )

    # --- Workbench Needs Review (report-only, not written to payload) ---
    # Phase 1 fields are protected; only report proposed review

    result["proposed_needs_review"] = bool(result["review_reasons"])
    result["proposed_review_reason"] = (
        "; ".join(result["review_reasons"]) if result["review_reasons"] else ""
    )

    # --- Workbench Last Updated At (only add if real changes exist) ---
    # This will be added later if there are actual business field changes

    return result


# =============================================================================
# MAIN
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Phase 1.5 Workbench Refresh (dry-run by default)"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="[DISABLED - Phase 1.5 safety] Apply changes to NocoDB (dry-run only)"
    )
    parser.add_argument(
        "--report-dir",
        type=str,
        help="Custom report directory path"
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Run pure function tests and exit"
    )

    args = parser.parse_args()

    # Run tests if requested
    if args.test:
        run_tests()
        return

    # Validate configuration
    if not NOCODB_URL or not NOCODB_TOKEN or not NOCODB_COMPANIES_TABLE_ID:
        raise SystemExit(
            "[refresh] NocoDB configuration incomplete.\n"
            "  Check .env: NOCODB_URL, NOCODB_TOKEN, NOCODB_COMPANIES_TABLE_ID"
        )

    # Create report directory
    if args.report_dir:
        report_dir = Path(args.report_dir)
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_dir = Path(SCRIPT_ROOT) / "payroll" / "_reports" / f"workbench_refresh_{timestamp}"

    report_dir.mkdir(parents=True, exist_ok=True)

    print(f"[refresh] Phase 1.5 Workbench Refresh")
    print(f"[refresh] Mode: {'apply' if args.apply else 'dry-run'}")
    print(f"[refresh] Report directory: {report_dir}")

    # Initialize NocoDB client
    client = NocoDBClient(timeout=30)

    # Fetch all Companies rows
    print(f"[refresh] Fetching Companies rows...")
    try:
        response = client.list_rows(NOCODB_COMPANIES_TABLE_ID)
        companies = response.get("list", [])
    except NocoDBError as e:
        raise SystemExit(f"[refresh] Failed to fetch Companies: {e}")

    print(f"[refresh]   Found {len(companies)} companies")

    # Calculate workbench values
    today = date.today()
    print(f"[refresh] Calculating workbench values (reference date: {today})...")

    planned_updates = []
    due_differences = []
    needs_review_rows = []
    no_action_rows = []

    for row in companies:
        result = calculate_workbench_for_row(row, today)

        if result["due_differences"]:
            due_differences.append(result)

        if result["review_reasons"]:
            needs_review_rows.append(result)

        # Check if any fields would change (excluding timestamp)
        has_changes = False
        for field_name, new_value in result["fields"].items():
            existing_value = row.get(field_name)
            # Normalize comparison
            if str(existing_value) != str(new_value):
                has_changes = True
                break

        if has_changes:
            # Only add timestamp when there are real business changes
            result["fields"][FIELD_WORKBENCH_LAST_UPDATED_AT] = (
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            )
            planned_updates.append(result)
        else:
            no_action_rows.append(result)

    print(f"[refresh]   Planned updates: {len(planned_updates)}")
    print(f"[refresh]   Due differences: {len(due_differences)}")
    print(f"[refresh]   Needs review: {len(needs_review_rows)}")
    print(f"[refresh]   No action: {len(no_action_rows)}")

    # Apply if requested
    apply_performed = False
    if args.apply:
        print(f"[refresh] ERROR: --apply is DISABLED for Phase 1.5")
        print(f"[refresh]   This script is dry-run only. Review the generated reports first.")
        print(f"[refresh]   To enable apply, remove this guard after Riley approval.")
        raise SystemExit(1)
    else:
        print(f"[refresh] Dry-run mode - no changes applied to NocoDB")

    # Generate reports
    generate_report(
        planned_updates=planned_updates,
        due_differences=due_differences,
        needs_review_rows=needs_review_rows,
        no_action_rows=no_action_rows,
        total_companies=len(companies),
        apply_performed=apply_performed,
        report_dir=report_dir,
    )

    print(f"\n[refresh] Done.")


# =============================================================================
# TESTS (pure functions only)
# =============================================================================

def run_tests() -> None:
    """Run pure function tests for date calculations."""
    print("[test] Running pure function tests...")

    test_passed = 0
    test_failed = 0

    def assert_eq(actual, expected, description):
        nonlocal test_passed, test_failed
        if actual == expected:
            test_passed += 1
            print(f"[test]   PASS: {description}")
        else:
            test_failed += 1
            print(f"[test]   FAIL: {description}")
            print(f"[test]     Expected: {expected}")
            print(f"[test]     Actual: {actual}")

    # Test 1: GST Monthly from 2025-12-31
    filed = date(2025, 12, 31)
    start, end, error = calculate_next_period_end(filed, FREQ_MONTHLY)
    assert_eq(start, date(2026, 1, 1), "GST Monthly start from 2025-12-31")
    assert_eq(end, date(2026, 1, 31), "GST Monthly end from 2025-12-31")

    if start:
        due = calculate_gst_due(start, FREQ_MONTHLY)
        assert_eq(due, date(2026, 2, 28), "GST Monthly due from 2025-12-31")

    # Test 2: GST Quarterly from 2025-12-31
    start, end, error = calculate_next_period_end(filed, FREQ_QUARTERLY)
    assert_eq(start, date(2026, 1, 1), "GST Quarterly start from 2025-12-31")
    assert_eq(end, date(2026, 3, 31), "GST Quarterly end from 2025-12-31")

    if start:
        due = calculate_gst_due(start, FREQ_QUARTERLY)
        assert_eq(due, date(2026, 4, 30), "GST Quarterly due from 2025-12-31")

    # Test 3: GST Annual from 2025-12-31
    start, end, error = calculate_next_period_end(filed, FREQ_ANNUAL)
    assert_eq(start, date(2026, 1, 1), "GST Annual start from 2025-12-31")
    assert_eq(end, date(2026, 12, 31), "GST Annual end from 2025-12-31")

    if start:
        due = calculate_gst_due(start, FREQ_ANNUAL)
        assert_eq(due, date(2027, 3, 31), "GST Annual due from 2025-12-31")

    # Test 4: PST EOMONTH behavior
    period_end = date(2026, 1, 31)
    pst_due = calculate_pst_due(period_end)
    assert_eq(pst_due, date(2026, 2, 28), "PST due for period ending 2026-01-31")

    period_end = date(2026, 3, 31)
    pst_due = calculate_pst_due(period_end)
    assert_eq(pst_due, date(2026, 4, 30), "PST due for period ending 2026-03-31")

    # Test 5: Urgency classification
    today = date(2026, 6, 9)
    assert_eq(
        classify_urgency(date(2026, 6, 1), today),
        URGENCY_OVERDUE,
        "Urgency: overdue (past due)"
    )
    assert_eq(
        classify_urgency(date(2026, 6, 15), today),
        URGENCY_DUE_THIS_WEEK,
        "Urgency: due this week"
    )
    assert_eq(
        classify_urgency(date(2026, 7, 9), today),
        URGENCY_DUE_SOON,
        "Urgency: due soon (30 days)"
    )
    assert_eq(
        classify_urgency(date(2026, 8, 1), today),
        URGENCY_OK,
        "Urgency: OK (>30 days)"
    )

    # Test 6: Priority mapping
    assert_eq(urgency_to_priority(URGENCY_NEEDS_REVIEW), PRIORITY_P0, "Priority: Needs Review -> P0")
    assert_eq(urgency_to_priority(URGENCY_OVERDUE), PRIORITY_P1, "Priority: Overdue -> P1")
    assert_eq(urgency_to_priority(URGENCY_DUE_THIS_WEEK), PRIORITY_P2, "Priority: Due This Week -> P2")
    assert_eq(urgency_to_priority(URGENCY_DUE_SOON), PRIORITY_P3, "Priority: Due Soon -> P3")
    assert_eq(urgency_to_priority(URGENCY_OK), PRIORITY_P4, "Priority: OK -> P4")

    # Test 7: Bookkeeping blocking detection
    bookkeeping_until = date(2026, 1, 31)
    tax_period_end = date(2026, 3, 31)
    tax_due = date(2026, 4, 30)
    is_blocked, required = check_bookkeeping_block(
        bookkeeping_until, tax_period_end, tax_due
    )
    assert_eq(is_blocked, True, "Bookkeeping blocks tax (behind period end)")
    assert_eq(required, date(2026, 3, 31), "Required through date matches tax period end")

    # Test 8: Most urgent item selection
    items = [
        {"type": "GST", "urgency": URGENCY_OK, "due_date": date(2026, 8, 1)},
        {"type": "PST", "urgency": URGENCY_OVERDUE, "due_date": date(2026, 6, 1)},
        {"type": "T2", "urgency": URGENCY_DUE_SOON, "due_date": date(2026, 7, 1)},
    ]
    most_urgent = get_most_urgent_item(items)
    assert_eq(most_urgent["type"], "PST", "Most urgent item is PST (Overdue)")

    # Test 9: Bookkeeping with OK tax should not block
    bookkeeping_until = date(2026, 3, 31)
    tax_period_end = date(2026, 3, 31)
    tax_due = date(2026, 4, 30)
    is_blocked, required = check_bookkeeping_block(
        bookkeeping_until, tax_period_end, tax_due
    )
    assert_eq(is_blocked, False, "Bookkeeping up to date does not block")

    # Test 10: Needs Review action format
    review_action = format_next_action("GST", period_end=None, due_date=None)
    assert_eq(review_action, "File GST", "Next Action with no dates")

    # Test 11: Bookkeeping behind OK tax should NOT trigger blocking
    # Scenario: bookkeeping_until = 2026-01-31, tax_period_end = 2026-03-31
    # tax_due is far future (urgency = OK), bookkeeping is behind but OK urgency
    # Expected: NO bookkeeping blocker generated
    today = date(2026, 6, 9)  # Reference date for urgency calculation
    bookkeeping_until = date(2026, 1, 31)
    tax_period_end = date(2026, 3, 31)
    tax_due = date(2026, 8, 31)  # Far future -> urgency = OK

    # Verify the due date is indeed OK for the reference date
    gst_urgency = classify_urgency(tax_due, today)
    assert_eq(gst_urgency, URGENCY_OK, "Far future tax due is OK urgency")

    # Check if bookkeeping would block (it would based on dates)
    is_blocked, required = check_bookkeeping_block(
        bookkeeping_until, tax_period_end, tax_due
    )
    assert_eq(is_blocked, True, "Bookkeeping is behind period end (date check)")

    # But in the actual blocker logic, OK urgency should be filtered out
    # Simulate the blocker filtering logic
    BLOCKING_URGENCIES = {URGENCY_OVERDUE, URGENCY_DUE_THIS_WEEK, URGENCY_DUE_SOON}
    test_item = {
        "type": "GST",
        "period_end": tax_period_end,
        "due_date": tax_due,
        "urgency": URGENCY_OK,
    }
    would_trigger_blocker = bool(
        test_item.get("urgency") in BLOCKING_URGENCIES
        and test_item["period_end"] and test_item["due_date"]
    )
    assert_eq(would_trigger_blocker, False, "OK urgency is filtered out from blocker")

    # Test 12: Bookkeeping behind Overdue tax SHOULD trigger blocking
    tax_due_overdue = date(2026, 5, 1)  # Past due for reference date 2026-06-09
    overdue_urgency = classify_urgency(tax_due_overdue, today)
    assert_eq(overdue_urgency, URGENCY_OVERDUE, "Past tax due is Overdue urgency")

    overdue_item = {
        "type": "GST",
        "period_end": tax_period_end,
        "due_date": tax_due_overdue,
        "urgency": URGENCY_OVERDUE,
    }
    would_trigger_blocker_overdue = bool(
        overdue_item.get("urgency") in BLOCKING_URGENCIES
        and overdue_item["period_end"] and overdue_item["due_date"]
    )
    assert_eq(would_trigger_blocker_overdue, True, "Overdue urgency triggers blocker")

    # Test 13: Bookkeeping blocker Next Action format
    bk_action = format_next_action(
        "Bookkeeping",
        period_end=date(2026, 3, 31),
        is_bookkeeping=True,
        blocking_tax="GST",
        blocking_due=date(2026, 4, 30)
    )
    assert_eq(
        bk_action,
        "Finish bookkeeping through 2026-03-31 before GST due 2026-04-30",
        "Bookkeeping blocker Next Action format"
    )

    # Test 14: PST applicability - all fields empty = OK (not P0)
    # Simulate pst_applicable check
    pst_no_none = None
    pst_freq_none = None
    pst_filed_none = None
    pst_due_none = None

    pst_applicable_empty = bool(
        (pst_no_none and str(pst_no_none).strip()) or
        (pst_freq_none and str(pst_freq_none).strip()) or
        pst_filed_none or
        pst_due_none
    )
    assert_eq(pst_applicable_empty, False, "PST all empty = not applicable")

    # Test 15: PST Due Next Period has value but PST Frequency empty = applicable
    # (should be Needs Review in full calculation)
    pst_due_has_value = date(2026, 4, 30)
    pst_applicable_due_only = bool(
        (pst_no_none and str(pst_no_none).strip()) or
        (pst_freq_none and str(pst_freq_none).strip()) or
        pst_filed_none or
        pst_due_has_value
    )
    assert_eq(pst_applicable_due_only, True, "PST Due Next Period exists = applicable")

    # Test 16: PST Frequency has value but PST Filed Until empty = applicable
    # (should be Needs Review in full calculation)
    pst_freq_has_value = "Quarterly"
    pst_applicable_freq_only = bool(
        (pst_no_none and str(pst_no_none).strip()) or
        (pst_freq_has_value and str(pst_freq_has_value).strip()) or
        pst_filed_none or
        pst_due_none
    )
    assert_eq(pst_applicable_freq_only, True, "PST Frequency exists = applicable")

    # Test 17: End-to-end - GST due mismatch → P0 Needs Review
    # Row with GST due mismatch should generate P0 review, not filing action
    today = date(2026, 6, 9)
    gst_due_mismatch_row = {
        "Id": 1,
        FIELD_LEGAL_NAME: "Test Company",
        FIELD_GST_FREQUENCY: "Quarterly",
        FIELD_GST_FILED_UNTIL: "2025-12-31",
        FIELD_GST_DUE_NEXT_PERIOD: "2026-04-15",  # Wrong due date (mismatch)
        FIELD_PST_FREQUENCY: None,
        FIELD_PST_FILED_UNTIL: None,
        FIELD_PST_DUE_NEXT_PERIOD: None,
        FIELD_T2_FILED_UNTIL: None,
        FIELD_T2_DUE_NEXT_PERIOD: None,
        FIELD_BOOKKEEPING_FILED_UNTIL: "2026-03-31",
    }

    result = calculate_workbench_for_row(gst_due_mismatch_row, today)

    assert_eq(
        result["fields"].get(FIELD_WORKBENCH_PRIORITY),
        PRIORITY_P0,
        "GST due mismatch => P0 Needs Review"
    )
    next_action_val = result["fields"].get(FIELD_WORKBENCH_NEXT_ACTION, "")
    assert_eq(
        next_action_val.startswith("Review workbench data:"),
        True,
        "GST due mismatch => Review action (not filing)"
    )
    assert_eq(
        "File GST" in next_action_val,
        False,
        "GST due mismatch => No 'File GST' in action"
    )
    # Next Action Date should be the existing GST Due Next Period, not null
    next_action_date_val = result["fields"].get(FIELD_WORKBENCH_NEXT_ACTION_DATE)
    assert_eq(
        next_action_date_val,
        "2026-04-15",  # Existing due from the test row
        "GST due mismatch => Next Action Date = existing GST Due Next Period"
    )

    # Test 18: End-to-end - Bookkeeping blank + urgent GST → P0 review
    # NOTE: This row has a GST due mismatch (calculated vs existing)
    bk_blank_row = {
        "Id": 2,
        FIELD_LEGAL_NAME: "Test Company 2",
        FIELD_GST_FREQUENCY: "Monthly",
        FIELD_GST_FILED_UNTIL: "2026-04-30",
        FIELD_GST_DUE_NEXT_PERIOD: "2026-06-15",  # Differs from calculated (2026-06-30)
        FIELD_PST_FREQUENCY: None,
        FIELD_PST_FILED_UNTIL: None,
        FIELD_PST_DUE_NEXT_PERIOD: None,
        FIELD_T2_FILED_UNTIL: None,
        FIELD_T2_DUE_NEXT_PERIOD: None,
        FIELD_BOOKKEEPING_FILED_UNTIL: None,  # Blank → review reason
    }

    result = calculate_workbench_for_row(bk_blank_row, today)

    assert_eq(
        result["fields"].get(FIELD_WORKBENCH_PRIORITY),
        PRIORITY_P0,
        "Bookkeeping blank => P0 Needs Review"
    )
    next_action_val = result["fields"].get(FIELD_WORKBENCH_NEXT_ACTION, "")
    assert_eq(
        next_action_val.startswith("Review workbench data:"),
        True,
        "Bookkeeping blank => Review action"
    )
    # Next Action Date should be existing GST Due Next Period (due mismatch present)
    next_action_date_val = result["fields"].get(FIELD_WORKBENCH_NEXT_ACTION_DATE)
    assert_eq(
        next_action_date_val,
        "2026-06-15",
        "Bookkeeping blank with GST due mismatch => Next Action Date = existing GST due"
    )

    # Test 18b: End-to-end - Missing fields ONLY (no due mismatch) → P0 review, Next Action Date = null
    # GST Due Next Period must match calculated to avoid due mismatch
    missing_fields_row = {
        "Id": 2,
        FIELD_LEGAL_NAME: "Test Company 2b",
        FIELD_GST_FREQUENCY: "Monthly",
        FIELD_GST_FILED_UNTIL: "2026-04-30",
        FIELD_GST_DUE_NEXT_PERIOD: "2026-06-30",  # Matches calculated (no mismatch)
        FIELD_PST_FREQUENCY: None,
        FIELD_PST_FILED_UNTIL: None,
        FIELD_PST_DUE_NEXT_PERIOD: None,
        FIELD_T2_FILED_UNTIL: None,
        FIELD_T2_DUE_NEXT_PERIOD: None,
        FIELD_BOOKKEEPING_FILED_UNTIL: None,  # Blank → review reason
    }

    result = calculate_workbench_for_row(missing_fields_row, today)

    assert_eq(
        result["fields"].get(FIELD_WORKBENCH_PRIORITY),
        PRIORITY_P0,
        "Missing fields only => P0 Needs Review"
    )
    next_action_val = result["fields"].get(FIELD_WORKBENCH_NEXT_ACTION, "")
    assert_eq(
        next_action_val.startswith("Review workbench data:"),
        True,
        "Missing fields only => Review action"
    )
    # Next Action Date should be null since this is missing fields only (no due mismatch)
    next_action_date_val = result["fields"].get(FIELD_WORKBENCH_NEXT_ACTION_DATE)
    assert_eq(
        next_action_date_val,
        None,
        "Missing fields only (no due mismatch) => Next Action Date = null"
    )

    # Test 19: End-to-end - No review_reasons + bookkeeping blocker
    # Should show "Finish bookkeeping through ..." action
    # NOTE: GST Due Next Period must match calculated due to avoid due mismatch
    # Filed Until 2025-12-31 + Quarterly = Due 2026-04-30 (calculated)
    # T2 fields must be valid to avoid "T2 Filed Until is blank" review reason
    bk_blocker_row = {
        "Id": 3,
        FIELD_LEGAL_NAME: "Test Company 3",
        FIELD_GST_FREQUENCY: "Quarterly",
        FIELD_GST_FILED_UNTIL: "2025-12-31",
        FIELD_GST_DUE_NEXT_PERIOD: "2026-04-30",  # Matches calculated due, Overdue as of 2026-06-09
        FIELD_PST_NO: None,  # Required for PST applicability check
        FIELD_PST_FREQUENCY: None,
        FIELD_PST_FILED_UNTIL: None,
        FIELD_PST_DUE_NEXT_PERIOD: None,
        FIELD_T2_FILED_UNTIL: "2025-12-31",  # Valid T2 Filed Until to avoid review reason
        FIELD_T2_DUE_NEXT_PERIOD: "2026-12-31",  # Valid T2 due (far future, OK urgency)
        FIELD_BOOKKEEPING_FILED_UNTIL: "2026-01-31",  # Behind GST period end (2026-03-31)
    }

    result = calculate_workbench_for_row(bk_blocker_row, today)

    # Should be P1 Overdue (from GST), not P0

    # Should be P1 Overdue (from GST), not P0
    assert_eq(
        result["fields"].get(FIELD_WORKBENCH_PRIORITY),
        PRIORITY_P1,
        "Bookkeeping blocker + no review reasons => P1 Overdue (from GST)"
    )
    next_action_val = result["fields"].get(FIELD_WORKBENCH_NEXT_ACTION, "")
    assert_eq(
        next_action_val.startswith("Finish bookkeeping through"),
        True,
        "Bookkeeping blocker => 'Finish bookkeeping through' action"
    )

    print(f"\n[test] Tests passed: {test_passed}")
    print(f"[test] Tests failed: {test_failed}")

    if test_failed > 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()