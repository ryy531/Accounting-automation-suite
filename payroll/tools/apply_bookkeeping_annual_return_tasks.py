"""Prepare Bookkeeping and Annual Return Engagements for a 3-month window.

The default is dry-run. Use ``--apply`` for the approved live migration.
Rows are deduplicated by Company + Type + Period End and every created row
gets an append-only Event.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "_lib"))

import config.settings as settings
import data_access
from connectors.nocodb_client import NocoDBApiError, NocoDBClient
from server import bookkeeping_annual_automation
from server.period_automation import add_months, parse_date


try:
    TIMEZONE = ZoneInfo(settings.PAYROLL_TIMEZONE)
except ZoneInfoNotFoundError:
    TIMEZONE = timezone(timedelta(hours=-6), name="America/Regina")

BOOKKEEPING = "Bookkeeping"
ANNUAL_RETURN = "Annual Return"
URGENT_FIELD = "Urgent"
STATUS_NOT_STARTED = "stage | not started"
STATUS_COMPLETE = "stage | completed"
EVENT_TYPE = "Manual Action"
BOOKKEEPING_VIEW_ID = "vwknh9y2qmqg5v7p"
ANNUAL_RETURN_VIEW_ID = "vw4a1xxn1t6czn7b"

MONTH_NUMBERS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def normalize(value) -> str:
    if isinstance(value, dict):
        value = value.get("value") or value.get("title") or ""
    return " ".join(str(value or "").split())


def projected_rows(table_id: str, fields: list[str]) -> list[dict]:
    """Read only the required fields in small pages to avoid NocoDB timeouts."""
    client = NocoDBClient(timeout=30)
    rows: list[dict] = []
    offset = 0
    limit = 25
    encoded_fields = quote(",".join(fields))
    while True:
        url = (
            f"{client._base_url}/api/v2/tables/{table_id}/records"
            f"?limit={limit}&offset={offset}&fields={encoded_fields}"
        )
        response = None
        for attempt in range(1, 6):
            try:
                response = client._get(url)
                break
            except NocoDBApiError:
                if attempt == 5:
                    raise
                time.sleep(attempt * 2)
        batch = (response or {}).get("list", [])
        rows.extend(batch)
        if len(batch) < limit:
            return rows
        offset += limit


def month_periods(today: date) -> list[tuple[date, date]]:
    current = today.replace(day=1)
    result = []
    for offset in (-1, 0, 1):
        start = add_months(current, offset)
        end = add_months(start, 1) - timedelta(days=1)
        result.append((start, end))
    return result


def priority_for(due_date: date, today: date) -> str:
    days = (due_date - today).days
    if days < 0:
        return "P1 Overdue"
    if days <= 7:
        return "P2 Due This Week"
    if days <= 30:
        return "P3 Due Soon"
    return "P4 OK"


def identity(row: dict) -> tuple[str, str, str]:
    return (
        normalize(row.get("Company")).casefold(),
        normalize(row.get("Type")),
        normalize(row.get("Period End")),
    )


def bookkeeping_candidates(company: dict) -> list[dict]:
    """Prepare only the next missing month until Bookkeeping reaches GST."""
    try:
        fields, _ = bookkeeping_annual_automation.build_next_bookkeeping_fields(
            company,
        )
    except ValueError:
        return []
    return [fields] if fields else []

def annual_return_candidates(company: dict, today: date) -> list[dict]:
    company_name = normalize(company.get("Legal Name"))
    month_label = normalize(company.get("Annual Return Month")).lower()[:3]
    annual_month = MONTH_NUMBERS.get(month_label)
    if not company_name or annual_month is None:
        return []

    rows = []
    for period_start, period_end in month_periods(today):
        if period_start.month != annual_month:
            continue
        due_date = period_end
        rows.append({
            "Name": f"{company_name} Annual Return {period_end.year}",
            "Type": ANNUAL_RETURN,
            settings.NOCODB_ENGAGEMENT_FIELD_ANNUAL_RETURN_STATUS: STATUS_NOT_STARTED,
            "Company": company_name,
            "Year": period_end.year,
            "Due Date": due_date.isoformat(),
            "Frequency": "Annual",
            "Period Start": period_start.isoformat(),
            "Period End": period_end.isoformat(),
            "Priority": priority_for(due_date, today),
            "Source": "Auto",
            "Notes": (
                "Created from Companies.Annual Return Month for the approved "
                f"3-month window on {today.isoformat()}"
            ),
            URGENT_FIELD: False,
        })
    return rows


def refresh_view_filters(today: date) -> dict[str, list[dict]]:
    lower, upper = month_periods(today)[0][0], month_periods(today)[-1][1]
    changes = {}
    for name, view_id in {
        BOOKKEEPING: BOOKKEEPING_VIEW_ID,
        ANNUAL_RETURN: ANNUAL_RETURN_VIEW_ID,
    }.items():
        changes[name] = data_access.update_tax_workbench_date_filters(
            view_id,
            lower_sub_op="daysAgo",
            lower_value=(today - lower).days,
            upper_sub_op="daysFromNow",
            upper_value=(upper - today).days,
        )
    return changes


def run(today: date, apply: bool = False) -> dict:
    companies = projected_rows(
        settings.NOCODB_COMPANIES_TABLE_ID,
        [
            "Legal Name",
            "client status",
            "Bookkeeping Filed Until",
            "GST Filed Until",
            "GST Due Next Period",
            "Annual Return Month",
        ],
    )
    engagements = projected_rows(
        settings.NOCODB_ENGAGEMENTS_TABLE_ID,
        ["Id", "Company", "Type", "Period End"],
    )
    existing = {identity(row) for row in engagements if identity(row)[2]}
    candidates: list[dict] = []

    for company in companies:
        if normalize(company.get("client status")).casefold() == "inactive":
            continue
        candidates.extend(bookkeeping_candidates(company))
        candidates.extend(annual_return_candidates(company, today))

    unique_candidates = []
    seen = set(existing)
    for fields in candidates:
        key = identity(fields)
        if key in seen:
            continue
        seen.add(key)
        unique_candidates.append(fields)

    summary = {
        "run_date": today.isoformat(),
        "dry_run": not apply,
        "companies_scanned": len(companies),
        "existing_engagements_scanned": len(engagements),
        "candidate_count": len(unique_candidates),
        "candidate_count_by_type": {
            name: sum(1 for row in unique_candidates if row["Type"] == name)
            for name in (BOOKKEEPING, ANNUAL_RETURN)
        },
        "candidate_status_counts": {
            status: sum(1 for row in unique_candidates if status in {row.get(settings.NOCODB_ENGAGEMENT_FIELD_BOOKKEEPING_STATUS), row.get(settings.NOCODB_ENGAGEMENT_FIELD_ANNUAL_RETURN_STATUS)})
            for status in (STATUS_NOT_STARTED, STATUS_COMPLETE)
        },
        "created": [],
        "errors": [],
        "view_filter_changes": refresh_view_filters(today) if apply else {},
    }

    if apply:
        for start in range(0, len(unique_candidates), 25):
            batch = unique_candidates[start:start + 25]
            try:
                created_rows = data_access.create_recurring_engagements(batch)
                events = []
                for fields, created in zip(batch, created_rows):
                    engagement_id = created.get("Id")
                    events.append({
                        "engagement_id": engagement_id or fields["Name"],
                        "event_type": EVENT_TYPE,
                        "description": (
                            f"Created {fields['Type']} Engagement for period ending "
                            f"{fields['Period End']}"
                        ),
                        "actor": "Bookkeeping/Annual Return Prepare",
                    })
                    summary["created"].append({
                        "id": engagement_id,
                        "type": fields["Type"],
                        "period_end": fields["Period End"],
                        "status": fields.get(settings.NOCODB_ENGAGEMENT_FIELD_BOOKKEEPING_STATUS) or fields.get(settings.NOCODB_ENGAGEMENT_FIELD_ANNUAL_RETURN_STATUS),
                    })
                data_access.create_events(events)
            except Exception as exc:
                summary["errors"].append({
                    "batch_start": start,
                    "batch_size": len(batch),
                    "error": str(exc),
                })

    summary["created_count"] = len(summary["created"])
    summary["error_count"] = len(summary["errors"])
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--date", help="YYYY-MM-DD; defaults to today")
    parser.add_argument("--output")
    args = parser.parse_args()
    today = date.fromisoformat(args.date) if args.date else datetime.now(TIMEZONE).date()
    summary = run(today, apply=args.apply)

    output = Path(args.output) if args.output else (
        ROOT / "payroll" / "_reports" /
        f"bookkeeping_annual_return_{'apply' if args.apply else 'dryrun'}_{today:%Y%m%d}.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "output": str(output),
        "candidate_count_by_type": summary["candidate_count_by_type"],
        "candidate_status_counts": summary["candidate_status_counts"],
        "created_count": summary["created_count"],
        "error_count": summary["error_count"],
    }, ensure_ascii=False))
    return 1 if summary["error_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())






