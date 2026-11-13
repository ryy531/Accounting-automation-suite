"""
Person-group People dry-run from Master Excel primary owners.

Read-only:
- Reads encrypted Master Excel in memory.
- Reads live NocoDB People rows with GET only.
- Generates report files only.
- Does not write NocoDB, schema, Excel, relations, Engagements, or Companies.
"""

from __future__ import annotations

import csv
import json
import os
import re
from collections import Counter, defaultdict
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
COMPANY_FIELD_SAFE_LENGTH = 255
COMPANY_SUMMARY_FIELD_PRIORITY = ("Related Companies", "Company Summary", "Companies")
SAFE_COMPANY_SUMMARY_TYPES = {"LongText", "MultiLineText", "RichText"}


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
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value or ""))


def email_local_part(value: str) -> str:
    return value.split("@", 1)[0].casefold() if "@" in value else ""


def load_schema_snapshot() -> dict[str, Any]:
    return json.loads(SCHEMA_SNAPSHOT.read_text(encoding="utf-8-sig"))


def table_by_name(schema: dict[str, Any], name: str) -> dict[str, Any]:
    for table in schema.get("tables", []):
        if table.get("name") == name:
            return table
    raise KeyError(name)


def get_people_table_id() -> str:
    schema = load_schema_snapshot()
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


