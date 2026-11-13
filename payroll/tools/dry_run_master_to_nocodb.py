"""
Dry-run Master Excel -> NocoDB sync planning.

This script is intentionally read-only for NocoDB:
- Uses GET requests only.
- Does not change schema.
- Does not modify the Master Excel workbook.
- Does not print passwords, tokens, keys, or raw report data to stdout.

Outputs JSON and Markdown reports under:
  payroll/_reports/master_sync_YYYYMMDD_HHMMSS/
"""

from __future__ import annotations

import json
import os
import re
import argparse
import csv
import calendar
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import msoffcrypto
import requests
from dotenv import load_dotenv
from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / "_lib" / "config" / ".env"
SCHEMA_SNAPSHOT = ROOT / "payroll" / "_doc" / "nocodb_current_state_snapshot_2026-06-03.json"
DEFAULT_MASTER_PATH = Path(
    r"\\file-server\Shared Folder\4. General - Scan-Notes-Form-PPT"
    r"\4. Accounting client list\#Business Master Client List.xlsx"
)
REPORT_ROOT = ROOT / "payroll" / "_reports"

REPORT_NAMES = [
    "company_fill_blank_updates",
    "company_create_candidates",
    "people_create_candidates",
    "people_update_candidates",
    "people_link_candidates",
    "engagement_create_candidates",
    "engagement_legacy_snapshot_candidates",
    "invalid_select_values",
    "invalid_field_values",
    "duplicate_company_groups",
    "weak_people_matches",
    "skipped_fields_with_reason",
]

EMPTY_MARKERS = {"", "na", "n/a", "none", "-", "--", "null"}


COMPANY_FIELD_MAP = {
    "Legal Name": "Legal Name",
    "Business Number": "Business Number",
    "Entity Number": "Entity Number",
    "Operating Name (previous Name)": "Operating Name",
    "Incorporate Date": "Incorporate Date",
    "Incorporation Jurisdiction": "Incorporation Jurisdiction",
    "Language": "Language",
    "Client Type": "Client Type",
    "Contract Services": "Contract Services",
    "Monthly Fee": "Monthly Fee",
    "B/B+ Charge": "B/B+ Charge",
    "(A) T2 / Self-employed Charge": "T2/Self-employed Charge",
    "Payment Method": "Payment Method",
    "T2 YE": "T2 YE",
    "GST Frequncy": "GST Frequency",
    "PST No.": "PST No.",
    "PST Frequency": "PST Frequency",
    "Owner Name": "Owner Name",
    "Owner Email": "Owner Email",
    "2nd Owner Name": "2nd Owner Name",
    "Payroll Frequency": "Pay Frequency",
}

COMPANY_SCHEMA_REQUIRED = {
    "WCB policy #": "WCB Policy #",
    "T Slips": "T Slips",
}

SELECT_OPTIONS = {
    "Companies.Language": {"Chinese", "English", "Vietnamese"},
    "Companies.Client Type": {"Annual", "Monthly"},
    "Companies.Payment Method": {"Cheque", "PAD", "E-Transfer"},
    "Companies.GST Frequency": {"Quarterly", "Annual", "Monthly"},
    "Companies.PST Frequency": {"Annual", "Quarterly", "Monthly"},
    "Companies.Pay Frequency": {"Monthly", "Yearly", "Semi-monthly", "Bi-weekly"},
    "People.Language": {"Chinese", "English", "Vietnamese", "1"},
}

SELECT_NORMALIZATION = {
    "Companies.Payment Method": {"pad": "PAD"},
}

OLD_BROAD_ENGAGEMENT_CANDIDATE_COUNT = 654
TIMEZONE = "America/Regina"
MONTH_ALIASES = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


@dataclass
class Context:
    report_dir: Path
    schema: dict[str, Any]
    table_fields: dict[str, dict[str, dict[str, Any]]]
    table_ids: dict[str, str]
    engagement_type_options: set[str]
    reference_date: date
    due_window_start: date
    due_window_end: date


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return " ".join(str(value).replace("\n", " ").split()).strip()


def is_blank(value: Any) -> bool:
    return normalize_text(value).casefold() in EMPTY_MARKERS


def normalize_key(value: Any) -> str:
    return re.sub(r"\s+", " ", normalize_text(value)).casefold()


