"""Dedicated Bookkeeping catch-up and Annual Return rollover automation."""

from __future__ import annotations

from datetime import date, timedelta

import data_access
from server.period_automation import (
    BUSINESS_STATUS_COMPLETED,
    FREQ_MONTHLY,
    add_months,
    is_business_completed_transition,
    next_period,
    normalize_select_value,
    parse_date,
)


TYPE_BOOKKEEPING = "Bookkeeping"
TYPE_ANNUAL_RETURN = "Annual Return"
BOOKKEEPING_STATUS_FIELD = "Bookkeeping Status"
ANNUAL_RETURN_STATUS_FIELD = "Annual Return Status"
STATUS_NOT_STARTED = "stage | not started"
EVENT_ACTOR = "NocoDB Webhook"


def next_bookkeeping_period(bookkeeping_filed_until, gst_filed_until):
    """Return the next missing monthly period, or None when cursors align."""
    bookkeeping_cursor = parse_date(bookkeeping_filed_until)
    gst_cursor = parse_date(gst_filed_until)
    if bookkeeping_cursor is None:
        raise ValueError("Bookkeeping Filed Until is blank or invalid")
    if gst_cursor is None:
        raise ValueError("GST Filed Until is blank or invalid")
    for field_name, cursor in (
        ("Bookkeeping Filed Until", bookkeeping_cursor),
        ("GST Filed Until", gst_cursor),
    ):
        month_end = add_months(cursor.replace(day=1), 1) - timedelta(days=1)
        if cursor != month_end:
            raise ValueError(f"{field_name} must be a calendar month end")
    if bookkeeping_cursor > gst_cursor:
        raise ValueError("Bookkeeping Filed Until is after GST Filed Until")
    if bookkeeping_cursor == gst_cursor:
        return None
    period_start, period_end = next_period(bookkeeping_cursor, FREQ_MONTHLY)
    if period_end > gst_cursor:
        raise ValueError("GST Filed Until is not aligned to a Bookkeeping month end")
    return period_start, period_end


def validate_completed_bookkeeping_period(existing_filed_until, completed_period_end):
    """Require the completed row to be exactly the next Bookkeeping month."""
    existing = parse_date(existing_filed_until)
    completed = parse_date(completed_period_end)
    if existing is None:
        raise ValueError("Bookkeeping Filed Until is blank or invalid")
    if completed is None:
        raise ValueError("Bookkeeping Period End is blank or invalid")
    _, expected_end = next_period(existing, FREQ_MONTHLY)
    if completed != expected_end:
        raise ValueError(
            f"Bookkeeping completion must be sequential; expected {expected_end.isoformat()}"
        )
    return completed


def build_next_bookkeeping_fields(company_row: dict):
    """Build the next monthly Bookkeeping row using the current GST due date."""
    company = normalize_select_value(company_row.get("Legal Name"))
    if not company:
        raise ValueError("Company Legal Name is blank")
    next_dates = next_bookkeeping_period(
        company_row.get("Bookkeeping Filed Until"),
        company_row.get("GST Filed Until"),
    )
    if next_dates is None:
        return None, None
    period_start, period_end = next_dates
    gst_due_date = parse_date(company_row.get("GST Due Next Period"))
    review_reason = (
        None
        if gst_due_date
        else "Bookkeeping Due Date needs review: GST Due Next Period is blank or invalid"
    )

    fields = {
        "Name": f"{company} Bookkeeping {period_end.isoformat()}",
        "Type": TYPE_BOOKKEEPING,
        "Company": company,
        "Frequency": FREQ_MONTHLY,
        "Period Start": period_start.isoformat(),
        "Period End": period_end.isoformat(),
        BOOKKEEPING_STATUS_FIELD: STATUS_NOT_STARTED,
        "Urgent": False,
        "Priority": "P0 Needs Review" if review_reason else "P4 OK",
        "Needs Review": bool(review_reason),
        "Review Reason": review_reason,
        "Source": "Auto",
    }
    if gst_due_date:
        fields["Due Date"] = gst_due_date.isoformat()
    return fields, review_reason

def ensure_next_bookkeeping_engagement(company_row: dict, trigger_ref=None):
    """Ensure only the next missing Bookkeeping month exists until GST aligns."""
    company = normalize_select_value(company_row.get("Legal Name"))
    try:
        fields, review_reason = build_next_bookkeeping_fields(company_row)
    except ValueError as exc:
        data_access.create_event(
            engagement_id=trigger_ref or company or "Unknown Engagement",
            event_type="Needs Review",
            description=f"Bookkeeping catch-up review: {exc}",
            actor=EVENT_ACTOR,
        )
        return {"status": "needs_review", "reason": str(exc)}

    if fields is None:
        return {"status": "aligned", "company": company}
    period_end = date.fromisoformat(fields["Period End"])
    if data_access.recurring_engagement_exists(company, TYPE_BOOKKEEPING, period_end):
        return {
            "status": "skipped",
            "reason": "next_engagement_exists",
            "period_end": fields["Period End"],
        }

    created = data_access.create_recurring_engagement(fields)
    data_access.create_event(
        engagement_id=trigger_ref or company,
        event_type="Manual Action",
        description=f"Created Bookkeeping {fields['Period End']} to catch up to GST",
        actor=EVENT_ACTOR,
    )
    return {
        "status": "created",
        "company": company,
        "period_end": fields["Period End"],
        "needs_review": bool(review_reason),
        "created": created,
    }


