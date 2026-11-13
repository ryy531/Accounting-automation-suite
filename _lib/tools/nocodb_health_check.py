"""
tools/nocodb_health_check.py -- read-only NocoDB platform health check.

Scope: connection check, per-table metadata/hook/row counts, selected field
value distributions, and known aggregate data-quality signals. Never writes to
NocoDB. Report goes to _reports/ (gitignored) and contains only aggregate
counts -- no customer names, emails, SIN, URLs, tokens, or webhook destinations.

Companies/People/Engagements row reads go through data_access.py (the business
data contract). Events has no data_access read function yet, so this tool falls
back to NocoDBClient directly for Events row reads and uses
NocoDBClient.get_table_metadata for all four tables' field/view counts -- this
is the documented tools/ exception in _lib/AGENTS.md (read-only metadata may
use connectors directly; business read/write still goes through data_access.py).
"""
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config.settings as settings
import data_access
from connectors import nocodb_client

ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = ROOT / "_reports"

TABLES = ("Companies", "People", "Engagements", "Events")

_DATA_ACCESS_ROW_FN = {
    "Companies": data_access.list_companies,
    "People": data_access.list_people,
    "Engagements": data_access.list_engagements,
}

_TABLE_ID = {
    "Companies": settings.NOCODB_COMPANIES_TABLE_ID,
    "People": settings.NOCODB_PEOPLE_TABLE_ID,
    "Engagements": settings.NOCODB_ENGAGEMENTS_TABLE_ID,
    "Events": settings.NOCODB_EVENTS_TABLE_ID,
}

# Discipline: this is not a schema mirror. These fields must stay limited to
# names already referenced by current business code/scripts. Add or remove items
# only when the business code references change.
_DISTRIBUTION_FIELDS = {
    "Companies": (
        "client status",
        "Is Payroll Client",
        "Pay Frequency",
        "GST Frequency",
        "PST Frequency",
        "Workbench Priority",
        "Workbench Needs Review",
    ),
    "People": (
        "client status",
        "Language",
    ),
    "Engagements": (
        "Type",
        "Status",
        "Frequency",
        "Priority",
        "Assigned To",
        "GST Status",
        "PST Status",
        "T2 Status",
        "Bookkeeping Status",
        "Annual Return Status",
    ),
    "Events": (
        "Event Type",
    ),
}

_ENGAGEMENT_BLANK_FIELDS = (
    "Type",
    "Company",
    "Status",
    "Frequency",
    "Priority",
    "Assigned To",
    "Period Start",
    "Period End",
    "Due Date",
)

_ALLOWED_PEOPLE_LANGUAGE_VALUES = {"(blank)", "Chinese", "English", "Vietnamese"}


def _available_field_titles(meta: dict) -> set[str]:
    return {
        column.get("title")
        for column in meta.get("columns", [])
        if column.get("title")
    }


def _normalize_value(value) -> str:
    if isinstance(value, dict):
        value = value.get("value") or value.get("title") or ""
    if value is None:
        return "(blank)"
    text = " ".join(str(value).split())
    return text if text else "(blank)"


def _iter_distribution_values(value) -> Iterable[str]:
    if isinstance(value, list):
        if not value:
            yield "(blank)"
            return
        for item in value:
            yield _normalize_value(item)
        return
    yield _normalize_value(value)


def _distribution(rows: list[dict], field: str) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        for value in _iter_distribution_values(row.get(field)):
            counts[value] += 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _field_distributions(name: str, meta: dict, rows: list[dict]) -> dict:
    available = _available_field_titles(meta)
    distributions = {}
    missing = []
    for field in _DISTRIBUTION_FIELDS.get(name, ()):  # field names are safe metadata
        if field not in available:
            missing.append(field)
            continue
        distributions[field] = _distribution(rows, field)
    result = {"fields": distributions}
    if missing:
        result["missing_tracked_fields"] = missing
    return result


