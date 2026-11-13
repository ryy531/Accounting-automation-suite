"""
data_access.py -- Unified data access layer for ExampleCo automation platform.

This is the ONLY module business code should import for data operations.
Business code must use this module instead of direct NocoDB REST calls.
"""

import logging
from datetime import date, datetime, timezone

import connectors.nocodb_client as nocodb
import config.settings as settings

logger = logging.getLogger(__name__)


# â”€â”€ Helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _normalize_company_name(name: str) -> str:
    """
    Normalize company name for comparison.

    - Strip leading/trailing whitespace
    - Collapse multiple whitespace to single space
    - Preserve case for exact matching (NocoDB is case-sensitive)

    Args:
        name: Raw company name

    Returns:
        Normalized company name
    """
    import re
    if not name:
        return ""
    # Strip and collapse multiple whitespace
    normalized = re.sub(r'\s+', ' ', name.strip())
    return normalized


def _normalize_select_value(value) -> str:
    """Normalize a NocoDB select value that may arrive as a dict or string."""
    if isinstance(value, dict):
        value = value.get("value") or value.get("title") or ""
    return " ".join(str(value or "").split())


def _assert_company_allows_engagement_creation(company_name: str) -> None:
    """Raise when a Companies row should not receive new Engagements."""
    normalized_name = _normalize_company_name(company_name)
    if not normalized_name:
        raise ValueError("cannot create Engagement: Company is blank")

    matches = [
        row for row in list_companies()
        if _normalize_company_name(row.get(settings.NOCODB_COMPANY_FIELD_LEGAL_NAME))
        == normalized_name
    ]
    if len(matches) != 1:
        raise ValueError(
            f"cannot create Engagement for {company_name!r}: "
            f"Companies match count is {len(matches)}"
        )

    client_status = _normalize_select_value(matches[0].get("client status")).casefold()
    if client_status == "inactive":
        raise ValueError(
            f"cannot create Engagement for inactive company: {company_name}"
        )


def _engagement_company_name(fields: dict) -> str:
    return _normalize_company_name(
        fields.get(settings.NOCODB_ENGAGEMENT_FIELD_COMPANY)
        or fields.get("Company")
    )


# â”€â”€ Companies â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def list_payroll_companies() -> list[dict]:
    """
    List all companies that are payroll clients.

    Returns:
        List of company dicts from NocoDB Companies table.
        Each dict contains: Id, Legal Name, Pay Frequency, Pay Period End Rule, etc.

    Raises:
        NocoDBError: If API call fails.
    """
    client = nocodb.NocoDBClient()
    result = client.list_rows(
        settings.NOCODB_COMPANIES_TABLE_ID,
        filters=[(
            settings.NOCODB_COMPANY_FIELD_IS_PAYROLL,
            "boolean",
            True
        )],
    )
    companies = result.get("list", [])
    logger.info(f"Found {len(companies)} payroll clients in NocoDB")
    return companies


def list_companies() -> list[dict]:
    """List all Companies rows for recurring engagement preparation."""
    client = nocodb.NocoDBClient()
    result = client.list_rows(settings.NOCODB_COMPANIES_TABLE_ID)
    return result.get("list", [])


def get_company_legal_name(company_id: int) -> str | None:
    """
    Get legal name of a company by ID.

    Args:
        company_id: NocoDB Company row Id

    Returns:
        Legal Name string or None if not found.
    """
    client = nocodb.NocoDBClient()
    result = client.list_rows(
        settings.NOCODB_COMPANIES_TABLE_ID,
        filters=[("Id", "eq", company_id)],
        size=1,
    )
    rows = result.get("list", [])
    if rows:
        return rows[0].get(settings.NOCODB_COMPANY_FIELD_LEGAL_NAME)
    return None