def fetch_people_metadata(table_id: str) -> dict[str, Any]:
    base_url = os.getenv("NOCODB_URL", "").rstrip("/")
    token = os.getenv("NOCODB_TOKEN", "")
    if not base_url or not token or not table_id:
        raise RuntimeError("NocoDB People metadata read config missing")
    response = requests.get(
        f"{base_url}/api/v2/meta/tables/{table_id}",
        headers={"xc-token": token, "Content-Type": "application/json"},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


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


def schema_check(people_rows: list[dict[str, Any]], live_metadata: dict[str, Any]) -> dict[str, Any]:
    snapshot = load_schema_snapshot()
    people_fields = {
        field["title"]: field
        for field in table_by_name(snapshot, "People").get("fields", [])
    }
    for field in live_metadata.get("columns", []):
        if field.get("title"):
            people_fields[field["title"]] = field
    live_fields = set(people_rows[0].keys()) if people_rows else set()
    company_like = []
    for name in COMPANY_SUMMARY_FIELD_PRIORITY:
        field = people_fields.get(name)
        if field and (field.get("uidt") in SAFE_COMPANY_SUMMARY_TYPES or field.get("dt") == "text"):
            company_like.append(name)
    company_field = people_fields.get("Company", {})
    notes_field = people_fields.get("Notes", {})
    related_field = people_fields.get("Related Companies", {})
    return {
        "people_fields": {
            field: {
                "exists": field in people_fields or field in live_fields,
                "uidt": people_fields.get(field, {}).get("uidt", "unknown"),
                "dt": people_fields.get(field, {}).get("dt", "unknown"),
                "options": people_fields.get(field, {}).get("options", ""),
            }
            for field in ("Full Name", "Email", "Language", "Is Primary Owner", "Notes", "Company")
        },
        "company_summary_like_fields": company_like,
        "all_live_people_field_titles": sorted(people_fields.keys()),
        "related_companies_exists": "Related Companies" in people_fields,
        "related_companies_field_type": related_field.get("uidt", ""),
        "related_companies_db_type": related_field.get("dt", ""),
        "related_companies_suitable": bool(
            related_field
            and (related_field.get("uidt") in SAFE_COMPANY_SUMMARY_TYPES or related_field.get("dt") == "text")
        ),
        "company_field_type": company_field.get("uidt", "unknown"),
        "company_field_db_type": company_field.get("dt", "unknown"),
        "company_field_safe_length_assumption": COMPANY_FIELD_SAFE_LENGTH,
        "notes_field_type": notes_field.get("uidt", "unknown"),
        "notes_field_db_type": notes_field.get("dt", "unknown"),
        "notes_suitable_for_long_source_evidence": notes_field.get("uidt") == "LongText",
    }


def source_occurrences(master_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    occurrences = []
    company_notes = []
    for row in master_rows:
        owner = norm(row.get("Owner Name"))
        company = norm(row.get("Legal Name"))
        if not owner and company:
            company_notes.append(
                {
                    "excel_row": row["_excel_row"],
                    "company": company,
                    "reason": "missing_primary_owner_name",
                    "suggested_note": f"Master Excel row {row['_excel_row']} has missing primary owner name.",
                }
            )
            continue
        if not owner or not company:
            company_notes.append(
                {
                    "excel_row": row["_excel_row"],
                    "company": company,
                    "reason": "missing_primary_owner_or_company",
                    "suggested_note": "Structurally incomplete Master row; manual review required.",
                }
            )
            continue
        occurrences.append(
            {
                "excel_row": row["_excel_row"],
                "normalized_name": norm_name(owner),
                "owner_name_raw": owner,
                "company": company,
                "email_raw": norm_email(row.get("Owner Email")),
                "phone_raw": norm(row.get("Owner Phone")),
                "language_raw": norm(row.get("Language")),
            }
        )
    return occurrences, company_notes


def choose_full_name(items: list[dict[str, Any]]) -> str:
    ordered = sorted(items, key=lambda item: (-len(item["owner_name_raw"]), item["excel_row"]))
    return ordered[0]["owner_name_raw"]


def choose_email(items: list[dict[str, Any]]) -> str:
    for item in sorted(items, key=lambda item: item["excel_row"]):
        email = item["email_raw"]
        if valid_email(email):
            return email
    return ""


def choose_language(items: list[dict[str, Any]]) -> tuple[str, list[str]]:
    raw_values = sorted({item["language_raw"] for item in items if item["language_raw"]})
    valid_values = {value for value in raw_values if value in VALID_LANGUAGES}
    flags = []
    if any(value and value not in VALID_LANGUAGES for value in raw_values):
        flags.append("invalid_language")
    if len(valid_values) == 1 and len(raw_values) == 1:
        return next(iter(valid_values)), flags
    if len(valid_values) > 1:
        flags.append("conflicting_valid_languages")
    elif raw_values and valid_values and len(raw_values) > 1:
        flags.append("invalid_language")
    return "", flags


def existing_match_status(normalized_name: str, people_rows: list[dict[str, Any]]) -> str:
    matches = [
        row.get("Id")
        for row in people_rows
        if norm_name(row.get("Full Name")) == normalized_name
    ]
    if not matches:
        return "no_existing_people_name_match"
    return f"possible_duplicate_existing_people_ids={','.join(str(item) for item in matches)}"


def build_notes(
    normalized_name: str,
    full_name: str,
    email: str,
    items: list[dict[str, Any]],
    flags: list[str],
) -> str:
    name_variants = sorted({item["owner_name_raw"] for item in items})
    rows = [str(item["excel_row"]) for item in items]
    companies = [item["company"] for item in sorted(items, key=lambda item: (item["company"], item["excel_row"]))]
    raw_emails = [f"row {item['excel_row']}: {item['email_raw'] or '<blank>'}" for item in items]
    raw_languages = [f"row {item['excel_row']}: {item['language_raw'] or '<blank>'}" for item in items]
    return "; ".join(
        [
            "Source: Master Excel",
            "Dry-run date: 2026-06-05",
            f"Normalized owner name: {normalized_name}",
            f"Selected Full Name: {full_name}",
            f"All raw owner name variants: {', '.join(name_variants)}",
            f"Source Master rows: {', '.join(rows)}",
            f"Source companies: {' | '.join(companies)}",
            f"Raw email values: {' | '.join(raw_emails)}",
            f"Selected first email: {email or '<blank>'}",
            f"Raw language values: {' | '.join(raw_languages)}",
            f"Review flags: {', '.join(flags) if flags else '<none>'}",
        ]
    )


def group_candidates(
    occurrences: list[dict[str, Any]],
    people_rows: list[dict[str, Any]],
    schema: dict[str, Any],
) -> list[dict[str, Any]]:
    by_name = defaultdict(list)
    for item in occurrences:
        by_name[item["normalized_name"]].append(item)

    candidates = []
    for normalized_name, items in sorted(by_name.items()):
        full_name = choose_full_name(items)
        email = choose_email(items)
        language, language_flags = choose_language(items)
        companies = sorted({item["company"] for item in items})
        company_summary = "; ".join(companies)
        raw_emails = sorted({item["email_raw"] for item in items if item["email_raw"]})
        flags = set(language_flags)
        if len(companies) > 1:
            flags.add("same_name_multiple_companies")
        if len(raw_emails) > 1:
            flags.add("same_name_multiple_distinct_emails")
        if len(companies) > 3:
            flags.add("company_count_gt_3")
        if not email:
            flags.add("missing_email")
        if any(email_local_part(value) in SHARED_EMAIL_LOCAL_PARTS for value in raw_emails):
            flags.add("shared_or_company_email")
        if existing_match_status(normalized_name, people_rows) != "no_existing_people_name_match":
            flags.add("existing_people_possible_duplicate")

        target_field = "Company"
        schema_support_status = "company_summary_fits_company_field"
        schema_warning = ""
        if schema["company_summary_like_fields"]:
            target_field = schema["company_summary_like_fields"][0]
            schema_support_status = (
                "related_companies_safe_to_write"
                if target_field == "Related Companies"
                else "company_summary_like_field_safe_to_write"
            )
        elif len(company_summary) > COMPANY_FIELD_SAFE_LENGTH:
            schema_support_status = "schema_gap_company_summary_too_long"
            schema_warning = "Company summary exceeds conservative SingleLineText length assumption; add Related Companies LongText."
            flags.add("schema_gap_company_summary_too_long")
            flags.add("schema_gap_missing_related_companies_field")
        else:
            flags.add("schema_gap_missing_related_companies_field")

        sorted_flags = sorted(flags)
        notes = build_notes(normalized_name, full_name, email, items, sorted_flags)
        candidates.append(
            {
                "normalized_name": normalized_name,
                "full_name": full_name,
                "email": email,
                "language": language,
                "is_primary_owner": True,
                "company_count": len(companies),
                "company_summary": company_summary,
                "company_summary_length": len(company_summary),
                "company_summary_target_field": target_field,
                "schema_support_status": schema_support_status,
                "schema_warning": schema_warning,
                "notes": notes,
                "source_occurrence_count": len(items),
                "source_rows": [item["excel_row"] for item in sorted(items, key=lambda item: item["excel_row"])],
                "all_owner_name_variants": sorted({item["owner_name_raw"] for item in items}),
                "all_raw_emails": [item["email_raw"] for item in items],
                "review_flags": sorted_flags,
                "uncertain_flag": bool(sorted_flags),
                "uncertain_reason": ", ".join(sorted_flags),
                "existing_people_match_status": existing_match_status(normalized_name, people_rows),
            }
        )
    return candidates


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = []
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
                    key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value
                    for key, value in row.items()
                }
            )