def _blank_field_summary(rows: list[dict], fields: Iterable[str]) -> dict[str, dict]:
    total = len(rows)
    summary = {}
    for field in fields:
        blank_count = sum(1 for row in rows if _normalize_value(row.get(field)) == "(blank)")
        summary[field] = {
            "blank_count": blank_count,
            "blank_ratio": round(blank_count / total, 4) if total else 0,
        }
    return summary


def _max_row_id(rows: list[dict]) -> int | None:
    ids = []
    for row in rows:
        raw = row.get("Id", row.get("id"))
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    return max(ids) if ids else None


def _latest_timestamp(rows: list[dict]) -> str | None:
    values = [
        _normalize_value(row.get("Timestamp"))
        for row in rows
        if _normalize_value(row.get("Timestamp")) != "(blank)"
    ]
    return max(values) if values else None


def _quality_signals(name: str, rows: list[dict]) -> dict:
    if name == "People":
        language_distribution = _distribution(rows, "Language")
        anomalies = {
            value: count
            for value, count in language_distribution.items()
            if value not in _ALLOWED_PEOPLE_LANGUAGE_VALUES
        }
        return {
            "people_language_anomalies": {
                "status": "needs_review" if anomalies else "ok",
                "anomalous_values": anomalies,
            }
        }

    if name == "Engagements":
        return {
            "blank_fields": _blank_field_summary(rows, _ENGAGEMENT_BLANK_FIELDS)
        }

    if name == "Events":
        return {
            "growth_marker": {
                "row_count": len(rows),
                "max_id": _max_row_id(rows),
                "latest_timestamp": _latest_timestamp(rows),
                "note": "Compare row_count/max_id with the previous report to detect stalled Events growth.",
            }
        }

    return {}


def _fetch_rows(client: nocodb_client.NocoDBClient, name: str, table_id: str) -> list[dict]:
    row_fn = _DATA_ACCESS_ROW_FN.get(name)
    if row_fn is not None:
        return row_fn()
    return client.list_rows(table_id=table_id).get("list", [])


def _check_table(client: nocodb_client.NocoDBClient, name: str) -> dict:
    result = {"table": name}
    table_id = _TABLE_ID.get(name)
    if not table_id:
        result["error"] = "table id not configured in .env"
        return result

    try:
        meta = client.get_table_metadata(table_id)
        result["field_count"] = len(meta.get("columns", []))
        result["view_count"] = len(meta.get("views", []))
        result["hook_count"] = len(client.get_table_hooks(table_id))
    except Exception as exc:
        result["error"] = f"metadata fetch failed: {type(exc).__name__}"
        return result

    try:
        rows = _fetch_rows(client, name, table_id)
        result["row_count"] = len(rows)
        result["field_distributions"] = _field_distributions(name, meta, rows)
        signals = _quality_signals(name, rows)
        if signals:
            result["quality_signals"] = signals
    except Exception as exc:
        result["error"] = f"row fetch failed: {type(exc).__name__}"

    return result


def run_health_check() -> dict:
    client = nocodb_client.NocoDBClient()
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "nocodb_url_configured": bool(settings.NOCODB_URL),
        "tables": [_check_table(client, name) for name in TABLES],
    }
    return report


def write_report(report: dict) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REPORT_DIR / f"nocodb_health_check_{stamp}.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def main() -> None:
    report = run_health_check()
    path = write_report(report)
    print(f"NocoDB health check written to {path}")
    has_error = False
    for entry in report["tables"]:
        if "error" in entry:
            has_error = True
            print(f"  {entry['table']}: ERROR - {entry['error']}")
        else:
            distribution_count = len(
                entry.get("field_distributions", {}).get("fields", {})
            )
            signal_count = len(entry.get("quality_signals", {}))
            print(
                f"  {entry['table']}: rows={entry.get('row_count', '?')} "
                f"fields={entry.get('field_count', '?')} "
                f"views={entry.get('view_count', '?')} "
                f"hooks={entry.get('hook_count', '?')} "
                f"distributions={distribution_count} "
                f"signals={signal_count}"
            )
    if has_error:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