def extract_payroll_schedules(company: dict) -> tuple[list[dict], list[dict]]:
    """
    Extract enabled payroll schedules from a Companies row.

    Returns:
        (schedules, skipped), where schedules contain slot, frequency, and
        period_end_rule. Skipped entries describe incomplete optional schedules.
    """
    schedules = []
    skipped = []

    primary_frequency = _normalize_select_value(
        company.get(settings.NOCODB_COMPANY_FIELD_PAY_FREQUENCY)
    )
    if primary_frequency:
        schedules.append({
            "slot": 1,
            "frequency": primary_frequency,
            "period_end_rule": company.get(
                settings.NOCODB_COMPANY_FIELD_PAY_PERIOD_RULE
            ),
        })

    schedule2_active = bool(
        company.get(settings.NOCODB_COMPANY_FIELD_PAYROLL_SCHEDULE_2_ACTIVE)
    )
    if schedule2_active:
        schedule2_frequency = _normalize_select_value(
            company.get(settings.NOCODB_COMPANY_FIELD_PAYROLL_SCHEDULE_2_FREQUENCY)
        )
        if schedule2_frequency:
            schedules.append({
                "slot": 2,
                "frequency": schedule2_frequency,
                "period_end_rule": company.get(
                    settings.NOCODB_COMPANY_FIELD_PAYROLL_SCHEDULE_2_PERIOD_END_RULE
                ),
            })
        else:
            skipped.append({
                "slot": 2,
                "reason": "Schedule 2 active but frequency is blank",
            })

    return schedules, skipped


# â”€â”€ Engagements â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def list_payroll_engagements() -> list[dict]:
    """
    List all payroll engagements.

    Returns:
        List of engagement dicts from NocoDB Engagements table.
        Filters: Type = Payroll.

    Raises:
        NocoDBError: If API call fails.
    """
    client = nocodb.NocoDBClient()
    result = client.list_rows(
        settings.NOCODB_ENGAGEMENTS_TABLE_ID,
    )

    # Filter Type = Payroll in Python (simpler than API filter for now)
    all_engagements = result.get("list", [])
    payroll_engagements = [
        e for e in all_engagements
        if e.get(settings.NOCODB_ENGAGEMENT_FIELD_TYPE) ==
           settings.NOCODB_ENGAGEMENT_TYPE_PAYROLL
    ]

    logger.info(f"Found {len(payroll_engagements)} payroll engagements in NocoDB")
    return payroll_engagements


def engagement_exists(company_name: str, period_end: date) -> bool:
    """
    Check if a payroll engagement already exists for the given company and period.

    Args:
        company_name: Legal Name of the company (will be normalized)
        period_end: Payroll Period End date

    Returns:
        True if engagement exists, False otherwise.

    Raises:
        NocoDBError: If API call fails.
    """
    client = nocodb.NocoDBClient()

    # Normalize input company name
    normalized_input = _normalize_company_name(company_name)

    # List all payroll engagements and filter in Python
    result = client.list_rows(
        settings.NOCODB_ENGAGEMENTS_TABLE_ID,
    )

    all_engagements = result.get("list", [])
    period_end_str = period_end.isoformat()

    # Filter for matching Type=Payroll, Company=company_name, Period End=period_end
    for engagement in all_engagements:
        if (engagement.get(settings.NOCODB_ENGAGEMENT_FIELD_TYPE) ==
                settings.NOCODB_ENGAGEMENT_TYPE_PAYROLL and
                _normalize_company_name(
                    engagement.get(settings.NOCODB_ENGAGEMENT_FIELD_COMPANY, "")
                ) == normalized_input and
                engagement.get(settings.NOCODB_ENGAGEMENT_FIELD_PERIOD_END) ==
                period_end_str):
            return True

    return False


def get_last_payroll_period_end(company_name: str) -> date | None:
    """
    Get the most recent payroll period end date for a company.

    Args:
        company_name: Legal Name of the company (will be normalized)

    Returns:
        Most recent Payroll Period End date, or None if no payroll engagements exist.

    Raises:
        NocoDBError: If API call fails.
    """
    client = nocodb.NocoDBClient()

    # Normalize input company name
    normalized_input = _normalize_company_name(company_name)

    result = client.list_rows(
        settings.NOCODB_ENGAGEMENTS_TABLE_ID,
        order_by=f"-{settings.NOCODB_ENGAGEMENT_FIELD_PERIOD_END}",
    )

    all_engagements = result.get("list", [])

    # Filter for Type=Payroll and Company=company_name, find max period end
    most_recent = None
    for engagement in all_engagements:
        if (engagement.get(settings.NOCODB_ENGAGEMENT_FIELD_TYPE) ==
                settings.NOCODB_ENGAGEMENT_TYPE_PAYROLL and
                _normalize_company_name(
                    engagement.get(settings.NOCODB_ENGAGEMENT_FIELD_COMPANY, "")
                ) == normalized_input):
            period_end_str = engagement.get(
                settings.NOCODB_ENGAGEMENT_FIELD_PERIOD_END
            )
            if period_end_str:
                try:
                    period_end = date.fromisoformat(period_end_str)
                    if most_recent is None or period_end > most_recent:
                        most_recent = period_end
                except ValueError:
                    logger.warning(
                        f"Invalid period_end format: {period_end_str}"
                    )

    return most_recent


