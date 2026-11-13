"""
Primary-owner-only People inventory dry-run.

Read-only:
- Reads encrypted Master Excel in memory.
- Reads NocoDB People schema/rows with GET only for schema visibility.
- Reads business client parent folder names only to suggest verification targets.
- Does not open Company Info files.
- Does not write NocoDB, schema, Excel, or filesystem data except reports.
"""

from __future__ import annotations

import csv
import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import msoffcrypto
import requests
from dotenv import load_dotenv
from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / "_lib" / "config" / ".env"
SCHEMA_SNAPSHOT = ROOT / "payroll" / "_doc" / "nocodb_current_state_snapshot_2026-06-03.json"
REPORT_ROOT = ROOT / "payroll" / "_reports"
DEFAULT_MASTER_PATH = Path(
    r"\\file-server\Shared Folder\4. General - Scan-Notes-Form-PPT"
    r"\4. Accounting client list\#Business Master Client List.xlsx"
)
BUSINESS_CLIENT_ROOT = Path(r"\\file-server\Shared Folder\1. Accounting - Business Clients")
SOURCE_LABEL = "Master Excel 2026-06-05"

PEOPLE_FIELDS = ["Full Name", "Email", "Phone", "Language", "Company", "Is Primary Owner", "Notes"]
VALID_LANGUAGES = {"Chinese", "English", "Vietnamese", "1"}
SHARED_EMAIL_LOCAL_PARTS = {
    "info",
    "admin",
    "office",
    "bookkeeping",
    "accounting",
    "payroll",
    "contact",
    "support",
    "sales",
    "reception",
    "hello",
    "service",
    "mail",
    "team",
}
SUSPICIOUS_OWNER_NAMES = {
    "info",
    "admin",
    "office",
    "bookkeeping",
    "accounting",
    "payroll",
    "contact",
    "support",
    "sales",
    "reception",
    "director",
    "shareholder",
    "unknown",
    "n/a",
    "na",
}


@dataclass
class Occurrence:
    excel_row: int
    owner_name_raw: str
    owner_name_normalized: str
    company: str
    email: str
    phone: str
    language_raw: str
    language_proposed: str
    language_warning: str
    role: str
    occurrence_key: str
    review_flags: list[str]
    notes: str
    proposed_fields: dict[str, Any]


def norm(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    return " ".join(str(value).replace("\n", " ").split()).strip()


def norm_name(value: Any) -> str:
    return re.sub(r"\s+", " ", norm(value)).casefold()


def norm_email(value: Any) -> str:
    return norm(value).lower()


def is_blank(value: Any) -> bool:
    return norm(value).casefold() in {"", "na", "n/a", "none", "-", "--", "null"}


def valid_email(value: str) -> bool:
    if not value:
        return False
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value))


def email_local_part(value: str) -> str:
    return value.split("@", 1)[0].casefold() if "@" in value else ""


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_SNAPSHOT.read_text(encoding="utf-8-sig"))


def table_by_name(schema: dict[str, Any], name: str) -> dict[str, Any]:
    for table in schema.get("tables", []):
        if table.get("name") == name:
            return table
    raise KeyError(name)


def get_people_table_id() -> str:
    schema = load_schema()
    fallback = table_by_name(schema, "People").get("table_id", "")
    return os.getenv("NOCODB_PEOPLE_TABLE_ID") or fallback


def fetch_people_rows(table_id: str) -> list[dict[str, Any]]:
    base_url = os.getenv("NOCODB_URL", "").rstrip("/")
    token = os.getenv("NOCODB_TOKEN", "")
    if not base_url or not token or not table_id:
        raise RuntimeError("NocoDB People read config missing")
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


def load_master_rows() -> tuple[list[dict[str, Any]], list[str]]:
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
        norm(value)
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
    return rows, [header for header in headers if header]


def people_schema_gaps(people_rows: list[dict[str, Any]]) -> list[str]:
    schema = load_schema()
    snapshot_fields = {
        field["title"]
        for field in table_by_name(schema, "People").get("fields", [])
    }
    live_fields = set(people_rows[0].keys()) if people_rows else set()
    observed = snapshot_fields | live_fields
    return [field for field in PEOPLE_FIELDS if field not in observed]


