"""
Repair NocoDB Companies.Incorporate Date values that were coerced to 1970-01-01.

Truth source: encrypted master Excel workbook, sheet "Master".

Default mode is dry-run. Use --apply only after reviewing the generated plan.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from typing import Any

import msoffcrypto
import requests
from dotenv import load_dotenv
from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / "_lib" / "config" / ".env"
REPORT_ROOT = ROOT / "payroll" / "_reports"
DEFAULT_MASTER_PATH = Path(
    r"\\file-server\Shared Folder\4. General - Scan-Notes-Form-PPT"
    r"\4. Accounting client list\#Business Master Client List.xlsx"
)

EMPTY_MARKERS = {"", "na", "n/a", "none", "-", "--", "null"}
DATE_FIELD = "Incorporate Date"
BAD_DATE = "1970-01-01"


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return " ".join(str(value).replace("\n", " ").split()).strip()


def is_blank(value: Any) -> bool:
    return normalize_text(value).casefold() in EMPTY_MARKERS


def normalize_key(value: Any) -> str:
    return re.sub(r"\s+", " ", normalize_text(value)).casefold()


def normalize_identifier(value: Any) -> str:
    text = normalize_text(value)
    if is_blank(text):
        return ""
    return re.sub(r"[^0-9A-Za-z]", "", text).casefold()


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
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y", "%Y-%b-%d", "%Y-%B-%d", "%Y-%m", "%Y"):
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


def load_master_rows() -> list[dict[str, Any]]:
    password = os.getenv("CLIENT_MASTER_PASSWORD", "")
    master_path = Path(os.getenv("MASTER_EXCEL_PATH") or DEFAULT_MASTER_PATH)
    if not password:
        raise RuntimeError("CLIENT_MASTER_PASSWORD is not configured")
    if not master_path.exists():
        raise RuntimeError(f"Master Excel not found: {master_path}")

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


def fetch_companies() -> list[dict[str, Any]]:
    base_url = os.getenv("NOCODB_URL", "").rstrip("/")
    token = os.getenv("NOCODB_TOKEN", "")
    table_id = os.getenv("NOCODB_COMPANIES_TABLE_ID", "")
    if not base_url or not token or not table_id:
        raise RuntimeError("NOCODB_URL, NOCODB_TOKEN, or NOCODB_COMPANIES_TABLE_ID is not configured")

    rows: list[dict[str, Any]] = []
    offset = 0
    headers = {"xc-token": token, "Content-Type": "application/json"}
    while True:
        response = requests.get(
            f"{base_url}/api/v2/tables/{table_id}/records",
            headers=headers,
            params={"limit": 100, "offset": offset},
            timeout=30,
        )
        response.raise_for_status()
        batch = response.json().get("list", [])
        rows.extend(batch)
        if len(batch) < 100:
            break
        offset += 100
    return rows


def patch_companies(plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    base_url = os.getenv("NOCODB_URL", "").rstrip("/")
    token = os.getenv("NOCODB_TOKEN", "")
    table_id = os.getenv("NOCODB_COMPANIES_TABLE_ID", "")
    headers = {"xc-token": token, "Content-Type": "application/json"}
    url = f"{base_url}/api/v2/tables/{table_id}/records"
    results: list[dict[str, Any]] = []

    for item in plan:
        payload = [{"Id": item["nocodb_id"], DATE_FIELD: item["new_incorporate_date"]}]
        try:
            response = requests.patch(url, headers=headers, json=payload, timeout=30)
            if response.status_code >= 400:
                results.append(
                    {
                        "nocodb_id": item["nocodb_id"],
                        "legal_name": item["legal_name"],
                        "status": "failed",
                        "error": response.text[:500],
                    }
                )
                continue
            results.append(
                {
                    "nocodb_id": item["nocodb_id"],
                    "legal_name": item["legal_name"],
                    "status": "updated",
                    "new_incorporate_date": item["new_incorporate_date"],
                }
            )
        except requests.RequestException as exc:
            results.append(
                {
                    "nocodb_id": item["nocodb_id"],
                    "legal_name": item["legal_name"],
                    "status": "failed",
                    "error": str(exc),
                }
            )
    return results


def build_plan(master_rows: list[dict[str, Any]], companies: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    plan: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    stop_reasons: list[str] = []

    master_by_legal: dict[str, list[dict[str, Any]]] = defaultdict(list)
    live_by_legal: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in master_rows:
        key = normalize_key(row.get("Legal Name"))
        if key:
            master_by_legal[key].append(row)
    for row in companies:
        key = normalize_key(row.get("Legal Name"))
        if key:
            live_by_legal[key].append(row)

    bad_rows = [row for row in companies if normalize_text(row.get(DATE_FIELD)) == BAD_DATE]
    for live in bad_rows:
        legal_name = normalize_text(live.get("Legal Name"))
        legal_key = normalize_key(legal_name)
        live_duplicates = live_by_legal.get(legal_key, [])
        master_matches = master_by_legal.get(legal_key, [])

        if len(live_duplicates) > 1:
            skipped.append({"nocodb_id": live.get("Id"), "legal_name": legal_name, "reason": "duplicate_live_legal_name"})
            continue
        if not master_matches:
            skipped.append({"nocodb_id": live.get("Id"), "legal_name": legal_name, "reason": "missing_master_match"})
            continue
        if len(master_matches) > 1:
            skipped.append({"nocodb_id": live.get("Id"), "legal_name": legal_name, "reason": "duplicate_master_legal_name"})
            continue

        master = master_matches[0]
        conflicts = []
        for field in ("Business Number", "Entity Number"):
            live_value = normalize_identifier(live.get(field))
            master_value = normalize_identifier(master.get(field))
            if live_value and master_value and live_value != master_value:
                conflicts.append({"field": field, "live": live_value, "master": master_value})
        if conflicts:
            skipped.append(
                {
                    "nocodb_id": live.get("Id"),
                    "legal_name": legal_name,
                    "reason": "identifier_conflict",
                    "conflicts": conflicts,
                    "master_excel_row": master.get("_excel_row"),
                }
            )
            continue

        parsed = parse_date(master.get(DATE_FIELD))
        if not parsed:
            skipped.append(
                {
                    "nocodb_id": live.get("Id"),
                    "legal_name": legal_name,
                    "reason": "master_date_blank_or_invalid",
                    "master_excel_row": master.get("_excel_row"),
                    "master_raw_value": normalize_text(master.get(DATE_FIELD)),
                }
            )
            continue
        if parsed == BAD_DATE:
            skipped.append(
                {
                    "nocodb_id": live.get("Id"),
                    "legal_name": legal_name,
                    "reason": "master_date_is_1970",
                    "master_excel_row": master.get("_excel_row"),
                }
            )
            stop_reasons.append(f"Master parsed to 1970 for planned row: {legal_name}")
            continue

        plan.append(
            {
                "nocodb_id": live.get("Id"),
                "legal_name": legal_name,
                "current_live_value": normalize_text(live.get(DATE_FIELD)),
                "new_incorporate_date": parsed,
                "master_excel_row": master.get("_excel_row"),
                "match_evidence": {
                    "legal_name": "exact_normalized",
                    "business_number_live": normalize_text(live.get("Business Number")),
                    "business_number_master": normalize_text(master.get("Business Number")),
                    "entity_number_live": normalize_text(live.get("Entity Number")),
                    "entity_number_master": normalize_text(master.get("Entity Number")),
                },
            }
        )

    if bad_rows and not plan:
        stop_reasons.append("Live 1970-01-01 rows exist, but planned update count is zero")
    return plan, skipped, sorted(set(stop_reasons))


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
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


def summarize(companies: list[dict[str, Any]]) -> dict[str, int]:
    total = len(companies)
    bad = sum(1 for row in companies if normalize_text(row.get(DATE_FIELD)) == BAD_DATE)
    blank = sum(1 for row in companies if is_blank(row.get(DATE_FIELD)))
    valid = total - bad - blank
    return {"total": total, "bad_1970": bad, "blank": blank, "non_1970_non_blank": valid}


def write_summary(path: Path, summary: dict[str, Any]) -> None:
    skipped_counts = summary.get("skipped_counts_by_reason", {})
    lines = [
        "# Company Incorporate Date Repair Summary",
        "",
        f"Generated at: {summary['generated_at']}",
        f"Mode: {summary['mode']}",
        f"Status: {summary['status']}",
        "",
        "## Counts",
        "",
        f"- Total live Companies rows: {summary['live_before']['total']}",
        f"- Live 1970-01-01 before: {summary['live_before']['bad_1970']}",
        f"- Planned updates: {summary['planned_update_count']}",
        f"- Applied updates: {summary['applied_update_count']}",
        f"- Failed updates: {summary['failed_update_count']}",
        f"- Live 1970-01-01 after: {summary.get('live_after', {}).get('bad_1970', 'not_run')}",
        "",
        "## Skipped Counts",
        "",
    ]
    if skipped_counts:
        lines.extend(f"- {reason}: {count}" for reason, count in sorted(skipped_counts.items()))
    else:
        lines.append("- none: 0")
    if summary.get("stop_reasons"):
        lines.extend(["", "## Stop Reasons", ""])
        lines.extend(f"- {reason}" for reason in summary["stop_reasons"])
    lines.extend(
        [
            "",
            "## Explicit Confirmation",
            "",
            "- Schema changes: 0",
            "- People changes: 0",
            "- Engagements changes: 0",
            "- Email/outbound communication: 0",
            "- PATCH payload limited to Id and Incorporate Date",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Repair NocoDB Companies Incorporate Date values.")
    parser.add_argument("--apply", action="store_true", help="Apply the generated repair plan.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_dotenv(ENV_PATH)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = REPORT_ROOT / f"company_incorporate_date_repair_{timestamp}"
    report_dir.mkdir(parents=True, exist_ok=False)

    master_rows = load_master_rows()
    companies_before = fetch_companies()
    plan, skipped, stop_reasons = build_plan(master_rows, companies_before)
    live_before = summarize(companies_before)
    skipped_counts = Counter(item["reason"] for item in skipped)

    write_json(report_dir / "dry_run_plan.json", plan)
    write_csv(report_dir / "dry_run_plan.csv", plan)
    write_json(report_dir / "skipped_rows.json", skipped)

    apply_results: list[dict[str, Any]] = []
    companies_after = companies_before
    status = "dry_run_complete"
    if stop_reasons:
        status = "stopped_before_apply"
    elif args.apply:
        apply_results = patch_companies(plan)
        write_json(report_dir / "apply_results.json", apply_results)
        companies_after = fetch_companies()
        status = "apply_completed"

    failed_count = sum(1 for item in apply_results if item["status"] == "failed")
    applied_count = sum(1 for item in apply_results if item["status"] == "updated")
    live_after = summarize(companies_after)
    post_verify = {
        "patched_rows_checked": [],
        "remaining_1970_rows": [
            {"Id": row.get("Id"), "Legal Name": row.get("Legal Name"), DATE_FIELD: row.get(DATE_FIELD)}
            for row in companies_after
            if normalize_text(row.get(DATE_FIELD)) == BAD_DATE
        ],
    }
    if args.apply:
        expected_by_id = {item["nocodb_id"]: item["new_incorporate_date"] for item in plan}
        live_by_id = {row.get("Id"): row for row in companies_after}
        for row_id, expected in expected_by_id.items():
            row = live_by_id.get(row_id, {})
            post_verify["patched_rows_checked"].append(
                {
                    "Id": row_id,
                    "expected": expected,
                    "actual": normalize_text(row.get(DATE_FIELD)),
                    "ok": normalize_text(row.get(DATE_FIELD)) == expected,
                }
            )
        write_json(report_dir / "post_apply_verify.json", post_verify)

    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "apply" if args.apply else "dry-run",
        "status": status,
        "report_dir": str(report_dir),
        "live_before": live_before,
        "live_after": live_after if args.apply else {},
        "master_rows": len(master_rows),
        "planned_update_count": len(plan),
        "applied_update_count": applied_count,
        "failed_update_count": failed_count,
        "skipped_counts_by_reason": dict(skipped_counts),
        "stop_reasons": stop_reasons,
        "sample_first_10_planned_updates": plan[:10],
    }
    write_json(report_dir / "summary.json", summary)
    write_summary(report_dir / "summary.md", summary)

    print("REPORT_DIR=" + str(report_dir))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if stop_reasons:
        return 2
    if args.apply and failed_count:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