def create_payroll_engagement(company_name: str, period_start: date,
                              period_end: date, frequency: str) -> dict:
    """
    Create a new payroll engagement.

    Args:
        company_name: Legal Name of the company
        period_start: Payroll Period Start date
        period_end: Payroll Period End date
        frequency: Pay Frequency (Monthly, Semi-monthly, etc.)

    Returns:
        Created engagement dict from NocoDB.

    Raises:
        NocoDBError: If API call fails.

    Note:
        Creates engagement with:
        - Name: "{company_name} {period_end} Payroll"
        - Type: "Payroll"
        - Status: "Payroll | Waiting Info"
        - Company: company_name (text field, not relation)
        - Payroll Period Start: period_start
        - Payroll Period End: period_end
        - Assigned To: (empty)
    """
    import server.payroll_periods as payroll_periods

    _assert_company_allows_engagement_creation(company_name)
    client = nocodb.NocoDBClient()

    # Format engagement name
    engagement_name = payroll_periods.format_engagement_name(
        company_name, period_end
    )

    # Build fields dict
    fields = {
        settings.NOCODB_ENGAGEMENT_FIELD_NAME: engagement_name,
        settings.NOCODB_ENGAGEMENT_FIELD_TYPE:
            settings.NOCODB_ENGAGEMENT_TYPE_PAYROLL,
        settings.NOCODB_ENGAGEMENT_FIELD_STATUS:
            settings.NOCODB_ENGAGEMENT_STATUS_WAITING_INFO,
        settings.NOCODB_ENGAGEMENT_FIELD_COMPANY: company_name,
        settings.NOCODB_ENGAGEMENT_FIELD_PERIOD_START:
            period_start.isoformat(),
        settings.NOCODB_ENGAGEMENT_FIELD_PERIOD_END:
            period_end.isoformat(),
        # Assigned To: leave empty (default)
    }

    result = client.create_row(
        settings.NOCODB_ENGAGEMENTS_TABLE_ID,
        fields
    )

    logger.info(f"Created payroll engagement: {engagement_name}")
    return result


# â”€â”€ Generic Engagement period automation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def list_engagements() -> list[dict]:
    """List all Engagements rows."""
    client = nocodb.NocoDBClient()
    result = client.list_rows(settings.NOCODB_ENGAGEMENTS_TABLE_ID)
    return result.get("list", [])


def recurring_engagement_exists(company: str, engagement_type: str,
                                period_end: date) -> bool:
    """
    Check whether a recurring engagement already exists.

    Uses Python filtering to avoid depending on NocoDB filter syntax details.
    """
    period_end_str = period_end.isoformat()
    for row in list_engagements():
        if (
            str(row.get("Company", "")).strip() == str(company).strip()
            and str(row.get("Type", "")).strip() == str(engagement_type).strip()
            and str(row.get("Period End", "")).strip() == period_end_str
        ):
            return True
    return False


def create_recurring_engagement(fields: dict) -> dict:
    """Create a GST/PST/T2/Bookkeeping engagement."""
    _assert_company_allows_engagement_creation(_engagement_company_name(fields))
    client = nocodb.NocoDBClient()
    result = client.create_row(settings.NOCODB_ENGAGEMENTS_TABLE_ID, fields)
    logger.info(
        "Created recurring engagement: %s",
        fields.get("Name", "<unnamed>")
    )
    return result


def create_recurring_engagements(fields_list: list[dict]) -> list[dict]:
    """Create recurring Engagements in one NocoDB batch."""
    if not fields_list:
        return []
    allowed_fields = []
    for fields in fields_list:
        company_name = _engagement_company_name(fields)
        try:
            _assert_company_allows_engagement_creation(company_name)
        except ValueError as exc:
            logger.warning("Skipping recurring Engagement create: %s", exc)
            continue
        allowed_fields.append(fields)
    if not allowed_fields:
        return []
    client = nocodb.NocoDBClient(timeout=30)
    url = (
        f"{client._base_url}/api/v2/tables/"
        f"{settings.NOCODB_ENGAGEMENTS_TABLE_ID}/records"
    )
    return client._post(url, allowed_fields)

