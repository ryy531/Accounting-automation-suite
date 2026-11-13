"""
People-only Master Excel -> NocoDB dry-run.

This script is read-only:
- Reads the encrypted Master Excel workbook in memory.
- Reads NocoDB People rows with GET requests only.
- Does not modify schema, Excel, or NocoDB records.
- Does not print secrets or full email lists to stdout.
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
SOURCE_LABEL = "Master Excel 2026-06-05"

ALLOWED_PEOPLE_FIELDS = [
    "Full Name",
    "Email",
    "Phone",
    "Language",
    "Company",
    "Is Primary Owner",
    "Notes",
]
PEOPLE_LANGUAGE_VALUES = {"Chinese", "English", "Vietnamese", "1"}
SHARED_INBOX_LOCAL_PARTS = {
    "info",
    "admin",
    "office",
    "bookkeeping",
    "accounting",
    "payroll",
    "contact",
    "support",
    "sales",
    "hello",
    "service",
    "reception",
    "mail",
    "team",
}


@dataclass
class Candidate:
    source_row: int
    role: str
    full_name: str
    email: str
    phone: str
    company: str
    language: str
    is_primary_owner: bool


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


def valid_email(value: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value or ""))


def email_local_part(value: str) -> str:
    return value.split("@", 1)[0].casefold() if "@" in value else ""


def mask_email(value: str) -> str:
    value = norm_email(value)
    if "@" not in value:
        return ""
    user, domain = value.split("@", 1)
    return f"{user[:2]}***@{domain[:1]}***"


def is_blank(value: Any) -> bool:
    return norm(value).casefold() in {"", "na", "n/a", "none", "-", "--", "null"}


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


def candidate_notes(candidates: list[Candidate]) -> str:
    related = ", ".join(
        f"{candidate.company}(row {candidate.source_row}, {candidate.role})"
        for candidate in sorted(candidates, key=lambda item: (item.company, item.source_row, item.role))
    )
    return f"Source: {SOURCE_LABEL}; Related companies: {related}"


def language_value(candidate: Candidate) -> tuple[str, bool]:
    if is_blank(candidate.language):
        return "", False
    if candidate.language in PEOPLE_LANGUAGE_VALUES:
        return candidate.language, False
    return "", True


def proposed_fields_for(candidates: list[Candidate], email: str) -> dict[str, Any]:
    first = candidates[0]
    language, _ = language_value(first)
    company = first.company if len({item.company for item in candidates}) == 1 else "Multiple companies (see Notes)"
    fields: dict[str, Any] = {
        "Full Name": first.full_name,
        "Email": email,
        "Company": company,
        "Is Primary Owner": first.is_primary_owner,
        "Notes": candidate_notes(candidates),
    }
    if first.phone:
        fields["Phone"] = first.phone
    if language:
        fields["Language"] = language
    return fields


def candidate_public(candidate: Candidate, mask: bool = False) -> dict[str, Any]:
    return {
        "source_row": candidate.source_row,
        "role": candidate.role,
        "full_name": candidate.full_name,
        "email": mask_email(candidate.email) if mask else candidate.email,
        "phone": candidate.phone,
        "company": candidate.company,
        "language": candidate.language,
        "is_primary_owner": candidate.is_primary_owner,
    }


def extract_candidates(rows: list[dict[str, Any]], headers: list[str]) -> tuple[list[Candidate], list[str]]:
    missing_columns = []
    for column in ("Owner Name", "Owner Email", "Legal Name", "Language", "2nd Owner Name", "2nd Owner Email", "2nd Owner Phone"):
        if column not in headers:
            missing_columns.append(column)
    owner_phone_available = "Owner Phone" in headers
    if not owner_phone_available:
        missing_columns.append("Owner Phone")

    candidates: list[Candidate] = []
    for row in rows:
        company = norm(row.get("Legal Name"))
        if not is_blank(row.get("Owner Name")):
            candidates.append(
                Candidate(
                    source_row=row["_excel_row"],
                    role="primary_owner",
                    full_name=norm(row.get("Owner Name")),
                    email=norm_email(row.get("Owner Email")),
                    phone=norm(row.get("Owner Phone")) if owner_phone_available else "",
                    company=company,
                    language=norm(row.get("Language")),
                    is_primary_owner=True,
                )
            )
        if not is_blank(row.get("2nd Owner Name")):
            candidates.append(
                Candidate(
                    source_row=row["_excel_row"],
                    role="second_owner",
                    full_name=norm(row.get("2nd Owner Name")),
                    email=norm_email(row.get("2nd Owner Email")),
                    phone=norm(row.get("2nd Owner Phone")),
                    company=company,
                    language=norm(row.get("Language")),
                    is_primary_owner=False,
                )
            )
    return candidates, missing_columns


def schema_gaps(people_rows: list[dict[str, Any]]) -> list[str]:
    schema = load_schema()
    snapshot_fields = {
        field["title"]
        for field in table_by_name(schema, "People").get("fields", [])
    }
    live_fields = set(people_rows[0].keys()) if people_rows else set()
    observed = snapshot_fields | live_fields
    return [field for field in ALLOWED_PEOPLE_FIELDS if field not in observed]


def existing_people_by_email(people_rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in people_rows:
        email = norm_email(row.get("Email"))
        if email:
            bucket[email].append(row)
    return bucket


def blank_update_fields(existing: dict[str, Any], candidates: list[Candidate], email: str) -> tuple[dict[str, Any], list[str]]:
    proposed = proposed_fields_for(candidates, email)
    fields: dict[str, Any] = {}
    skipped: list[str] = []
    for field, value in proposed.items():
        if value in ("", None):
            continue
        if is_blank(existing.get(field)):
            fields[field] = value
        else:
            skipped.append(field)
    return fields, skipped


def analyze(
    candidates: list[Candidate],
    people_rows: list[dict[str, Any]],
    gaps: list[str],
) -> dict[str, list[dict[str, Any]]]:
    reports = {
        "people_safe_create_candidates": [],
        "people_manual_review_candidates": [],
        "people_duplicate_or_conflict": [],
        "people_update_blank_candidates": [],
    }
    by_email: dict[str, list[Candidate]] = defaultdict(list)
    by_name: dict[str, list[Candidate]] = defaultdict(list)
    for candidate in candidates:
        by_name[norm_name(candidate.full_name)].append(candidate)
        if candidate.email:
            by_email[candidate.email].append(candidate)
        else:
            reports["people_manual_review_candidates"].append(
                {**candidate_public(candidate), "reasons": ["missing_email"]}
            )

    existing_by_email = existing_people_by_email(people_rows)
    name_email_sets = {
        name: {item.email for item in items if item.email}
        for name, items in by_name.items()
    }

    processed_emails: set[str] = set()
    for email, email_candidates in by_email.items():
        if email in processed_emails:
            continue
        processed_emails.add(email)
        reasons: set[str] = set()
        first = email_candidates[0]

        if not first.full_name:
            reasons.add("missing_full_name")
        if not valid_email(email):
            reasons.add("invalid_email_format")
        if email_local_part(email) in SHARED_INBOX_LOCAL_PARTS:
            reasons.add("shared_or_company_email")
        if not first.company:
            reasons.add("missing_source_company")
        if gaps:
            reasons.add("schema_gap")

        names = {norm_name(item.full_name) for item in email_candidates}
        roles = {item.role for item in email_candidates}
        companies = {item.company for item in email_candidates if item.company}
        if len(names) > 1:
            reasons.add("same_email_different_names")
        if len(roles) > 1:
            reasons.add("same_email_inconsistent_role")
        if first.language and first.language not in PEOPLE_LANGUAGE_VALUES:
            reasons.add("invalid_language")
        if len(name_email_sets.get(norm_name(first.full_name), set())) > 1:
            reasons.add("same_name_different_emails")

        existing_matches = existing_by_email.get(email, [])
        if len(existing_matches) > 1:
            reasons.add("duplicate_email_in_nocodb_people")
        elif len(existing_matches) == 1:
            existing = existing_matches[0]
            existing_name = norm_name(existing.get("Full Name"))
            if existing_name and existing_name != norm_name(first.full_name):
                reasons.add("same_email_different_names")
            if reasons:
                reports["people_duplicate_or_conflict"].append(
                    {
                        "email": email,
                        "email_masked": mask_email(email),
                        "reasons": sorted(reasons),
                        "nocodb_people_ids": [existing.get("Id")],
                        "source_rows": [item.source_row for item in email_candidates],
                        "names": sorted({item.full_name for item in email_candidates if item.full_name}),
                    }
                )
            else:
                fields, skipped = blank_update_fields(existing, email_candidates, email)
                if fields:
                    reports["people_update_blank_candidates"].append(
                        {
                            "email": email,
                            "email_masked": mask_email(email),
                            "nocodb_people_id": existing.get("Id"),
                            "fields_to_fill": fields,
                            "skipped_non_empty_fields": skipped,
                            "source_rows": [item.source_row for item in email_candidates],
                        }
                    )
            continue

        if reasons:
            target = (
                reports["people_duplicate_or_conflict"]
                if "same_email_different_names" in reasons or "same_name_different_emails" in reasons
                else reports["people_manual_review_candidates"]
            )
            target.append(
                {
                    "email": email,
                    "email_masked": mask_email(email),
                    "reasons": sorted(reasons),
                    "source_rows": [item.source_row for item in email_candidates],
                    "roles": sorted(roles),
                    "names": sorted({item.full_name for item in email_candidates if item.full_name}),
                    "companies": sorted(companies),
                }
            )
            continue

        fields = proposed_fields_for(email_candidates, email)
        reports["people_safe_create_candidates"].append(
            {
                "email": email,
                "email_masked": mask_email(email),
                "source_rows": [item.source_row for item in email_candidates],
                "roles": sorted(roles),
                "company_count": len(companies),
                "multiple_company": len(companies) > 1,
                "proposed_fields": fields,
            }
        )

    return reports


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
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


def masked_examples(rows: list[dict[str, Any]], limit: int = 5) -> list[dict[str, Any]]:
    examples = []
    for row in rows[:limit]:
        clone = dict(row)
        if "email" in clone:
            clone["email"] = clone.get("email_masked") or mask_email(clone["email"])
        if "proposed_fields" in clone and isinstance(clone["proposed_fields"], dict):
            fields = dict(clone["proposed_fields"])
            if "Email" in fields:
                fields["Email"] = mask_email(fields["Email"])
            clone["proposed_fields"] = fields
        examples.append(clone)
    return examples


def main() -> None:
    load_dotenv(ENV_PATH)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = REPORT_ROOT / f"people_sync_{timestamp}"
    report_dir.mkdir(parents=True, exist_ok=False)

    master_rows, headers = load_master_rows()
    people_rows = fetch_people_rows(get_people_table_id())
    candidates, missing_columns = extract_candidates(master_rows, headers)
    gaps = schema_gaps(people_rows)
    reports = analyze(candidates, people_rows, gaps)

    same_email_multi_company_accepted = sum(
        1 for row in reports["people_safe_create_candidates"] if row.get("multiple_company")
    )
    same_email_multi_company_manual = sum(
        1
        for row in reports["people_manual_review_candidates"] + reports["people_duplicate_or_conflict"]
        if len(row.get("companies", [])) > 1
    )
    invalid_language_count = sum(
        1
        for row in reports["people_manual_review_candidates"] + reports["people_duplicate_or_conflict"]
        if "invalid_language" in row.get("reasons", [])
    )
    shared_inbox_count = sum(
        1
        for row in reports["people_manual_review_candidates"] + reports["people_duplicate_or_conflict"]
        if "shared_or_company_email" in row.get("reasons", [])
    )

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "people-only dry-run read-only",
        "report_dir": str(report_dir),
        "source_rows_scanned": len(master_rows),
        "owner_candidates_extracted": len(candidates),
        "existing_people_rows_scanned": len(people_rows),
        "safe_create_count": len(reports["people_safe_create_candidates"]),
        "manual_review_count": len(reports["people_manual_review_candidates"]),
        "duplicate_or_conflict_count": len(reports["people_duplicate_or_conflict"]),
        "update_blank_candidate_count": len(reports["people_update_blank_candidates"]),
        "invalid_language_count": invalid_language_count,
        "shared_company_inbox_count": shared_inbox_count,
        "same_email_multi_company_accepted_count": same_email_multi_company_accepted,
        "same_email_multi_company_manual_review_count": same_email_multi_company_manual,
        "missing_source_columns": missing_columns,
        "schema_gaps": gaps,
        "blocker_prevents_apply": bool(gaps),
        "blocker_notes": "Schema gaps found; do not apply until reviewed." if gaps else "",
        "examples": {
            "safe_create_masked": masked_examples(reports["people_safe_create_candidates"]),
            "manual_review_masked": masked_examples(reports["people_manual_review_candidates"]),
            "duplicate_or_conflict_masked": masked_examples(reports["people_duplicate_or_conflict"]),
            "update_blank_masked": masked_examples(reports["people_update_blank_candidates"]),
        },
    }

    files = {
        "people_safe_create_candidates": reports["people_safe_create_candidates"],
        "people_manual_review_candidates": reports["people_manual_review_candidates"],
        "people_duplicate_or_conflict": reports["people_duplicate_or_conflict"],
        "people_update_blank_candidates": reports["people_update_blank_candidates"],
    }
    for name, rows in files.items():
        write_json(report_dir / f"{name}.json", rows)
        write_csv(report_dir / f"{name}.csv", rows)
    write_json(report_dir / "people_summary.json", summary)

    md = [
        "# People Sync Dry-Run Summary",
        "",
        f"Generated at: {summary['generated_at']}",
        "",
        "## Counts",
        "",
        f"- Source rows scanned: {summary['source_rows_scanned']}",
        f"- Owner candidates extracted: {summary['owner_candidates_extracted']}",
        f"- Existing People rows scanned: {summary['existing_people_rows_scanned']}",
        f"- Safe create candidates: {summary['safe_create_count']}",
        f"- Manual review candidates: {summary['manual_review_count']}",
        f"- Duplicate/conflict candidates: {summary['duplicate_or_conflict_count']}",
        f"- Update-blank candidates: {summary['update_blank_candidate_count']}",
        f"- Invalid language count: {summary['invalid_language_count']}",
        f"- Shared/company inbox count: {summary['shared_company_inbox_count']}",
        f"- Same-email multi-company accepted: {summary['same_email_multi_company_accepted_count']}",
        f"- Same-email multi-company manual review: {summary['same_email_multi_company_manual_review_count']}",
        "",
        "## Gaps",
        "",
        f"- Missing source columns: {', '.join(missing_columns) if missing_columns else 'none'}",
        f"- Schema gaps: {', '.join(gaps) if gaps else 'none'}",
        "",
        "## Masked Examples",
        "",
        "See `people_summary.json` for masked examples. Full emails are kept only in local CSV/JSON candidate files.",
    ]
    (report_dir / "people_summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print("PEOPLE_DRY_RUN_COMPLETE")
    print(f"report_dir={report_dir}")
    print(
        json.dumps(
            {
                "safe_create_count": summary["safe_create_count"],
                "manual_review_count": summary["manual_review_count"],
                "duplicate_or_conflict_count": summary["duplicate_or_conflict_count"],
                "update_blank_candidate_count": summary["update_blank_candidate_count"],
                "schema_gap_count": len(gaps),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
