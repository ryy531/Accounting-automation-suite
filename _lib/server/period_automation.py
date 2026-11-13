"""
server/period_automation.py -- Status-driven period advancement helpers.

Pure calculation helpers first; live NocoDB writes are routed through
data_access.py by the orchestration functions.
"""

from dataclasses import dataclass
from datetime import date, timedelta
import calendar

import data_access


TYPE_GST = "GST"
TYPE_PST = "PST"
TYPE_T2 = "T2"
TYPE_BOOKKEEPING = "Bookkeeping"
ADVANCE_TYPES = {TYPE_GST, TYPE_PST, TYPE_T2, TYPE_BOOKKEEPING}

FREQ_MONTHLY = "Monthly"
FREQ_QUARTERLY = "Quarterly"
FREQ_ANNUAL = "Annual"
ADVANCE_FREQUENCIES = {FREQ_MONTHLY, FREQ_QUARTERLY, FREQ_ANNUAL}

STATUS_COMPLETE = "Complete"
STATUS_NOT_STARTED = "Not Started"
EVENT_TYPE_MANUAL_ACTION = "Manual Action"
BUSINESS_STATUS_COMPLETED = "stage | completed"
BUSINESS_STATUS_FIELDS = {
    "GST Status": {
        "type": TYPE_GST,
        "company_field": "GST Filed Until",
    },
    "PST Status": {
        "type": TYPE_PST,
        "company_field": "PST Filed Until",
    },
    "T2 Status": {
        "type": TYPE_T2,
        "company_field": "T2 Filed Until",
    },
}


@dataclass(frozen=True)
class PeriodAdvanceResult:
    """Calculated next engagement period payload."""

    engagement_type: str
    company: str
    frequency: str
    period_start: date
    period_end: date
    due_date: date | None
    status: str
    source: str


@dataclass(frozen=True)
class CompanyFiledUntilCalculation:
    """Calculated Companies date-field payload plus safe-skip reasons."""

    fields: dict
    skipped: list[str]
    review_reasons: list[str]


@dataclass(frozen=True)
class BusinessStatusFiledUntilUpdate:
    """Pure decision for advancing one Companies Filed Until field."""

    status: str
    reason: str
    status_field: str
    company: str | None = None
    company_field: str | None = None
    period_end: date | None = None
    fields: dict | None = None
    review_reason: str | None = None


def normalize_select_value(value) -> str:
    """Normalize NocoDB select fields represented as dicts or strings."""
    if isinstance(value, dict):
        value = value.get("value") or value.get("title") or ""
    return " ".join(str(value or "").split())


def parse_multiselect_status_values(value) -> list[str]:
    """Parse NocoDB MultiSelect payload variants into normalized values."""
    if value is None or value == "":
        return []

    raw_values = []
    if isinstance(value, dict):
        raw_values.append(value.get("value") or value.get("title") or "")
    elif isinstance(value, (list, tuple)):
        for item in value:
            raw_values.extend(parse_multiselect_status_values(item))
    else:
        raw_values.extend(str(value).split(","))

    values = []
    for raw in raw_values:
        normalized = " ".join(str(raw or "").split())
        if normalized:
            values.append(normalized)
    return values


def is_exact_business_completed(value) -> bool:
    """Return true only for sole business completion value."""
    return parse_multiselect_status_values(value) == [BUSINESS_STATUS_COMPLETED]


def is_business_completed_transition(before_status, after_status) -> bool:
    """Return true only when a business status newly becomes completed."""
    return (
        not is_exact_business_completed(before_status)
        and is_exact_business_completed(after_status)
    )


def get_business_status_config(status_field: str) -> dict | None:
    """Return Type and Companies Filed Until mapping for a business status field."""
    return BUSINESS_STATUS_FIELDS.get(status_field)


def calculate_only_forward_filed_until(existing, candidate: date) -> date | None:
    """Return candidate only when it advances the existing Filed Until cursor."""
    existing_date = parse_date(existing)
    if existing_date is None or candidate > existing_date:
        return candidate
    return None