def complete_bookkeeping_engagement(after: dict, before: dict):
    """Advance the Bookkeeping cursor one month and ensure the next month."""
    if not is_business_completed_transition(
        before.get(BOOKKEEPING_STATUS_FIELD),
        after.get(BOOKKEEPING_STATUS_FIELD),
    ):
        return {"status": "ignored", "reason": "not_exact_completed_transition"}
    if normalize_select_value(after.get("Type")) != TYPE_BOOKKEEPING:
        return {"status": "needs_review", "reason": "type_status_mismatch"}
    company = normalize_select_value(after.get("Company"))
    rows = data_access.list_companies_by_legal_name(company)
    if len(rows) != 1:
        return {"status": "needs_review", "reason": "company_match_count_not_one"}
    company_row = rows[0]
    try:
        completed_end = validate_completed_bookkeeping_period(
            company_row.get("Bookkeeping Filed Until"),
            after.get("Period End"),
        )
        gst_cursor = parse_date(company_row.get("GST Filed Until"))
        if gst_cursor is None or completed_end > gst_cursor:
            raise ValueError("Completed Bookkeeping month is after GST Filed Until")
    except ValueError as exc:
        data_access.create_event(
            engagement_id=after.get("Id") or company,
            event_type="Needs Review",
            description=f"Bookkeeping completion review: {exc}",
            actor=EVENT_ACTOR,
        )
        return {"status": "needs_review", "reason": str(exc)}

    data_access.update_company_by_legal_name(
        company,
        {"Bookkeeping Filed Until": completed_end.isoformat()},
    )
    updated_company = {**company_row, "Bookkeeping Filed Until": completed_end.isoformat()}
    next_result = ensure_next_bookkeeping_engagement(
        updated_company,
        trigger_ref=after.get("Id") or company,
    )
    return {"status": "updated", "company": company, "next": next_result}


def build_next_annual_return_fields(completed: dict):
    """Build next year's Annual Return in the same calendar month."""
    company = normalize_select_value(completed.get("Company"))
    period_start = parse_date(completed.get("Period Start"))
    period_end = parse_date(completed.get("Period End"))
    due_date = parse_date(completed.get("Due Date"))
    if not company or period_start is None or period_end is None or due_date is None:
        raise ValueError("Annual Return requires Company, Period Start/End, and Due Date")
    next_start = add_months(period_start, 12)
    next_end = add_months(period_end, 12)
    next_due = add_months(due_date, 12)
    return {
        "Name": f"{company} Annual Return {next_end.year}",
        "Type": TYPE_ANNUAL_RETURN,
        "Company": company,
        "Year": next_end.year,
        "Frequency": "Annual",
        "Period Start": next_start.isoformat(),
        "Period End": next_end.isoformat(),
        "Due Date": next_due.isoformat(),
        ANNUAL_RETURN_STATUS_FIELD: STATUS_NOT_STARTED,
        "Urgent": False,
        "Source": "Auto",
    }


def complete_annual_return_engagement(after: dict, before: dict):
    """Create next year's Annual Return after exact completion."""
    if not is_business_completed_transition(
        before.get(ANNUAL_RETURN_STATUS_FIELD),
        after.get(ANNUAL_RETURN_STATUS_FIELD),
    ):
        return {"status": "ignored", "reason": "not_exact_completed_transition"}
    if normalize_select_value(after.get("Type")) != TYPE_ANNUAL_RETURN:
        return {"status": "needs_review", "reason": "type_status_mismatch"}
    try:
        fields = build_next_annual_return_fields(after)
    except ValueError as exc:
        data_access.create_event(
            engagement_id=after.get("Id") or after.get("Company") or "Unknown Engagement",
            event_type="Needs Review",
            description=f"Annual Return rollover review: {exc}",
            actor=EVENT_ACTOR,
        )
        return {"status": "needs_review", "reason": str(exc)}

    period_end = date.fromisoformat(fields["Period End"])
    if data_access.recurring_engagement_exists(
        fields["Company"], TYPE_ANNUAL_RETURN, period_end
    ):
        return {"status": "skipped", "reason": "next_engagement_exists"}
    created = data_access.create_recurring_engagement(fields)
    data_access.create_event(
        engagement_id=after.get("Id") or fields["Company"],
        event_type="Manual Action",
        description=f"Created Annual Return {period_end.year} after completion",
        actor=EVENT_ACTOR,
    )
    return {"status": "created", "period_end": fields["Period End"], "created": created}