def update_tax_workbench_date_filters(
    view_id: str,
    *,
    lower_sub_op: str | None,
    lower_value: int | None,
    upper_sub_op: str,
    upper_value: int,
) -> list[dict]:
    """Update existing Due Date filters to native relative-month values."""
    client = nocodb.NocoDBClient()
    metadata = client.get_table_metadata(settings.NOCODB_ENGAGEMENTS_TABLE_ID)
    due_column = next(
        (
            column for column in metadata.get("columns", [])
            if column.get("title") == settings.NOCODB_ENGAGEMENT_FIELD_DUE_DATE
        ),
        None,
    )
    if due_column is None:
        raise RuntimeError("Engagements Due Date column not found")

    filters = client.list_view_filters(view_id)
    due_filters = {
        item.get("comparison_op"): item
        for item in filters
        if item.get("fk_column_id") == due_column.get("id")
        and item.get("comparison_op") in {"ge", "le"}
    }

    required = {"le": (upper_sub_op, str(upper_value))}
    if lower_sub_op is not None:
        required["ge"] = (lower_sub_op, str(lower_value))

    missing = sorted(set(required) - set(due_filters))
    if missing:
        raise RuntimeError(
            f"View {view_id} is missing Due Date filter(s): {', '.join(missing)}"
        )

    changes = []
    for operator, (sub_op, value) in required.items():
        current_sub_op = due_filters[operator].get("comparison_sub_op")
        current_value = str(due_filters[operator].get("value") or "")
        if current_sub_op == sub_op and current_value == value:
            continue
        client.update_view_filter(
            due_filters[operator]["id"],
            {"comparison_sub_op": sub_op, "value": value},
        )
        changes.append({
            "filter_id": due_filters[operator]["id"],
            "operator": operator,
            "before_sub_op": current_sub_op,
            "before_value": current_value,
            "after_sub_op": sub_op,
            "after_value": value,
        })
    return changes


def list_companies_by_legal_name(company_name: str) -> list[dict]:
    """Return Companies rows whose Legal Name exactly matches company_name."""
    client = nocodb.NocoDBClient()
    result = client.list_rows(
        settings.NOCODB_COMPANIES_TABLE_ID,
        filters=[(settings.NOCODB_COMPANY_FIELD_LEGAL_NAME, "eq", company_name)],
    )
    return result.get("list", [])


def update_company_by_legal_name(company_name: str, fields: dict) -> dict | None:
    """
    Update Companies fields by exact Legal Name.

    Returns None when no unique company row exists.
    """
    client = nocodb.NocoDBClient()
    rows = list_companies_by_legal_name(company_name)
    if len(rows) != 1:
        logger.warning(
            "Company update skipped for %s: matches=%d",
            company_name,
            len(rows),
        )
        return None
    return client.update_row(
        settings.NOCODB_COMPANIES_TABLE_ID,
        rows[0]["Id"],
        fields,
    )


def update_company_workbench_by_name(company_name: str, fields: dict) -> dict | None:
    """
    Update Companies Workbench fields by Legal Name.

    Returns None when no unique company row exists.
    """
    return update_company_by_legal_name(company_name, fields)


# â”€â”€ Generic record update (bot field-edit tool) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

_TABLE_NAME_TO_ID = {
    "Companies": lambda: settings.NOCODB_COMPANIES_TABLE_ID,
    "Engagements": lambda: settings.NOCODB_ENGAGEMENTS_TABLE_ID,
    "People": lambda: settings.NOCODB_PEOPLE_TABLE_ID,
}


def _table_id(table: str) -> str:
    """Map a known table name to its NocoDB table ID. Raises ValueError otherwise."""
    try:
        return _TABLE_NAME_TO_ID[table]()
    except KeyError:
        raise ValueError(
            f"unknown table: {table!r}. Must be one of {sorted(_TABLE_NAME_TO_ID)}"
        ) from None


def get_protected_field_names(table: str) -> set[str]:
    """
    Return field titles on `table` that the generic update tool must never write to.

    Determined live from NocoDB column types (uidt), not a hardcoded field list,
    so new computed/system fields added later in the NocoDB web UI are
    automatically protected with no code change.
    """
    client = nocodb.NocoDBClient()
    metadata = client.get_table_metadata(_table_id(table))
    return {
        column["title"]
        for column in metadata.get("columns", [])
        if column.get("uidt") in settings.NOCODB_PROTECTED_FIELD_TYPES
    }


