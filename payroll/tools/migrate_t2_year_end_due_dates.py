"""Dry-run/apply migration for T2 Engagement year-end and due-date fields.

Historical T2 Engagements stored the T2 year end in Due Date. This migration
copies that old value to T2 Year End, recalculates Due Date as year end + 3
months, and rewrites Period End to the year end for consistency.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "_lib"
sys.path.insert(0, str(LIB))

import config.settings as settings
import data_access
from server.period_automation import calculate_t2_due, parse_date


REPORT_DIR = ROOT / "_reports"
ACTOR = "T2 Due Date Migration"


def normalize(value) -> str:
    if isinstance(value, dict):
        value = value.get("value") or value.get("title") or ""
    return " ".join(str(value or "").split())


def build_plan() -> dict:
    rows = data_access.list_engagements()
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "apply": False,
        "t2_rows_scanned": 0,
        "updates": [],
        "skipped": [],
        "warnings": [],
    }

    for row in rows:
        if normalize(row.get(settings.NOCODB_ENGAGEMENT_FIELD_TYPE)) != "T2":
            continue
        report["t2_rows_scanned"] += 1
        row_id = row.get("Id")
        company = normalize(row.get(settings.NOCODB_ENGAGEMENT_FIELD_COMPANY))
        existing_t2_year_end = parse_date(row.get(settings.NOCODB_ENGAGEMENT_FIELD_T2_YEAR_END))
        legacy_due = parse_date(row.get(settings.NOCODB_ENGAGEMENT_FIELD_DUE_DATE))
        legacy_period_end = parse_date(row.get(settings.NOCODB_ENGAGEMENT_FIELD_TAX_PERIOD_END))
        year_end = existing_t2_year_end or legacy_due or legacy_period_end
        if year_end is None:
            report["skipped"].append({
                "id": row_id,
                "company": company,
                "reason": "missing_or_invalid_year_end_source",
                "t2_year_end": row.get(settings.NOCODB_ENGAGEMENT_FIELD_T2_YEAR_END),
                "legacy_due_date": row.get(settings.NOCODB_ENGAGEMENT_FIELD_DUE_DATE),
                "legacy_period_end": row.get(settings.NOCODB_ENGAGEMENT_FIELD_TAX_PERIOD_END),
            })
            continue

        desired_year_end = year_end.isoformat()
        desired_due = calculate_t2_due(year_end).isoformat()
        desired = {
            settings.NOCODB_ENGAGEMENT_FIELD_TAX_PERIOD_END: desired_year_end,
            settings.NOCODB_ENGAGEMENT_FIELD_T2_YEAR_END: desired_year_end,
            settings.NOCODB_ENGAGEMENT_FIELD_DUE_DATE: desired_due,
        }
        before = {
            settings.NOCODB_ENGAGEMENT_FIELD_TAX_PERIOD_END: row.get(settings.NOCODB_ENGAGEMENT_FIELD_TAX_PERIOD_END),
            settings.NOCODB_ENGAGEMENT_FIELD_T2_YEAR_END: row.get(settings.NOCODB_ENGAGEMENT_FIELD_T2_YEAR_END),
            settings.NOCODB_ENGAGEMENT_FIELD_DUE_DATE: row.get(settings.NOCODB_ENGAGEMENT_FIELD_DUE_DATE),
        }
        changes = {
            field: value
            for field, value in desired.items()
            if normalize(before.get(field)) != value
        }
        if not changes:
            continue

        existing_period_end = parse_date(row.get(settings.NOCODB_ENGAGEMENT_FIELD_TAX_PERIOD_END))
        if existing_t2_year_end is None and legacy_due is not None and existing_period_end is not None and existing_period_end != legacy_due:
            report["warnings"].append({
                "id": row_id,
                "company": company,
                "reason": "period_end_differs_from_legacy_due_date",
                "period_end": existing_period_end.isoformat(),
                "legacy_due_date": legacy_due.isoformat(),
            })

        report["updates"].append({
            "id": row_id,
            "company": company,
            "before": before,
            "after": desired,
            "changes": changes,
        })

    report["stats"] = {
        "t2_rows_scanned": report["t2_rows_scanned"],
        "update_count": len(report["updates"]),
        "skipped_count": len(report["skipped"]),
        "warning_count": len(report["warnings"]),
    }
    return report


def apply_plan(report: dict) -> None:
    for item in report["updates"]:
        data_access.update_record(
            "Engagements",
            int(item["id"]),
            item["changes"],
            actor=ACTOR,
        )


def write_report(report: dict) -> Path:
    REPORT_DIR.mkdir(exist_ok=True)
    mode = "apply" if report["apply"] else "dryrun"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORT_DIR / f"t2_year_end_due_date_migration_{mode}_{timestamp}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate T2 year end and due dates")
    parser.add_argument("--apply", action="store_true", help="write updates to NocoDB")
    args = parser.parse_args()

    report = build_plan()
    report["apply"] = bool(args.apply)

    if args.apply:
        apply_plan(report)

    output = write_report(report)
    stats = report["stats"]
    print(f"Report: {output}")
    print(f"T2 rows scanned: {stats['t2_rows_scanned']}")
    print(f"Rows to update: {stats['update_count']}")
    print(f"Skipped: {stats['skipped_count']}")
    print(f"Warnings: {stats['warning_count']}")
    return 0 if not report["skipped"] else 2


if __name__ == "__main__":
    raise SystemExit(main())