def normalize_email(value: Any) -> str:
    return normalize_text(value).lower()


def looks_like_email(value: Any) -> bool:
    email = normalize_email(value)
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email))


def mask_email(value: Any) -> str:
    email = normalize_email(value)
    if "@" not in email:
        return ""
    user, domain = email.split("@", 1)
    return f"{user[:2]}***@{domain[:1]}***"


def parse_date(value: Any) -> str:
    if value is None or is_blank(value):
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            parsed = datetime(1899, 12, 30) + timedelta(days=float(value))
        except (OverflowError, ValueError):
            return ""
        if 1900 <= parsed.year <= 2100:
            return parsed.date().isoformat()
        return ""
    raw = normalize_text(value)
    raw = re.sub(
        r"^(\d{4})-([A-Za-z]{3,9})-(\d{1,2})$",
        lambda match: f"{match.group(1)}-{MONTH_ALIASES.get(match.group(2).casefold(), 0):02d}-{int(match.group(3)):02d}",
        raw,
    )
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y", "%Y-%m", "%Y"):
        try:
            parsed = datetime.strptime(raw, fmt)
            if fmt == "%Y":
                return f"{parsed.year}-12-31"
            if fmt == "%Y-%m":
                return f"{parsed.year}-{parsed.month:02d}-01"
            return parsed.date().isoformat()
        except ValueError:
            continue
    return ""


def compute_due_window(reference_date: date) -> tuple[date, date]:
    start = reference_date.replace(day=1)
    next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    after_next_month = (next_month.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start, after_next_month - timedelta(days=1)


def parse_reference_date(raw: str | None) -> date:
    if raw:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    return datetime.now(ZoneInfo(TIMEZONE)).date()


def in_due_window(due_date: str, start: date, end: date) -> bool:
    if not due_date:
        return False
    parsed = datetime.strptime(due_date, "%Y-%m-%d").date()
    return start <= parsed <= end


def annual_return_month(value: Any) -> int | None:
    raw = normalize_text(value).casefold()
    if not raw:
        return None
    match = re.search(r"\b([a-z]{3,9})\b", raw)
    if match and match.group(1) in MONTH_ALIASES:
        return MONTH_ALIASES[match.group(1)]
    if raw in MONTH_ALIASES:
        return MONTH_ALIASES[raw]
    return None


def parse_year(value: Any) -> int | None:
    parsed = parse_date(value)
    if parsed:
        return int(parsed[:4])
    raw = normalize_text(value)
    match = re.search(r"(20\d{2}|19\d{2})", raw)
    return int(match.group(1)) if match else None


def parse_number(value: Any) -> int | float | None:
    if value is None or is_blank(value):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return int(value) if float(value).is_integer() else float(value)
    cleaned = re.sub(r"[$,\s]", "", normalize_text(value))
    try:
        number = float(cleaned)
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_SNAPSHOT.read_text(encoding="utf-8-sig"))


def table_by_name(schema: dict[str, Any], name: str) -> dict[str, Any]:
    for table in schema.get("tables", []):
        if table.get("name") == name:
            return table
    raise KeyError(f"Missing table in snapshot: {name}")


def build_context(reference_date: date) -> Context:
    load_dotenv(ENV_PATH)
    schema = load_schema()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = REPORT_ROOT / f"master_sync_{timestamp}"
    report_dir.mkdir(parents=True, exist_ok=False)

    table_fields = {}
    table_ids = {}
    for name in ("Companies", "People", "Engagements", "Events"):
        table = table_by_name(schema, name)
        table_fields[name] = {field["title"]: field for field in table.get("fields", [])}
        table_ids[name] = table.get("table_id", "")

    table_ids["Companies"] = os.getenv("NOCODB_COMPANIES_TABLE_ID") or table_ids["Companies"]
    table_ids["People"] = os.getenv("NOCODB_PEOPLE_TABLE_ID") or table_ids["People"]
    table_ids["Engagements"] = os.getenv("NOCODB_ENGAGEMENTS_TABLE_ID") or table_ids["Engagements"]
    table_ids["Events"] = os.getenv("NOCODB_EVENTS_TABLE_ID") or table_ids["Events"]

    type_options = table_fields["Engagements"].get("Type", {}).get("options", "")
    engagement_type_options = {part.strip() for part in type_options.split(";") if part.strip()}
    due_window_start, due_window_end = compute_due_window(reference_date)

    return Context(
        report_dir=report_dir,
        schema=schema,
        table_fields=table_fields,
        table_ids=table_ids,
        engagement_type_options=engagement_type_options,
        reference_date=reference_date,
        due_window_start=due_window_start,
        due_window_end=due_window_end,
    )