def update_record(table: str, record_id: int, fields: dict, actor: str = "Bot") -> dict:
    """
    Update arbitrary content fields on a Companies/Engagements/People row.

    Rejects writes to fields that don't exist on the table (NocoDB silently
    no-ops on an unknown column name, which would otherwise look like a
    successful write) and to system-managed or computed fields (see
    get_protected_field_names). Logs an Event with the actor and the exact
    field/value diff for traceability.
    """
    client = nocodb.NocoDBClient()
    columns = client.get_table_metadata(_table_id(table)).get("columns", [])
    all_field_names = {column["title"] for column in columns}
    protected_field_names = {
        column["title"] for column in columns
        if column.get("uidt") in settings.NOCODB_PROTECTED_FIELD_TYPES
    }

    unknown = set(fields) - all_field_names
    if unknown:
        raise ValueError(
            f"unknown field(s) for {table}: {sorted(unknown)}; "
            f"available fields: {sorted(all_field_names)}"
        )

    blocked = set(fields) & protected_field_names
    if blocked:
        raise ValueError(f"cannot write protected field(s): {sorted(blocked)}")

    result = client.update_row(_table_id(table), record_id, fields)

    create_event(
        engagement_id=f"{table}:{record_id}",
        event_type="Manual Action",  # Events.Event Type is a fixed SingleSelect; "Record Updated" is not a valid option
        description=f"{table} {record_id}: "
        + ", ".join(f"{k} -> {v}" for k, v in fields.items()),
        actor=actor,
    )

    logger.info("Bot updated %s %s: %s", table, record_id, sorted(fields))
    return result


# â”€â”€ Events (Optional, for future use) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def create_event(engagement_id: int | str, event_type: str,
                 description: str, actor: str = "System") -> dict:
    """
    Create an event in the Events table.

    Args:
        engagement_id: Text reference for the engagement. Current Events schema
            stores this as flat text, not a relation.
        event_type: Event Type (Status Changed, Emailed, etc.)
        description: Event description
        actor: Who/what triggered this event (default: "System")

    Returns:
        Created event dict from NocoDB.

    Raises:
        NocoDBError: If API call fails.

    Note:
        Events are append-only. This creates a new event record.
    """
    client = nocodb.NocoDBClient()

    # For now, Engagement is a text field in Events table
    # TODO: Change to relation field when schema is updated
    fields = {
        settings.NOCODB_EVENT_FIELD_TIMESTAMP:
            datetime.now(timezone.utc).date().isoformat(),
        settings.NOCODB_EVENT_FIELD_ENGAGEMENT: str(engagement_id),
        settings.NOCODB_EVENT_FIELD_EVENT_TYPE: event_type,
        settings.NOCODB_EVENT_FIELD_DESCRIPTION: description,
        settings.NOCODB_EVENT_FIELD_ACTOR: actor,
        # Timestamp: auto-filled by NocoDB
    }

    result = client.create_row(
        settings.NOCODB_EVENTS_TABLE_ID,
        fields
    )

    logger.info(f"Created event: {event_type} for engagement {engagement_id}")
    return result


# â”€â”€ Bot (WhatsApp) helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def create_events(events: list[dict]) -> list[dict]:
    """Append multiple Events in one NocoDB batch."""
    if not events:
        return []
    rows = []
    for event in events:
        rows.append({
            settings.NOCODB_EVENT_FIELD_TIMESTAMP:
                datetime.now(timezone.utc).date().isoformat(),
            settings.NOCODB_EVENT_FIELD_ENGAGEMENT:
                str(event["engagement_id"]),
            settings.NOCODB_EVENT_FIELD_EVENT_TYPE: event["event_type"],
            settings.NOCODB_EVENT_FIELD_DESCRIPTION: event["description"],
            settings.NOCODB_EVENT_FIELD_ACTOR: event.get("actor", "System"),
        })
    client = nocodb.NocoDBClient(timeout=30)
    url = (
        f"{client._base_url}/api/v2/tables/"
        f"{settings.NOCODB_EVENTS_TABLE_ID}/records"
    )
    return client._post(url, rows)

def search_companies_by_ref(query: str) -> list[dict]:
    """
    Search companies by a short reference string.

    - Exactly 4 digits â†’ match any company whose Legal Name contains those digits.
      Handles numeric companies like "101233635 Saskatchewan Ltd." referenced as "3635".
    - Anything else â†’ case-insensitive substring match on Legal Name or Operating Name.
    """
    import re
    q = query.strip()
    all_companies = list_companies()

    if re.match(r"^\d{4}$", q):
        return [c for c in all_companies if q in (c.get("Legal Name") or "")]

    q_lower = q.lower()
    return [
        c for c in all_companies
        if q_lower in (c.get("Legal Name") or "").lower()
        or q_lower in (c.get("Operating Name") or "").lower()
    ]