def expected_parent_folder(company: str) -> Path:
    first = company.strip()[:1]
    if first.isdigit():
        return BUSINESS_CLIENT_ROOT / "#Number Companies"
    if first:
        return BUSINESS_CLIENT_ROOT / first.upper()
    return BUSINESS_CLIENT_ROOT


def normalize_folder_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def company_info_target(company: str) -> dict[str, str]:
    parent = expected_parent_folder(company)
    search_key = company.strip()
    status = "needs_manual_lookup"
    suggested = ""
    try:
        if parent.exists():
            target_key = normalize_folder_key(search_key)
            matches = []
            for child in parent.iterdir():
                if child.is_dir() and normalize_folder_key(child.name).startswith(target_key):
                    matches.append(child)
            if len(matches) == 1:
                suggested = str(matches[0] / "Company Info" / "Company Info Sheet.xlsx")
                status = "suggested_from_unique_folder_match"
            elif len(matches) > 1:
                status = "multiple_folder_matches_needs_manual_lookup"
    except OSError:
        status = "parent_folder_unreadable_needs_manual_lookup"

    return {
        "expected_parent_folder": str(parent),
        "company_search_key": search_key,
        "suggested_company_info_path": suggested,
        "company_info_path_status": status,
    }


def language_fields(raw_language: str) -> tuple[str, str]:
    if not raw_language:
        return "", ""
    if raw_language in VALID_LANGUAGES:
        return raw_language, ""
    return "", f"invalid Master value '{raw_language}' was not written"


def base_notes(row_num: int, company: str, flags: list[str], extra_notes: list[str]) -> str:
    notes = [
        f"Source: {SOURCE_LABEL}",
        f"Source row: {row_num}",
        "Role: primary_owner",
        f"Company: {company}",
    ]
    notes.extend(extra_notes)
    if flags:
        notes.append(f"Review flags: {', '.join(flags)}")
    return "; ".join(notes)


def build_occurrences(rows: list[dict[str, Any]], schema_gaps: list[str]) -> tuple[list[Occurrence], list[dict[str, Any]]]:
    raw_items = []
    blocked = []
    for row in rows:
        row_num = row["_excel_row"]
        owner_name = norm(row.get("Owner Name"))
        company = norm(row.get("Legal Name"))
        if not owner_name or not company:
            reasons = []
            if not owner_name:
                reasons.append("missing_owner_name")
            if not company:
                reasons.append("missing_source_company")
            if schema_gaps:
                reasons.append("schema_gap")
            blocked.append(
                {
                    "excel_row": row_num,
                    "owner_name_raw": owner_name,
                    "company": company,
                    "blocked_reasons": reasons,
                }
            )
            continue
        raw_items.append(
            {
                "excel_row": row_num,
                "owner_name_raw": owner_name,
                "owner_name_normalized": norm_name(owner_name),
                "company": company,
                "email": norm_email(row.get("Owner Email")),
                "phone": norm(row.get("Owner Phone")),
                "language_raw": norm(row.get("Language")),
                "role": "primary_owner",
            }
        )

    name_to_items = defaultdict(list)
    email_to_names = defaultdict(set)
    for item in raw_items:
        name_to_items[item["owner_name_normalized"]].append(item)
        if item["email"]:
            email_to_names[item["email"]].add(item["owner_name_normalized"])

    occurrences: list[Occurrence] = []
    for item in raw_items:
        flags = []
        extra_notes = []
        same_name_items = name_to_items[item["owner_name_normalized"]]
        same_name_companies = {entry["company"] for entry in same_name_items}
        same_name_emails = {entry["email"] for entry in same_name_items if entry["email"]}
        if not item["email"]:
            flags.append("missing_email")
            extra_notes.append("Email: missing in Master")
        if len(same_name_companies) > 1:
            flags.append("same_name_across_multiple_companies")
        if len(same_name_emails) > 1:
            flags.append("same_name_multiple_emails")
            extra_notes.append("Contact note: same owner name appears with other emails in Master")
        if item["email"] and len(email_to_names[item["email"]]) > 1:
            flags.append("same_email_multiple_names")
            other_names = sorted(
                {
                    entry["owner_name_raw"]
                    for entry in raw_items
                    if entry["email"] == item["email"] and entry["owner_name_normalized"] != item["owner_name_normalized"]
                }
            )
            if other_names:
                extra_notes.append(f"Contact note: same email also appears for {', '.join(other_names)}")
        if item["email"] and email_local_part(item["email"]) in SHARED_EMAIL_LOCAL_PARTS:
            flags.append("shared_or_company_email")
            extra_notes.append("Contact note: email local part suggests shared/company inbox")
        if item["owner_name_normalized"] in SUSPICIOUS_OWNER_NAMES:
            flags.append("suspicious_owner_name")
        language_proposed, language_warning = language_fields(item["language_raw"])
        if language_warning:
            flags.append("invalid_language")
            extra_notes.append(f"Language note: {language_warning}")

        target = company_info_target(item["company"])
        if flags:
            flags.append("company_info_verification_suggested")
            extra_notes.append("Verification note: review Company Info Sheet path in report")
            if target["company_info_path_status"] != "suggested_from_unique_folder_match":
                flags.append("company_info_path_needs_manual_lookup")

        flags = sorted(set(flags))
        notes = base_notes(item["excel_row"], item["company"], flags, extra_notes)
        proposed_fields: dict[str, Any] = {
            "Full Name": item["owner_name_raw"],
            "Company": item["company"],
            "Is Primary Owner": True,
            "Notes": notes,
        }
        if item["email"]:
            proposed_fields["Email"] = item["email"]
        if item["phone"]:
            proposed_fields["Phone"] = item["phone"]
        if language_proposed:
            proposed_fields["Language"] = language_proposed

        occurrences.append(
            Occurrence(
                excel_row=item["excel_row"],
                owner_name_raw=item["owner_name_raw"],
                owner_name_normalized=item["owner_name_normalized"],
                company=item["company"],
                email=item["email"],
                phone=item["phone"],
                language_raw=item["language_raw"],
                language_proposed=language_proposed,
                language_warning=language_warning,
                role=item["role"],
                occurrence_key=f"{item['owner_name_normalized']} + {item['company']}",
                review_flags=flags,
                notes=notes,
                proposed_fields=proposed_fields,
            )
        )
    return occurrences, blocked