def fetch_rows(table_id: str) -> list[dict[str, Any]]:
    base_url = os.getenv("NOCODB_URL", "").rstrip("/")
    token = os.getenv("NOCODB_TOKEN", "")
    if not base_url or not token or not table_id:
        raise RuntimeError("NocoDB URL/token/table id is not configured")

    rows: list[dict[str, Any]] = []
    offset = 0
    headers = {"xc-token": token, "Content-Type": "application/json"}
    while True:
        response = requests.get(
            f"{base_url}/api/v2/tables/{table_id}/records",
            headers=headers,
            params={"limit": 100, "offset": offset},
            timeout=15,
        )
        response.raise_for_status()
        batch = response.json().get("list", [])
        rows.extend(batch)
        if len(batch) < 100:
            break
        offset += 100
    return rows


def load_master_rows() -> list[dict[str, Any]]:
    password = os.getenv("CLIENT_MASTER_PASSWORD", "")
    master_path = Path(os.getenv("MASTER_EXCEL_PATH") or DEFAULT_MASTER_PATH)
    with master_path.open("rb") as file_obj:
        office = msoffcrypto.OfficeFile(file_obj)
        office.load_key(password=password)
        decrypted = BytesIO()
        office.decrypt(decrypted)
        decrypted.seek(0)
        workbook = load_workbook(decrypted, read_only=True, data_only=True)

    worksheet = workbook["Master"]
    headers = [
        normalize_text(value)
        for value in next(worksheet.iter_rows(min_row=1, max_row=1, max_col=39, values_only=True))
    ]

    rows: list[dict[str, Any]] = []
    for row_index, values in enumerate(
        worksheet.iter_rows(min_row=2, max_row=worksheet.max_row, max_col=39, values_only=True),
        start=2,
    ):
        record = {headers[i]: values[i] for i in range(len(headers)) if headers[i]}
        if any(not is_blank(value) for value in record.values()):
            record["_excel_row"] = row_index
            rows.append(record)
    workbook.close()
    return rows


def normalized_select(table_field: str, value: Any) -> tuple[str, str]:
    if is_blank(value):
        return "", ""
    raw = normalize_text(value)
    normalized = SELECT_NORMALIZATION.get(table_field, {}).get(raw.casefold(), raw)
    allowed = SELECT_OPTIONS.get(table_field)
    if allowed and normalized not in allowed:
        return normalized, "invalid_select"
    return normalized, ""


def company_value(master_field: str, company_field: str, value: Any) -> tuple[Any, str]:
    if is_blank(value):
        return None, "blank"
    if company_field in {"Incorporate Date"}:
        parsed = parse_date(value)
        return (parsed, "") if parsed else (None, "invalid_date")
    if company_field in {"Monthly Fee", "B/B+ Charge", "T2/Self-employed Charge"}:
        parsed_number = parse_number(value)
        return (parsed_number, "") if parsed_number is not None else (None, "invalid_number")
    if company_field in {"Language", "Client Type", "Payment Method", "GST Frequency", "PST Frequency", "Pay Frequency"}:
        return normalized_select(f"Companies.{company_field}", value)
    return normalize_text(value), ""


def add_invalid(invalid: dict[tuple[str, str, str], list[int]], table: str, field: str, value: Any, row: int) -> None:
    invalid[(table, field, normalize_text(value))].append(row)