def list_people() -> list[dict]:
    """List all People rows."""
    client = nocodb.NocoDBClient()
    result = client.list_rows(settings.NOCODB_PEOPLE_TABLE_ID)
    return result.get("list", [])


def search_people_by_name(query: str) -> list[dict]:
    """Case-insensitive substring search on People's Full Name field."""
    q_lower = query.strip().lower()
    return [
        p for p in list_people()
        if q_lower in (p.get("Full Name") or "").lower()
    ]


def find_active_engagements(
    company_name: str,
    eng_type: str,
    done_statuses: set,
) -> list[dict]:
    """
    Return engagements for a company+type that are not in a terminal status.

    done_statuses: strings that indicate the engagement is finished (e.g.
    "Payroll | Completed" for Payroll, "stage | completed" for T2/GST/PST).
    Checked as substrings so it works for both SingleSelect strings and
    comma-joined MultiSelect strings.
    """
    _type_to_status_field = {
        "Payroll": settings.NOCODB_ENGAGEMENT_FIELD_STATUS,
        "T2": settings.NOCODB_ENGAGEMENT_FIELD_T2_STATUS,
        "GST": settings.NOCODB_ENGAGEMENT_FIELD_GST_STATUS,
        "PST": settings.NOCODB_ENGAGEMENT_FIELD_PST_STATUS,
    }
    status_field = _type_to_status_field.get(eng_type, settings.NOCODB_ENGAGEMENT_FIELD_STATUS)

    result = []
    for e in list_engagements():
        if (e.get("Company") or "").strip() != company_name.strip():
            continue
        if (e.get("Type") or "").strip() != eng_type:
            continue

        raw = e.get(status_field, "")
        if isinstance(raw, list):
            status_str = ",".join(str(v) for v in raw)
        elif isinstance(raw, dict):
            status_str = raw.get("value") or raw.get("title") or ""
        else:
            status_str = str(raw or "")

        if any(done in status_str for done in done_statuses):
            continue

        result.append(e)

    return result


def query_engagements(
    types: list[str] | None = None,
    priorities: list[str] | None = None,
    assigned_to: str | None = None,
    due_period: str | None = None,
    blocked: bool = False,
    review: bool = False,
    exclude_done: bool = True,
    limit: int = 25,
) -> list[dict]:
    """
    Filter active engagements for bot queries.

    types       -- e.g. ["PST", "GST"] â€” None means all types
    priorities  -- prefix match against Priority field, e.g. ["P0", "P1"]
    assigned_to -- exact match against Assigned To field
    due_period  -- "this_month" | "this_week" | "overdue"
    blocked     -- if True, include only engagements with a blocker | status
    review      -- if True, include only engagements at stage | review
    exclude_done -- skip completed/archived engagements (default True)
    limit       -- max rows returned
    """
    import calendar
    from datetime import date, timedelta

    today = date.today()

    if due_period == "this_month":
        period_start = today.replace(day=1)
        period_end = today.replace(
            day=calendar.monthrange(today.year, today.month)[1]
        )
    elif due_period == "this_week":
        period_start = today
        period_end = today + timedelta(days=7)
    elif due_period == "overdue":
        period_start = None
        period_end = today - timedelta(days=1)
    else:
        period_start = None
        period_end = None

    _done_by_type = {
        "Payroll": {settings.NOCODB_ENGAGEMENT_STATUS_COMPLETED,
                    settings.NOCODB_ENGAGEMENT_STATUS_ARCHIVED},
        "T2":  {"stage | completed", "stage | archived"},
        "GST": {"stage | completed", "stage | archived"},
        "PST": {"stage | completed", "stage | archived"},
    }
    _status_field_by_type = {
        "Payroll": settings.NOCODB_ENGAGEMENT_FIELD_STATUS,
        "T2":  settings.NOCODB_ENGAGEMENT_FIELD_T2_STATUS,
        "GST": settings.NOCODB_ENGAGEMENT_FIELD_GST_STATUS,
        "PST": settings.NOCODB_ENGAGEMENT_FIELD_PST_STATUS,
    }

    results = []
    for e in list_engagements():
        eng_type = (e.get("Type") or "").strip()

        if types and eng_type not in types:
            continue

        sf = _status_field_by_type.get(eng_type, settings.NOCODB_ENGAGEMENT_FIELD_STATUS)
        raw = e.get(sf, "")
        status_str = (
            ",".join(str(v) for v in raw)
            if isinstance(raw, list)
            else str(raw or "")
        )

        if exclude_done:
            done_set = _done_by_type.get(eng_type, set())
            if any(d in status_str for d in done_set):
                continue

        priority_raw = e.get(settings.NOCODB_ENGAGEMENT_FIELD_PRIORITY) or ""
        priority = (
            priority_raw.get("value") if isinstance(priority_raw, dict)
            else str(priority_raw)
        ).strip()

        if blocked:
            if "blocker |" not in status_str:
                continue
        elif review:
            if "stage | review" not in status_str:
                continue
        elif priorities:
            if not any(priority.startswith(p) for p in priorities):
                continue

        if assigned_to:
            at_raw = e.get(settings.NOCODB_ENGAGEMENT_FIELD_ASSIGNED_TO) or ""
            at = (
                at_raw.get("value") if isinstance(at_raw, dict) else str(at_raw)
            ).strip()
            if at.lower() != assigned_to.lower():
                continue

        # Payroll engagements never get "Due Date" populated (only set by the
        # GST/PST/T2 tax_engagement_prepare automation) -- they carry their
        # due reference in "Payroll Period End" instead, same fallback as
        # bot_routes.find_engagement's period_end lookup.
        due_raw = (
            e.get(settings.NOCODB_ENGAGEMENT_FIELD_DUE_DATE)
            or e.get(settings.NOCODB_ENGAGEMENT_FIELD_PERIOD_END)
            or ""
        )
        if due_raw:
            try:
                due = date.fromisoformat(str(due_raw))
            except ValueError:
                due = None
        else:
            due = None

        if period_start and (due is None or due < period_start):
            continue
        if period_end and (due is None or due > period_end):
            continue

        results.append({
            "id": e.get("Id"),
            "name": e.get("Name", ""),
            "company": e.get("Company", ""),
            "type": eng_type,
            "priority": priority,
            "due_date": due_raw,
            "current_status": status_str,
            "assigned_to": str(
                (e.get(settings.NOCODB_ENGAGEMENT_FIELD_ASSIGNED_TO) or "")
            ).strip(),
        })

        if len(results) >= limit:
            break

    return results