def occurrence_row(item: Occurrence) -> dict[str, Any]:
    return {
        "excel_row": item.excel_row,
        "owner_name_raw": item.owner_name_raw,
        "owner_name_normalized": item.owner_name_normalized,
        "company": item.company,
        "email": item.email,
        "phone": item.phone,
        "language_raw": item.language_raw,
        "language_proposed": item.language_proposed,
        "language_warning": item.language_warning,
        "role": item.role,
        "occurrence_key": item.occurrence_key,
        "review_flags": item.review_flags,
        "notes": item.notes,
    }


def build_name_groups(occurrences: list[Occurrence]) -> list[dict[str, Any]]:
    by_name = defaultdict(list)
    for occurrence in occurrences:
        by_name[occurrence.owner_name_normalized].append(occurrence)
    groups = []
    for normalized, items in sorted(by_name.items()):
        flags = set()
        companies = sorted({item.company for item in items})
        emails = sorted({item.email for item in items if item.email})
        if len(companies) > 1:
            flags.add("same_name_across_multiple_companies")
        if len(emails) > 1:
            flags.add("same_name_multiple_emails")
        groups.append(
            {
                "owner_name_normalized": normalized,
                "owner_names_seen": sorted({item.owner_name_raw for item in items}),
                "company_count": len(companies),
                "companies": companies,
                "emails_seen": emails,
                "excel_rows": [item.excel_row for item in items],
                "review_flags": sorted(flags),
            }
        )
    return groups


def verification_targets(occurrences: list[Occurrence]) -> list[dict[str, Any]]:
    rows = []
    for occurrence in occurrences:
        if not occurrence.review_flags:
            continue
        target = company_info_target(occurrence.company)
        rows.append(
            {
                "excel_row": occurrence.excel_row,
                "owner_name": occurrence.owner_name_raw,
                "company": occurrence.company,
                **target,
                "reason_for_verification": ", ".join(occurrence.review_flags),
            }
        )
    return rows


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value
                    for key, value in row.items()
                }
            )