def parse_date(value) -> date | None:
    """Parse an ISO date string or date object."""
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def is_complete_transition(before_status, after_status) -> bool:
    """Return true only when a row newly transitions into Complete."""
    before = normalize_select_value(before_status)
    after = normalize_select_value(after_status)
    return before != STATUS_COMPLETE and after == STATUS_COMPLETE


def add_months(value: date, months: int) -> date:
    """Add calendar months while clamping to month end."""
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def next_period(period_end: date, frequency: str) -> tuple[date, date]:
    """Calculate next period start/end from the completed period end."""
    if frequency not in ADVANCE_FREQUENCIES:
        raise ValueError(f"Unsupported frequency: {frequency}")

    months = {
        FREQ_MONTHLY: 1,
        FREQ_QUARTERLY: 3,
        FREQ_ANNUAL: 12,
    }[frequency]

    period_start = period_end + timedelta(days=1)
    return period_start, add_months(period_start, months) - timedelta(days=1)


def next_period_from_filed_until(filed_until: date, frequency: str) -> tuple[date, date]:
    """Calculate next period start/end from a Companies Filed Until cursor."""
    return next_period(filed_until, frequency)


def end_of_next_month(period_end: date) -> date:
    """Return the last day of the month after period_end."""
    first_next = date(period_end.year, period_end.month, 1)
    first_after_next = add_months(first_next, 2)
    return first_after_next - timedelta(days=1)


def calculate_due_date(engagement_type: str, period_start: date,
                       period_end: date, frequency: str) -> date | None:
    """Calculate the due date for supported recurring tax periods."""
    if engagement_type == TYPE_GST:
        due_months = {
            FREQ_MONTHLY: 2,
            FREQ_QUARTERLY: 4,
            FREQ_ANNUAL: 15,
        }.get(frequency)
        if due_months is None:
            return None
        return add_months(period_start, due_months) - timedelta(days=1)

    if engagement_type == TYPE_PST:
        return end_of_next_month(period_end)

    return None


def calculate_gst_due(period_start: date, frequency: str) -> date | None:
    """Calculate GST due date from the next period start and frequency."""
    return calculate_due_date(TYPE_GST, period_start, period_start, frequency)


def calculate_pst_due(period_end: date) -> date:
    """Calculate Saskatchewan PST due date from the next period end."""
    return end_of_next_month(period_end)


def calculate_t2_year_end(filed_until: date) -> date:
    """Calculate the next T2 year end from the filed-until cursor."""
    return add_months(filed_until, 12)


def calculate_t2_due(year_end: date) -> date:
    """Calculate the real T2 due date from the year end."""
    return add_months(year_end, 3)