def update_engagement_status(
    engagement_id: int,
    status_field: str,
    new_status: str,
    actor: str = "Bot",
    company_ref: str = "",
) -> dict:
    """
    Update an engagement's status field and append a Status Changed event.

    For MultiSelect fields (T2/GST/PST Status): new_status is written as the
    entire field value, clearing all existing stages and blockers. This is
    required to trigger the webhook auto-calculation scripts.
    """
    client = nocodb.NocoDBClient()
    result = client.update_row(
        settings.NOCODB_ENGAGEMENTS_TABLE_ID,
        engagement_id,
        {status_field: new_status},
    )

    create_event(
        engagement_id=company_ref or str(engagement_id),
        event_type="Status Changed",
        description=f"{status_field} â†’ {new_status} (via WhatsApp bot)",
        actor=actor,
    )

    logger.info("Bot updated engagement %s: %s â†’ %s", engagement_id, status_field, new_status)
    return result


# â”€â”€ People-Company bridging (Owners -> People LinksToRecord) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
#
# update_record() above deliberately refuses to write LinkToAnotherRecord
# fields (see NOCODB_PROTECTED_FIELD_TYPES). This is the one sanctioned path
# for writing the Companies.Owners -> People relation. Bypasses the generic
# protected-field check by design.

def link_person_to_company(
    company_id: int,
    person_id: int,
    *,
    role: str = "",
    actor: str = "System",
    dry_run: bool = False,
) -> dict:
    """
    Link a Person to a Company via the Owners -> People LinksToRecord field.

    role: not persisted to NocoDB (a mm relation carries no per-link
    attributes) -- used only to make the Event description readable
    (e.g. "linked as secondary_owner"). Not queryable later.

    dry_run: only reads existing links and returns a preview; never calls
    the link-write endpoint and never writes an Event.

    Idempotent: if person_id is already linked, this is a no-op (still
    returns a result, but does not write a duplicate Event).
    """
    field_id = settings.NOCODB_COMPANY_OWNERS_LINK_FIELD_ID
    if not field_id:
        raise ValueError(
            "NOCODB_COMPANY_OWNERS_LINK_FIELD_ID is not set -- "
            "Step 1b (create the relation field in NocoDB) must run first"
        )

    client = nocodb.NocoDBClient()
    existing = client.list_linked_records(
        settings.NOCODB_COMPANIES_TABLE_ID, field_id, company_id
    )
    already_linked = any(row.get("Id") == person_id for row in existing)

    if dry_run:
        return {
            "action": "noop" if already_linked else "link",
            "company_id": company_id,
            "person_id": person_id,
            "already_linked": already_linked,
        }

    if already_linked:
        logger.info(
            "Person %s already linked to Company %s, skipping", person_id, company_id
        )
        return {"action": "noop", "company_id": company_id, "person_id": person_id}

    client.link_records(
        settings.NOCODB_COMPANIES_TABLE_ID, field_id, company_id, [person_id]
    )

    role_suffix = f" as {role}" if role else ""
    create_event(
        engagement_id=f"Companies:{company_id}",
        event_type="Manual Action",  # Events.Event Type is a fixed SingleSelect; "Person Linked" is not a valid option
        description=f"Linked Person {person_id} to Company {company_id}{role_suffix}",
        actor=actor,
    )

    logger.info("Linked Person %s to Company %s", person_id, company_id)
    return {"action": "linked", "company_id": company_id, "person_id": person_id}