def main() -> None:
    load_dotenv(ENV_PATH)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = REPORT_ROOT / f"primary_owner_inventory_{timestamp}"
    report_dir.mkdir(parents=True, exist_ok=False)

    master_rows, headers = load_master_rows()
    people_rows = fetch_people_rows(get_people_table_id())
    gaps = people_schema_gaps(people_rows)
    occurrences, blocked = build_occurrences(master_rows, gaps)
    occurrence_rows = [occurrence_row(item) for item in occurrences]
    name_groups = build_name_groups(occurrences)
    uncertain = [row for row in occurrence_rows if row["review_flags"]]
    targets = verification_targets(occurrences)
    import_candidates = [
        {**occurrence_row(item), "proposed_fields": item.proposed_fields}
        for item in occurrences
    ]

    flag_counter = Counter(flag for item in occurrences for flag in item.review_flags)
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "primary-owner inventory dry-run read-only",
        "report_dir": str(report_dir),
        "source_master_rows_scanned": len(master_rows),
        "primary_owner_occurrences_extracted": len(occurrences),
        "blocked_candidate_count": len(blocked),
        "people_import_candidate_count": len(import_candidates),
        "name_group_count": len(name_groups),
        "uncertain_review_count": len(uncertain),
        "missing_email_count": flag_counter["missing_email"],
        "duplicate_shared_email_flag_count": flag_counter["same_email_multiple_names"] + flag_counter["shared_or_company_email"],
        "same_name_multi_company_group_count": sum(
            1 for group in name_groups if "same_name_across_multiple_companies" in group["review_flags"]
        ),
        "invalid_language_count": flag_counter["invalid_language"],
        "company_info_verification_target_count": len(targets),
        "existing_people_rows_scanned": len(people_rows),
        "schema_gaps": gaps,
        "missing_source_columns": [column for column in ("Owner Phone",) if column not in headers],
        "blocker_prevents_future_apply": bool(gaps),
        "review_flag_counts": dict(flag_counter),
    }

    outputs = {
        "primary_owner_occurrences": occurrence_rows,
        "primary_owner_name_groups": name_groups,
        "primary_owner_uncertain_review": uncertain,
        "company_info_verification_targets": targets,
        "people_import_candidates": import_candidates,
        "blocked_candidates": blocked,
    }
    for name, rows in outputs.items():
        write_json(report_dir / f"{name}.json", rows)
        write_csv(report_dir / f"{name}.csv", rows)
    write_json(report_dir / "people_inventory_summary.json", summary)
    md = [
        "# Primary Owner Inventory Dry-Run Summary",
        "",
        f"Generated at: {summary['generated_at']}",
        "",
        f"- Source Master rows scanned: {summary['source_master_rows_scanned']}",
        f"- Primary owner occurrences extracted: {summary['primary_owner_occurrences_extracted']}",
        f"- Blocked candidates: {summary['blocked_candidate_count']}",
        f"- People import candidates: {summary['people_import_candidate_count']}",
        f"- Name groups: {summary['name_group_count']}",
        f"- Uncertain review rows: {summary['uncertain_review_count']}",
        f"- Missing email count: {summary['missing_email_count']}",
        f"- Duplicate/shared email flag count: {summary['duplicate_shared_email_flag_count']}",
        f"- Same-name multi-company groups: {summary['same_name_multi_company_group_count']}",
        f"- Invalid language count: {summary['invalid_language_count']}",
        f"- Company Info verification targets: {summary['company_info_verification_target_count']}",
        f"- Schema gaps: {', '.join(gaps) if gaps else 'none'}",
        f"- Future apply blocker: {summary['blocker_prevents_future_apply']}",
    ]
    (report_dir / "people_inventory_summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print("PRIMARY_OWNER_INVENTORY_DRY_RUN_COMPLETE")
    print(f"report_dir={report_dir}")
    print(
        json.dumps(
            {
                "primary_owner_occurrences_extracted": summary["primary_owner_occurrences_extracted"],
                "people_import_candidate_count": summary["people_import_candidate_count"],
                "blocked_candidate_count": summary["blocked_candidate_count"],
                "uncertain_review_count": summary["uncertain_review_count"],
                "schema_gap_count": len(gaps),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