def build_duplicate_groups(
    excel_rows: list[dict[str, Any]],
    companies: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    excel_legal = defaultdict(list)
    excel_bn = defaultdict(list)
    excel_entity = defaultdict(list)
    for row in excel_rows:
        if not is_blank(row.get("Legal Name")):
            excel_legal[normalize_key(row.get("Legal Name"))].append(row)
        if not is_blank(row.get("Business Number")):
            excel_bn[normalize_key(row.get("Business Number"))].append(row)
        if not is_blank(row.get("Entity Number")) and normalize_key(row.get("Entity Number")) != "na":
            excel_entity[normalize_key(row.get("Entity Number"))].append(row)

    for group_type, bucket, label_field in (
        ("excel_legal_name", excel_legal, "Legal Name"),
        ("excel_business_number", excel_bn, "Business Number"),
        ("excel_entity_number", excel_entity, "Entity Number"),
    ):
        for key, rows in bucket.items():
            if len(rows) > 1:
                groups.append(
                    {
                        "type": group_type,
                        "value": normalize_text(rows[0].get(label_field)),
                        "excel_rows": [row["_excel_row"] for row in rows],
                        "recommendation": "manual_confirm_before_apply",
                    }
                )

    nocodb_legal = defaultdict(list)
    for row in companies:
        if not is_blank(row.get("Legal Name")):
            nocodb_legal[normalize_key(row.get("Legal Name"))].append(row)
    for rows in nocodb_legal.values():
        if len(rows) > 1:
            groups.append(
                {
                    "type": "nocodb_legal_name",
                    "value": normalize_text(rows[0].get("Legal Name")),
                    "nocodb_ids": [row.get("Id") for row in rows],
                    "recommendation": "manual_confirm_before_apply",
                }
            )

    return groups


def plan_company_reports(
    ctx: Context,
    excel_rows: list[dict[str, Any]],
    companies: list[dict[str, Any]],
    reports: dict[str, list[dict[str, Any]]],
) -> None:
    invalid_select = defaultdict(list)
    invalid_field = defaultdict(list)
    company_fields = ctx.table_fields["Companies"]
    by_legal = defaultdict(list)
    by_bn = defaultdict(list)
    by_entity = defaultdict(list)
    for company in companies:
        by_legal[normalize_key(company.get("Legal Name"))].append(company)
        if not is_blank(company.get("Business Number")):
            by_bn[normalize_key(company.get("Business Number"))].append(company)
        if not is_blank(company.get("Entity Number")):
            by_entity[normalize_key(company.get("Entity Number"))].append(company)

    duplicate_excel_legal = {
        key for key, rows in Counter(normalize_key(row.get("Legal Name")) for row in excel_rows).items()
        if key and rows > 1
    }

    for row in excel_rows:
        excel_row = row["_excel_row"]
        legal_key = normalize_key(row.get("Legal Name"))
        if not legal_key:
            reports["skipped_fields_with_reason"].append(
                {"excel_row": excel_row, "field": "Legal Name", "reason": "missing_required_match_key"}
            )
            continue
        if legal_key in duplicate_excel_legal:
            reports["skipped_fields_with_reason"].append(
                {"excel_row": excel_row, "field": "Legal Name", "reason": "duplicate_excel_company_group"}
            )
            continue

        matches = by_legal.get(legal_key, [])
        planned_fields: dict[str, Any] = {}
        skipped_non_empty: list[str] = []
        invalid_values: list[dict[str, Any]] = []
        schema_required: list[dict[str, str]] = []

        for master_field, target_field in COMPANY_FIELD_MAP.items():
            if target_field not in company_fields:
                continue
            value, reason = company_value(master_field, target_field, row.get(master_field))
            if reason == "blank":
                continue
            if reason:
                target = invalid_select if reason == "invalid_select" else invalid_field
                add_invalid(target, "Companies", target_field, row.get(master_field), excel_row)
                invalid_values.append({"field": target_field, "value": normalize_text(row.get(master_field)), "reason": reason})
                continue
            if matches:
                existing = matches[0].get(target_field)
                if is_blank(existing):
                    planned_fields[target_field] = value
                else:
                    skipped_non_empty.append(target_field)
            else:
                planned_fields[target_field] = value

        for master_field, target_field in COMPANY_SCHEMA_REQUIRED.items():
            if not is_blank(row.get(master_field)) and target_field not in company_fields:
                schema_required.append({"master_field": master_field, "target_field": target_field})

        if matches and len(matches) == 1:
            if planned_fields or skipped_non_empty or invalid_values or schema_required:
                reports["company_fill_blank_updates"].append(
                    {
                        "excel_row": excel_row,
                        "nocodb_company_id": matches[0].get("Id"),
                        "legal_name": normalize_text(row.get("Legal Name")),
                        "fields_to_fill": planned_fields,
                        "skipped_non_empty_fields": skipped_non_empty,
                        "invalid_values": invalid_values,
                        "schema_required_fields": schema_required,
                    }
                )
        elif not matches:
            conflict = []
            bn_key = normalize_key(row.get("Business Number"))
            entity_key = normalize_key(row.get("Entity Number"))
            if bn_key and by_bn.get(bn_key):
                conflict.append({"field": "Business Number", "nocodb_ids": [item.get("Id") for item in by_bn[bn_key]]})
            if entity_key and by_entity.get(entity_key):
                conflict.append({"field": "Entity Number", "nocodb_ids": [item.get("Id") for item in by_entity[entity_key]]})
            reports["company_create_candidates"].append(
                {
                    "excel_row": excel_row,
                    "legal_name": normalize_text(row.get("Legal Name")),
                    "proposed_fields": planned_fields,
                    "schema_required_fields": schema_required,
                    "conflicts": conflict,
                    "safe_to_create": not conflict,
                }
            )
        else:
            reports["skipped_fields_with_reason"].append(
                {
                    "excel_row": excel_row,
                    "field": "Legal Name",
                    "reason": "multiple_nocodb_company_matches",
                    "nocodb_ids": [match.get("Id") for match in matches],
                }
            )

    for (table, field, value), rows in invalid_select.items():
        reports["invalid_select_values"].append(
            {"table": table, "field": field, "raw_value": value, "rows": rows, "count": len(rows)}
        )
    for (table, field, value), rows in invalid_field.items():
        reports["invalid_field_values"].append(
            {"table": table, "field": field, "raw_value": value, "rows": rows, "count": len(rows)}
        )


def owner_records(row: dict[str, Any]) -> list[dict[str, Any]]:
    records = []
    if not is_blank(row.get("Owner Name")):
        records.append(
            {
                "role": "primary_owner",
                "full_name": normalize_text(row.get("Owner Name")),
                "email": normalize_email(row.get("Owner Email")),
                "phone": "",
                "is_primary_owner": True,
            }
        )
    if not is_blank(row.get("2nd Owner Name")):
        records.append(
            {
                "role": "second_owner",
                "full_name": normalize_text(row.get("2nd Owner Name")),
                "email": normalize_email(row.get("2nd Owner Email")),
                "phone": normalize_text(row.get("2nd Owner Phone")),
                "is_primary_owner": False,
            }
        )
    return records


def plan_people_reports(
    ctx: Context,
    excel_rows: list[dict[str, Any]],
    people: list[dict[str, Any]],
    reports: dict[str, list[dict[str, Any]]],
) -> None:
    people_fields = ctx.table_fields["People"]
    by_email = defaultdict(list)
    for person in people:
        if looks_like_email(person.get("Email")):
            by_email[normalize_email(person.get("Email"))].append(person)

    seen_new_email: set[str] = set()
    for row in excel_rows:
        company_name = normalize_text(row.get("Legal Name"))
        for owner in owner_records(row):
            email = owner["email"]
            source = {
                "excel_row": row["_excel_row"],
                "role": owner["role"],
                "full_name": owner["full_name"],
                "email_masked": mask_email(email),
                "company": company_name,
            }
            if not looks_like_email(email):
                reports["weak_people_matches"].append(
                    {**source, "reason": "missing_or_invalid_email", "proposed_action": "manual_confirm"}
                )
                continue

            matches = by_email.get(email, [])
            if len(matches) > 1:
                reports["weak_people_matches"].append(
                    {**source, "reason": "duplicate_people_email_in_nocodb", "proposed_action": "manual_confirm"}
                )
                continue

            if matches:
                person = matches[0]
                fields_to_fill = {}
                skipped_non_empty = []
                planned = {
                    "Full Name": owner["full_name"],
                    "Phone": owner["phone"],
                    "Language": normalized_select("People.Language", row.get("Language"))[0],
                    "Company": company_name,
                    "Is Primary Owner": owner["is_primary_owner"],
                }
                for field, value in planned.items():
                    if field not in people_fields or value in ("", None):
                        continue
                    if is_blank(person.get(field)):
                        fields_to_fill[field] = value
                    else:
                        skipped_non_empty.append(field)
                reports["people_update_candidates"].append(
                    {
                        **source,
                        "nocodb_people_id": person.get("Id"),
                        "matched_by": "email",
                        "fields_to_fill": fields_to_fill,
                        "skipped_non_empty_fields": skipped_non_empty,
                    }
                )
                reports["people_link_candidates"].append(
                    {
                        **source,
                        "nocodb_people_id": person.get("Id"),
                        "confidence": "email_exact_existing",
                        "schema_required": "Linked People/Linked Companies relation fields missing",
                    }
                )
            elif email not in seen_new_email:
                seen_new_email.add(email)
                proposed = {
                    "Full Name": owner["full_name"],
                    "Email": email,
                    "Phone": owner["phone"],
                    "Language": normalized_select("People.Language", row.get("Language"))[0],
                    "Company": company_name,
                    "Is Primary Owner": owner["is_primary_owner"],
                }
                proposed = {key: value for key, value in proposed.items() if key in people_fields and value not in ("", None)}
                reports["people_create_candidates"].append(
                    {**source, "proposed_fields": proposed, "dedupe_key": f"email:{email}"}
                )
                reports["people_link_candidates"].append(
                    {
                        **source,
                        "new_people_dedupe_key": f"email:{email}",
                        "confidence": "email_exact_new",
                        "schema_required": "Linked People/Linked Companies relation fields missing",
                    }
                )
            else:
                reports["people_link_candidates"].append(
                    {
                        **source,
                        "new_people_dedupe_key": f"email:{email}",
                        "confidence": "email_exact_new_duplicate_source",
                        "schema_required": "Linked People/Linked Companies relation fields missing",
                    }
                )


def engagement_existing_keys(engagements: list[dict[str, Any]]) -> set[tuple[str, str, str, str]]:
    keys = set()
    for item in engagements:
        keys.add(
            (
                normalize_text(item.get("Type")),
                normalize_key(item.get("Company")),
                normalize_text(item.get("Year")),
                normalize_text(item.get("Due Date")),
            )
        )
    return keys


def plan_engagement_reports(
    ctx: Context,
    excel_rows: list[dict[str, Any]],
    engagements: list[dict[str, Any]],
    reports: dict[str, list[dict[str, Any]]],
) -> None:
    existing = engagement_existing_keys(engagements)
    seen_candidates = set()
    for row in excel_rows:
        company_name = normalize_text(row.get("Legal Name"))
        row_num = row["_excel_row"]

        # Annual Return: Master stores a month label, not a precise due date.
        annual_value = row.get("Annual Return")
        if not is_blank(annual_value):
            month = annual_return_month(annual_value)
            if month in {ctx.due_window_start.month, ctx.due_window_end.month}:
                _add_engagement_candidate(
                    ctx,
                    reports,
                    existing,
                    seen_candidates,
                    row_num,
                    "Annual Return",
                    company_name,
                    "",
                    ctx.reference_date.year,
                    {"Annual Return": normalize_text(annual_value)},
                    "Annual Return month is current/next month; Due Date intentionally blank",
                )
            else:
                _add_legacy_snapshot(
                    reports,
                    row_num,
                    "Annual Return",
                    "annual_return_month_outside_current_next_window",
                    {"Annual Return": normalize_text(annual_value)},
                )

        # Current/next month due-date workbench items.
        due_rules = [
            (
                "T2",
                "T2 Due Next Period",
                ["T2 YE", "T2 Due Next Period", "T2 Filed Until 18", "T2 Status Days YTD)"],
            ),
            (
                "GST",
                "GST Due Next Period",
                ["GST Start", "GST End", "GST Due Next Period", "GST Filed Until 24", "GST Status Days YTD)"],
            ),
            (
                "PST",
                "PST Next Due",
                ["PST Next Due", "PST Filed Until", "PST Status"],
            ),
        ]
        for engagement_type, due_field, note_fields in due_rules:
            source_values = {
                field: normalize_text(row.get(field))
                for field in note_fields
                if not is_blank(row.get(field))
            }
            if not source_values:
                continue
            due_date = parse_date(row.get(due_field))
            if due_date and in_due_window(due_date, ctx.due_window_start, ctx.due_window_end):
                year = parse_year(due_date)
                _add_engagement_candidate(
                    ctx,
                    reports,
                    existing,
                    seen_candidates,
                    row_num,
                    engagement_type,
                    company_name,
                    due_date,
                    year,
                    source_values,
                    f"{due_field} is inside due window",
                )
            else:
                reason = (
                    f"{due_field}_outside_current_next_window"
                    if due_date
                    else f"{due_field}_missing_or_unparseable"
                )
                _add_legacy_snapshot(reports, row_num, engagement_type, reason, source_values)

        # Historical progress markers: preserve as legacy snapshots, not Engagements.
        if not is_blank(row.get("Bookkeeping Done Until 15")):
            _add_legacy_snapshot(
                reports,
                row_num,
                "Bookkeeping",
                "bookkeeping_done_until_is_historical_progress_marker_not_due_item",
                {"Bookkeeping Done Until 15": normalize_text(row.get("Bookkeeping Done Until 15"))},
            )

        if not is_blank(row.get("T Slips")):
            _add_legacy_snapshot(
                reports,
                row_num,
                "T Slips",
                "t_slips_has_no_specific_due_date_in_master",
                {"T Slips": normalize_text(row.get("T Slips"))},
            )


def _add_legacy_snapshot(
    reports: dict[str, list[dict[str, Any]]],
    excel_row: int,
    engagement_type: str,
    reason: str,
    source_fields: dict[str, str],
) -> None:
    reports["engagement_legacy_snapshot_candidates"].append(
        {
            "excel_row": excel_row,
            "type": engagement_type,
            "reason": reason,
            "source_fields": source_fields,
        }
    )
    reports["skipped_fields_with_reason"].append(
        {
            "excel_row": excel_row,
            "field": engagement_type,
            "reason": reason,
        }
    )


def _add_engagement_candidate(
    ctx: Context,
    reports: dict[str, list[dict[str, Any]]],
    existing: set[tuple[str, str, str, str]],
    seen_candidates: set[tuple[str, str, str, str]],
    excel_row: int,
    engagement_type: str,
    company_name: str,
    due_date: str,
    year: int | None,
    source_fields: dict[str, str],
    note_reason: str,
) -> None:
    if engagement_type not in ctx.engagement_type_options:
        reports["skipped_fields_with_reason"].append(
            {
                "excel_row": excel_row,
                "field": engagement_type,
                "reason": "engagement_type_option_missing",
                "required_type": engagement_type,
            }
        )
        return
    key = (engagement_type, normalize_key(company_name), str(year or ""), due_date)
    if key in existing or key in seen_candidates:
        reports["skipped_fields_with_reason"].append(
            {
                "excel_row": excel_row,
                "field": engagement_type,
                "reason": "engagement_duplicate_key",
                "duplicate_key": "|".join(key),
            }
        )
        return
    seen_candidates.add(key)

    name_suffix = str(due_date or year or "Current Window")
    notes = "; ".join(f"{field}: {value}" for field, value in source_fields.items())
    reports["engagement_create_candidates"].append(
        {
            "excel_row": excel_row,
            "type": engagement_type,
            "name": f"{company_name} {engagement_type} {name_suffix}",
            "company": company_name,
            "year": year,
            "due_date": due_date,
            "status": "",
            "notes": f"Master Excel source row {excel_row}; {note_reason}; {notes}",
            "source_fields": source_fields,
            "duplicate_key": "|".join(key),
        }
    )


def write_reports(ctx: Context, reports: dict[str, list[dict[str, Any]]], summary: dict[str, Any]) -> None:
    for name in REPORT_NAMES:
        (ctx.report_dir / f"{name}.json").write_text(
            json.dumps(reports[name], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        write_csv_report(ctx.report_dir / f"{name}.csv", reports[name])

    (ctx.report_dir / "dry_run_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    markdown = [
        "# Master Sync Dry-Run Summary",
        "",
        f"Generated at: {summary['generated_at']}",
        f"Due window: {summary['due_window']['start']} through {summary['due_window']['end']}",
        f"Old broad engagement candidate count: {summary['old_broad_engagement_candidate_count']}",
        "",
        "## Counts",
        "",
        "| Report | Count |",
        "| --- | ---: |",
    ]
    for name in REPORT_NAMES:
        markdown.append(f"| `{name}` | {summary['report_counts'][name]} |")
    markdown.extend(
        [
            "",
            "## Source Counts",
            "",
            f"- Excel Master rows: {summary['source_counts']['excel_master_rows']}",
            f"- NocoDB Companies rows: {summary['source_counts']['nocodb_companies_rows']}",
            f"- NocoDB People rows: {summary['source_counts']['nocodb_people_rows']}",
            f"- NocoDB Engagements rows: {summary['source_counts']['nocodb_engagements_rows']}",
            "",
            "## Engagement Candidate Counts",
            "",
            f"- Revised actionable candidates: {summary['revised_engagement_candidate_count']}",
            f"- Legacy/snapshot candidates skipped: {summary['legacy_snapshot_candidate_count']}",
            f"- Revised by Type: {summary['revised_engagement_counts_by_type']}",
            "",
            "## Safety",
            "",
            "- NocoDB write calls: 0",
            "- Schema changes: 0",
            "- Excel modifications: 0",
            "- Existing non-empty NocoDB fields are only reported as skipped.",
        ]
    )
    (ctx.report_dir / "dry_run_summary.md").write_text("\n".join(markdown) + "\n", encoding="utf-8")


def write_csv_report(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                    for key, value in row.items()
                }
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Dry-run Master Excel to NocoDB sync.")
    parser.add_argument(
        "--reference-date",
        help="Reference date in YYYY-MM-DD. Defaults to today in America/Regina.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    reference_date = parse_reference_date(args.reference_date)
    ctx = build_context(reference_date)
    reports = {name: [] for name in REPORT_NAMES}

    excel_rows = load_master_rows()
    companies = fetch_rows(ctx.table_ids["Companies"])
    people = fetch_rows(ctx.table_ids["People"])
    engagements = fetch_rows(ctx.table_ids["Engagements"])

    reports["duplicate_company_groups"].extend(build_duplicate_groups(excel_rows, companies))
    plan_company_reports(ctx, excel_rows, companies, reports)
    plan_people_reports(ctx, excel_rows, people, reports)
    plan_engagement_reports(ctx, excel_rows, engagements, reports)
    engagement_counts_by_type = Counter(item["type"] for item in reports["engagement_create_candidates"])

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "dry-run read-only",
        "report_dir": str(ctx.report_dir),
        "timezone": TIMEZONE,
        "reference_date": ctx.reference_date.isoformat(),
        "due_window": {
            "start": ctx.due_window_start.isoformat(),
            "end": ctx.due_window_end.isoformat(),
        },
        "old_broad_engagement_candidate_count": OLD_BROAD_ENGAGEMENT_CANDIDATE_COUNT,
        "revised_engagement_candidate_count": len(reports["engagement_create_candidates"]),
        "revised_engagement_counts_by_type": dict(engagement_counts_by_type),
        "legacy_snapshot_candidate_count": len(reports["engagement_legacy_snapshot_candidates"]),
        "source_counts": {
            "excel_master_rows": len(excel_rows),
            "nocodb_companies_rows": len(companies),
            "nocodb_people_rows": len(people),
            "nocodb_engagements_rows": len(engagements),
        },
        "report_counts": {name: len(reports[name]) for name in REPORT_NAMES},
        "schema_gaps": {
            "companies_missing": [
                field for field in ("WCB Policy #", "T Slips", "Legacy Master Notes", "Linked People")
                if field not in ctx.table_fields["Companies"]
            ],
            "people_missing": [
                field for field in ("Linked Companies", "Source", "Legacy Master Notes")
                if field not in ctx.table_fields["People"]
            ],
            "engagement_type_missing": [
                field for field in ("T Slips",) if field not in ctx.engagement_type_options
            ],
        },
    }

    write_reports(ctx, reports, summary)

    print("DRY_RUN_COMPLETE")
    print(f"report_dir={ctx.report_dir}")
    print(json.dumps({"report_counts": summary["report_counts"], "source_counts": summary["source_counts"]}, indent=2))


if __name__ == "__main__":
    main()