def main() -> None:
    load_dotenv(ENV_PATH)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = REPORT_ROOT / f"people_person_group_dryrun_{timestamp}"
    report_dir.mkdir(parents=True, exist_ok=False)

    master_rows, _headers = load_master_rows()
    people_table_id = get_people_table_id()
    people_rows = fetch_people_rows(people_table_id)
    live_metadata = fetch_people_metadata(people_table_id)
    schema = schema_check(people_rows, live_metadata)
    occurrences, company_notes = source_occurrences(master_rows)
    candidates = group_candidates(occurrences, people_rows, schema)
    uncertain = [candidate for candidate in candidates if candidate["uncertain_flag"]]

    source_rows = [
        {
            **item,
            "occurrence_key": f"{item['normalized_name']} + {item['company']}",
        }
        for item in occurrences
    ]
    too_long = [candidate for candidate in candidates if candidate["schema_support_status"] == "schema_gap_company_summary_too_long"]
    possible_duplicates = [
        candidate for candidate in candidates
        if candidate["existing_people_match_status"] != "no_existing_people_name_match"
    ]

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "people person-group dry-run read-only",
        "report_dir": str(report_dir),
        "source_master_rows_scanned": len(master_rows),
        "primary_owner_occurrences_extracted": len(occurrences),
        "person_groups_generated": len(candidates),
        "skipped_missing_owner_rows": len(company_notes),
        "person_groups_with_multiple_companies": sum(1 for item in candidates if item["company_count"] > 1),
        "person_groups_with_multiple_emails": sum(1 for item in candidates if "same_name_multiple_distinct_emails" in item["review_flags"]),
        "person_groups_with_conflicting_languages": sum(1 for item in candidates if "conflicting_valid_languages" in item["review_flags"]),
        "person_groups_with_invalid_languages": sum(1 for item in candidates if "invalid_language" in item["review_flags"]),
        "person_groups_with_missing_email": sum(1 for item in candidates if "missing_email" in item["review_flags"]),
        "person_groups_with_possible_existing_people_duplicates": len(possible_duplicates),
        "company_summaries_safe_to_write_completely": len(candidates) - len(too_long),
        "company_summaries_too_long_for_current_target": len(too_long),
        "related_companies_field_exists": bool(schema["company_summary_like_fields"]),
        "related_companies_detected": schema["related_companies_exists"],
        "related_companies_field_type": schema["related_companies_field_type"],
        "related_companies_db_type": schema["related_companies_db_type"],
        "related_companies_suitable": schema["related_companies_suitable"],
        "company_summary_like_fields": schema["company_summary_like_fields"],
        "existing_people_rows_scanned": len(people_rows),
        "possible_duplicate_count": len(possible_duplicates),
        "schema_gaps_found": sorted({flag for item in candidates for flag in item["review_flags"] if flag.startswith("schema_gap")}),
        "apply_allowed": False,
    }

    outputs = {
        "person_group_import_candidates": candidates,
        "person_group_uncertain_review": uncertain,
        "person_group_source_occurrences": source_rows,
        "company_note_update_candidates": company_notes,
    }
    for name, rows in outputs.items():
        write_json(report_dir / f"{name}.json", rows)
        write_csv(report_dir / f"{name}.csv", rows)
    write_json(report_dir / "summary.json", summary)

    schema_md = [
        "# People Schema Check Report",
        "",
        f"Existing People rows scanned: {len(people_rows)}",
        "",
        "## Field Snapshot",
        "",
        "| Field | Exists | Type | DB Type | Options |",
        "| --- | --- | --- | --- | --- |",
    ]
    for field, info in schema["people_fields"].items():
        schema_md.append(f"| {field} | {info['exists']} | {info['uidt']} | {info['dt']} | {info['options']} |")
    schema_md.extend(
        [
            "",
            f"Company field type: {schema['company_field_type']} / {schema['company_field_db_type']}",
            f"Company field safe length assumption: {schema['company_field_safe_length_assumption']}",
            f"Related/company-summary-like fields: {', '.join(schema['company_summary_like_fields']) if schema['company_summary_like_fields'] else 'none'}",
            f"Related Companies detected: {schema['related_companies_exists']}",
            f"Related Companies type: {schema['related_companies_field_type']} / {schema['related_companies_db_type']}",
            f"Related Companies suitable: {schema['related_companies_suitable']}",
            f"Live People field titles: {', '.join(schema['all_live_people_field_titles'])}",
            f"Candidates too long for current target: {len(too_long)}",
            f"Notes field type: {schema['notes_field_type']} / {schema['notes_field_db_type']}",
            f"Notes suitable for long source evidence: {schema['notes_suitable_for_long_source_evidence']}",
        ]
    )
    (report_dir / "schema_check_report.md").write_text("\n".join(schema_md) + "\n", encoding="utf-8")
    summary_md = [
        "# People Person-Group Dry-Run Summary",
        "",
        f"Generated at: {summary['generated_at']}",
        "",
        f"- Source Master rows scanned: {summary['source_master_rows_scanned']}",
        f"- Primary owner occurrences extracted: {summary['primary_owner_occurrences_extracted']}",
        f"- Person groups generated: {summary['person_groups_generated']}",
        f"- Skipped missing-owner rows: {summary['skipped_missing_owner_rows']}",
        f"- Person groups with multiple companies: {summary['person_groups_with_multiple_companies']}",
        f"- Person groups with multiple emails: {summary['person_groups_with_multiple_emails']}",
        f"- Person groups with invalid languages: {summary['person_groups_with_invalid_languages']}",
        f"- Person groups with missing email: {summary['person_groups_with_missing_email']}",
        f"- Company summaries safe to write completely: {summary['company_summaries_safe_to_write_completely']}",
        f"- Company summaries too long: {summary['company_summaries_too_long_for_current_target']}",
        f"- Related Companies field exists: {summary['related_companies_field_exists']}",
        f"- Related Companies type: {summary['related_companies_field_type']} / {summary['related_companies_db_type']}",
        f"- Related Companies suitable: {summary['related_companies_suitable']}",
        f"- Existing People rows scanned: {summary['existing_people_rows_scanned']}",
        f"- Possible duplicate count: {summary['possible_duplicate_count']}",
        f"- Apply allowed: {summary['apply_allowed']}",
    ]
    (report_dir / "summary.md").write_text("\n".join(summary_md) + "\n", encoding="utf-8")

    print("PERSON_GROUP_DRY_RUN_COMPLETE")
    print(f"report_dir={report_dir}")
    print(
        json.dumps(
            {
                "person_groups_generated": summary["person_groups_generated"],
                "company_summaries_too_long": summary["company_summaries_too_long_for_current_target"],
                "related_companies_field_exists": summary["related_companies_field_exists"],
                "possible_duplicate_count": summary["possible_duplicate_count"],
                "apply_allowed": summary["apply_allowed"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