def list_people_for_company(company_id: int) -> list[dict]:
    """Read-only: People rows linked to a Company via the Owners relation field."""
    field_id = settings.NOCODB_COMPANY_OWNERS_LINK_FIELD_ID
    if not field_id:
        return []
    client = nocodb.NocoDBClient()
    return client.list_linked_records(
        settings.NOCODB_COMPANIES_TABLE_ID, field_id, company_id
    )


def get_person_for_engagement(engagement_id: int) -> list[dict]:
    """
    Read-only reverse lookup: Engagement -> Company (existing text-field
    join, unchanged) -> Person (via the Owners relation field).

    Does not touch Engagements.Company or Engagements.Person -- those stay
    plain text, per the D4 decision to protect the existing Payroll/Tax
    automation that depends on the string join. Returns [] when the
    engagement isn't found or its Company doesn't resolve to exactly one
    Companies row.
    """
    engagement = next(
        (e for e in list_engagements() if e.get("Id") == engagement_id), None
    )
    if not engagement:
        return []

    company_name = (engagement.get(settings.NOCODB_ENGAGEMENT_FIELD_COMPANY) or "").strip()
    if not company_name:
        return []

    companies = list_companies_by_legal_name(company_name)
    if len(companies) != 1:
        return []

    return list_people_for_company(companies[0]["Id"])


def create_person(full_name: str, email: str = "", actor: str = "System") -> dict:
    """
    Create a new People row (Full Name + optional Email only). Writes a
    Manual Action Event for traceability. Used by the Company-created
    webhook when no existing People row matches the new owner.
    """
    client = nocodb.NocoDBClient()
    fields = {"Full Name": full_name}
    if email:
        fields["Email"] = email
    result = client.create_row(settings.NOCODB_PEOPLE_TABLE_ID, fields)
    person_id = result[0]["Id"]  # create_row posts a bulk-create body, NocoDB returns a list

    create_event(
        engagement_id=f"People:{person_id}",
        event_type="Manual Action",
        description=f"Created Person '{full_name}'" + (f" ({email})" if email else ""),
        actor=actor,
    )
    logger.info("Created Person %s: %s", person_id, full_name)
    return {**fields, "Id": person_id}


def create_company(legal_name: str, owner_name: str = "", owner_email: str = "",
                    actor: str = "System") -> dict:
    """
    Create a minimal stub Companies row (Legal Name + Owner Name/Email
    only). Writes a Manual Action Event for traceability. Used by the
    Person-created webhook when no existing Companies row matches the
    new Person's Company text field. Deliberately minimal -- all other
    Companies fields (tax setup, frequencies, etc.) are left blank for
    manual completion, so downstream automation doesn't mistake this for
    a fully-configured client.
    """
    client = nocodb.NocoDBClient()
    fields = {settings.NOCODB_COMPANY_FIELD_LEGAL_NAME: legal_name}
    if owner_name:
        fields["Owner Name"] = owner_name
    if owner_email:
        fields["Owner Email"] = owner_email
    result = client.create_row(settings.NOCODB_COMPANIES_TABLE_ID, fields)
    company_id = result[0]["Id"]  # create_row posts a bulk-create body, NocoDB returns a list

    create_event(
        engagement_id=f"Companies:{company_id}",
        event_type="Manual Action",
        description=f"Created stub Company '{legal_name}' (Owner: {owner_name or '-'} / {owner_email or '-'})",
        actor=actor,
    )
    logger.info("Created stub Company %s: %s", company_id, legal_name)
    return {**fields, "Id": company_id}