def _iso_or_none(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _add_sales_tax_company_dates(
    *,
    fields: dict,
    skipped: list[str],
    review_reasons: list[str],
    company: dict,
    tax_type: str,
    filed_until_field: str,
    frequency_field: str,
    next_period_end_field: str,
    due_next_period_field: str,
) -> None:
    filed_until = parse_date(company.get(filed_until_field))
    frequency = normalize_select_value(company.get(frequency_field))

    if filed_until is None:
        skipped.append(f"{tax_type} Filed Until is blank or invalid")
        return
    if frequency not in ADVANCE_FREQUENCIES:
        review_reasons.append(f"{tax_type} Frequency is blank or unsupported")
        return

    next_start, next_end = next_period_from_filed_until(filed_until, frequency)
    due_date = (
        calculate_gst_due(next_start, frequency)
        if tax_type == TYPE_GST
        else calculate_pst_due(next_end)
    )

    fields[next_period_end_field] = _iso_or_none(next_end)
    fields[due_next_period_field] = _iso_or_none(due_date)


def build_company_filed_until_update_fields(company: dict) -> CompanyFiledUntilCalculation:
    """
    Build dependent Companies date fields from Filed Until cursor fields.

    This is pure calculation only; callers are responsible for routing writes
    through data_access.py and for recording review events when needed.
    """
    fields = {}
    skipped = []
    review_reasons = []

    _add_sales_tax_company_dates(
        fields=fields,
        skipped=skipped,
        review_reasons=review_reasons,
        company=company,
        tax_type=TYPE_GST,
        filed_until_field="GST Filed Until",
        frequency_field="GST Frequency",
        next_period_end_field="GST Next Period End",
        due_next_period_field="GST Due Next Period",
    )
    _add_sales_tax_company_dates(
        fields=fields,
        skipped=skipped,
        review_reasons=review_reasons,
        company=company,
        tax_type=TYPE_PST,
        filed_until_field="PST Filed Until",
        frequency_field="PST Frequency",
        next_period_end_field="PST Next Period End",
        due_next_period_field="PST Due Next Period",
    )

    t2_filed_until = parse_date(company.get("T2 Filed Until"))
    if t2_filed_until is None:
        skipped.append("T2 Filed Until is blank or invalid")
    else:
        fields["T2 Due Next Period"] = calculate_t2_year_end(t2_filed_until).isoformat()

    return CompanyFiledUntilCalculation(
        fields=fields,
        skipped=skipped,
        review_reasons=review_reasons,
    )


def build_business_status_filed_until_update(
    after: dict,
    before: dict,
    status_field: str,
    existing_filed_until=None,
) -> BusinessStatusFiledUntilUpdate:
    """
    Build a Companies Filed Until update from one completed business status.

    This function is pure. It validates status transition, Type alignment,
    Company, Period End, and only-forward cursor behavior, then returns the
    minimal Companies update payload for the caller to write via data_access.py.
    """
    config = get_business_status_config(status_field)
    if config is None:
        return BusinessStatusFiledUntilUpdate(
            status="ignored",
            reason="unsupported_status_field",
            status_field=status_field,
        )

    if after.get(status_field) == before.get(status_field):
        return BusinessStatusFiledUntilUpdate(
            status="ignored",
            reason="status_field_not_changed",
            status_field=status_field,
        )

    if not is_business_completed_transition(
        before.get(status_field),
        after.get(status_field),
    ):
        return BusinessStatusFiledUntilUpdate(
            status="ignored",
            reason="not_exact_completed_transition",
            status_field=status_field,
        )

    engagement_type = normalize_select_value(after.get("Type"))
    expected_type = config["type"]
    company_field = config["company_field"]
    company = normalize_select_value(after.get("Company"))
    period_end = parse_date(after.get("Period End"))

    if engagement_type != expected_type:
        return BusinessStatusFiledUntilUpdate(
            status="needs_review",
            reason="type_status_mismatch",
            status_field=status_field,
            company=company or None,
            company_field=company_field,
            period_end=period_end,
            review_reason=(
                f"{status_field} completed but Type is "
                f"{engagement_type or '<blank>'}, expected {expected_type}"
            ),
        )

    if not company:
        return BusinessStatusFiledUntilUpdate(
            status="needs_review",
            reason="missing_company",
            status_field=status_field,
            company_field=company_field,
            period_end=period_end,
            review_reason=f"{status_field} completed but Company is blank",
        )

    if period_end is None:
        return BusinessStatusFiledUntilUpdate(
            status="needs_review",
            reason="invalid_period_end",
            status_field=status_field,
            company=company,
            company_field=company_field,
            review_reason=f"{status_field} completed but Period End is blank or invalid",
        )

    next_filed_until = calculate_only_forward_filed_until(
        existing_filed_until,
        period_end,
    )
    if next_filed_until is None:
        return BusinessStatusFiledUntilUpdate(
            status="skipped",
            reason="not_after_existing_filed_until",
            status_field=status_field,
            company=company,
            company_field=company_field,
            period_end=period_end,
        )

    return BusinessStatusFiledUntilUpdate(
        status="update",
        reason="completed_status_advances_filed_until",
        status_field=status_field,
        company=company,
        company_field=company_field,
        period_end=period_end,
        fields={company_field: period_end.isoformat()},
    )


def calculate_next_engagement(completed: dict) -> PeriodAdvanceResult:
    """
    Calculate the next engagement from a completed engagement dict.

    Required input keys: Type, Company, Frequency, Period End.
    """
    engagement_type = normalize_select_value(completed.get("Type"))
    company = normalize_select_value(completed.get("Company"))
    frequency = normalize_select_value(completed.get("Frequency"))
    period_end = parse_date(completed.get("Period End"))

    missing = []
    if engagement_type not in ADVANCE_TYPES:
        missing.append("Type")
    if not company:
        missing.append("Company")
    if frequency not in ADVANCE_FREQUENCIES:
        missing.append("Frequency")
    if period_end is None:
        missing.append("Period End")

    if missing:
        raise ValueError("Cannot advance engagement; missing/invalid: " + ", ".join(missing))

    next_start, next_end = next_period(period_end, frequency)
    due_date = calculate_due_date(engagement_type, next_start, next_end, frequency)

    return PeriodAdvanceResult(
        engagement_type=engagement_type,
        company=company,
        frequency=frequency,
        period_start=next_start,
        period_end=next_end,
        due_date=due_date,
        status=STATUS_NOT_STARTED,
        source="Auto",
    )


def build_next_engagement_fields(next_period_result: PeriodAdvanceResult) -> dict:
    """Build NocoDB-ready fields for the next engagement."""
    name = (
        f"{next_period_result.company} "
        f"{next_period_result.engagement_type} "
        f"{next_period_result.period_end.isoformat()}"
    )
    fields = {
        "Name": name,
        "Type": next_period_result.engagement_type,
        "Company": next_period_result.company,
        "Frequency": next_period_result.frequency,
        "Period Start": next_period_result.period_start.isoformat(),
        "Period End": next_period_result.period_end.isoformat(),
        "Status": next_period_result.status,
        "Source": next_period_result.source,
    }
    if next_period_result.due_date:
        fields["Due Date"] = next_period_result.due_date.isoformat()
    return fields


def build_company_workbench_fields(next_period_result: PeriodAdvanceResult) -> dict:
    """Build a minimal Companies Workbench summary from the next engagement."""
    action = (
        f"Prepare {next_period_result.engagement_type} "
        f"for {next_period_result.period_end.isoformat()}"
    )
    if next_period_result.due_date:
        action += f" by {next_period_result.due_date.isoformat()}"

    return {
        "Workbench Priority": "P4 OK",
        "Workbench Next Action": action,
        "Workbench Next Action Date": (
            next_period_result.due_date.isoformat()
            if next_period_result.due_date else None
        ),
    }


def advance_completed_engagement(after: dict, before: dict) -> dict:
    """
    Advance a recurring engagement after Status changes to Complete.

    Returns a small action summary. All writes go through data_access.py.
    """
    if not is_complete_transition(before.get("Status"), after.get("Status")):
        return {"status": "ignored", "reason": "not_complete_transition"}

    next_result = calculate_next_engagement(after)

    if data_access.recurring_engagement_exists(
        next_result.company,
        next_result.engagement_type,
        next_result.period_end,
    ):
        return {
            "status": "skipped",
            "reason": "next_engagement_exists",
            "company": next_result.company,
            "type": next_result.engagement_type,
            "period_end": next_result.period_end.isoformat(),
        }

    next_fields = build_next_engagement_fields(next_result)
    created = data_access.create_recurring_engagement(next_fields)

    data_access.create_event(
        engagement_id=after.get("Id") or after.get("id") or next_result.company,
        event_type=EVENT_TYPE_MANUAL_ACTION,
        description=(
            f"Created {next_result.engagement_type} "
            f"{next_result.period_end.isoformat()} after Complete"
        ),
        actor="NocoDB Webhook",
    )

    return {
        "status": "created",
        "company": next_result.company,
        "type": next_result.engagement_type,
        "period_end": next_result.period_end.isoformat(),
        "created": created,
        "company_workbench_updated": False,
        "company_workbench_update_reason": "deferred_to_workbench_refresh",
    }
