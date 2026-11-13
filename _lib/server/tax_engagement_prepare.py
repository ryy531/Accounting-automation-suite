"""Daily GST/PST/T2 engagement preparation for NocoDB.

The default mode is dry-run. Use ``--apply`` to refresh the three workbench
date filters and create missing Engagements.
"""

import argparse
import contextlib
import json
import logging
import msvcrt
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config.settings as settings
import data_access
from server.period_automation import add_months, calculate_t2_due, parse_date
from utils.retry import retry


logger = logging.getLogger(__name__)

try:
    TIMEZONE = ZoneInfo(settings.PAYROLL_TIMEZONE)
except ZoneInfoNotFoundError:
    TIMEZONE = timezone(timedelta(hours=-6), name="America/Regina")
LOG_DIR = Path(__file__).resolve().parents[1] / "logs"
LOCK_PATH = Path(__file__).resolve().parent / "_locks" / "tax_engagement_prepare.lock"
STATUS_NOT_STARTED = "stage | not started"
EVENT_TYPE = "Manual Action"

PRIORITY_BY_URGENCY = {
    "Needs Review": "P0 Needs Review",
    "Overdue": "P1 Overdue",
    "Due This Week": "P2 Due This Week",
    "Due Soon": "P3 Due Soon",
    "OK": "P4 OK",
    "Blocked": "Blocked",
}

TAX_CONFIGS = {
    "GST": {
        "frequency_field": settings.NOCODB_COMPANY_FIELD_GST_FREQUENCY,
        "filed_until_field": settings.NOCODB_COMPANY_FIELD_GST_FILED_UNTIL,
        "period_end_field": settings.NOCODB_COMPANY_FIELD_GST_NEXT_PERIOD_END,
        "due_date_field": settings.NOCODB_COMPANY_FIELD_GST_DUE_NEXT_PERIOD,
        "urgency_field": settings.NOCODB_COMPANY_FIELD_GST_URGENCY,
        "status_field": settings.NOCODB_ENGAGEMENT_FIELD_GST_STATUS,
        "view_id": settings.NOCODB_GST_WORKBENCH_VIEW_ID,
    },
    "PST": {
        "frequency_field": settings.NOCODB_COMPANY_FIELD_PST_FREQUENCY,
        "filed_until_field": settings.NOCODB_COMPANY_FIELD_PST_FILED_UNTIL,
        "period_end_field": settings.NOCODB_COMPANY_FIELD_PST_NEXT_PERIOD_END,
        "due_date_field": settings.NOCODB_COMPANY_FIELD_PST_DUE_NEXT_PERIOD,
        "urgency_field": settings.NOCODB_COMPANY_FIELD_PST_URGENCY,
        "status_field": settings.NOCODB_ENGAGEMENT_FIELD_PST_STATUS,
        "view_id": settings.NOCODB_PST_WORKBENCH_VIEW_ID,
    },
    "T2": {
        "frequency_field": None,
        "filed_until_field": settings.NOCODB_COMPANY_FIELD_T2_FILED_UNTIL,
        "period_end_field": settings.NOCODB_COMPANY_FIELD_T2_DUE_NEXT_PERIOD,
        "due_date_field": None,
        "urgency_field": settings.NOCODB_COMPANY_FIELD_T2_URGENCY,
        "status_field": settings.NOCODB_ENGAGEMENT_FIELD_T2_STATUS,
        "view_id": settings.NOCODB_T2_WORKBENCH_VIEW_ID,
    },
}


def normalize_value(value) -> str:
    """Normalize NocoDB select values represented as strings or dicts."""
    if isinstance(value, dict):
        value = value.get("value") or value.get("title") or ""
    return " ".join(str(value or "").split())


def month_window(today: date) -> tuple[date, date]:
    """Return previous calendar-month start through next month end."""
    current_month_start = date(today.year, today.month, 1)
    return (
        add_months(current_month_start, -1),
        add_months(current_month_start, 2) - timedelta(days=1),
    )


def due_date_in_window(engagement_type: str, due_date: date, today: date) -> bool:
    """Apply the user-approved GST/PST/T2 workbench date rules."""
    lower, upper = month_window(today)
    if engagement_type == "T2":
        return due_date <= upper
    return lower <= due_date <= upper


def priority_for_due_date(due_date: date, today: date, urgency=None) -> str:
    """Use Companies urgency when present, otherwise calculate P0-P4 priority."""
    normalized_urgency = normalize_value(urgency)
    if normalized_urgency in PRIORITY_BY_URGENCY:
        return PRIORITY_BY_URGENCY[normalized_urgency]

    days_until = (due_date - today).days
    if days_until < 0:
        return "P1 Overdue"
    if days_until <= 7:
        return "P2 Due This Week"
    if days_until <= 30:
        return "P3 Due Soon"
    return "P4 OK"


def build_candidate(company: dict, engagement_type: str, today: date) -> tuple[dict | None, str | None]:
    """Build one tax Engagement candidate from a Companies row."""
    config = TAX_CONFIGS[engagement_type]
    company_name = normalize_value(
        company.get(settings.NOCODB_COMPANY_FIELD_LEGAL_NAME)
    )
    if not company_name:
        return None, "missing_legal_name"

    filed_until = parse_date(company.get(config["filed_until_field"]))
    period_end = parse_date(company.get(config["period_end_field"]))
    if filed_until is None:
        return None, "missing_or_invalid_filed_until"
    if period_end is None:
        return None, "missing_or_invalid_period_end"

    due_date = (
        calculate_t2_due(period_end)
        if engagement_type == "T2"
        else parse_date(company.get(config["due_date_field"]))
    )
    if due_date is None:
        return None, "missing_or_invalid_due_date"
    if not due_date_in_window(engagement_type, due_date, today):
        return None, "outside_due_window"

    if engagement_type == "T2":
        frequency = "Annual"
    else:
        frequency = normalize_value(company.get(config["frequency_field"]))
        if frequency not in {"Monthly", "Quarterly", "Annual"}:
            return None, "missing_or_invalid_frequency"

    period_start = filed_until + timedelta(days=1)
    # NOT feeding the Companies-level urgency field into priority anymore --
    # confirmed 2026-06-25 it's stale batch-imported data (164/166 companies
    # have a non-blank value, nothing in the codebase ever writes it), not a
    # maintained override. Kept in the notes annotation below only as a
    # breadcrumb in case it turns out to matter for some company after all.
    urgency = normalize_value(company.get(config["urgency_field"]))
    priority = priority_for_due_date(due_date, today)
    notes = f"Auto-created from Companies tax schedule on {today.isoformat()}"
    if urgency:
        notes += f"; {engagement_type} Urgency (not used for priority): {urgency}"

    fields = {
        settings.NOCODB_ENGAGEMENT_FIELD_NAME: (
            f"{company_name} {engagement_type} {period_end.isoformat()}"
        ),
        settings.NOCODB_ENGAGEMENT_FIELD_TYPE: engagement_type,
        settings.NOCODB_ENGAGEMENT_FIELD_COMPANY: company_name,
        settings.NOCODB_ENGAGEMENT_FIELD_FREQUENCY: frequency,
        settings.NOCODB_ENGAGEMENT_FIELD_TAX_PERIOD_START: period_start.isoformat(),
        settings.NOCODB_ENGAGEMENT_FIELD_TAX_PERIOD_END: period_end.isoformat(),
        settings.NOCODB_ENGAGEMENT_FIELD_DUE_DATE: due_date.isoformat(),
        **(
            {settings.NOCODB_ENGAGEMENT_FIELD_T2_YEAR_END: period_end.isoformat()}
            if engagement_type == "T2"
            else {}
        ),
        settings.NOCODB_ENGAGEMENT_FIELD_YEAR: period_end.year,
        settings.NOCODB_ENGAGEMENT_FIELD_PRIORITY: priority,
        settings.NOCODB_ENGAGEMENT_FIELD_SOURCE: "Auto",
        settings.NOCODB_ENGAGEMENT_FIELD_NOTES: notes,
        config["status_field"]: STATUS_NOT_STARTED,
    }
    return fields, None


_DONE_STATUSES = {"stage | completed", "stage | archived"}


def is_done(engagement_row: dict, engagement_type: str) -> bool:
    """True if the engagement's own status field shows it's finished --
    don't bother refreshing Priority on something already filed/archived."""
    config = TAX_CONFIGS[engagement_type]
    raw = engagement_row.get(config["status_field"], "")
    status_str = ",".join(str(v) for v in raw) if isinstance(raw, list) else str(raw or "")
    return any(d in status_str for d in _DONE_STATUSES)


def refresh_stale_priorities(
    engagements: list, today: date, *, actor: str, apply: bool
) -> tuple[list, list]:
    """
    Recompute Priority for every open (not-done) GST/PST/T2 engagement from its
    own Due Date and write back anything that's drifted.

    Priority is otherwise only ever set once, at creation (see build_candidate)
    -- nothing else in the codebase (this scan's own create-path, or any
    webhook) ever revisits it, so it silently goes stale as days pass (an item
    created as "P3 Due Soon" stays "P3 Due Soon" forever, even after it
    becomes overdue).

    Deliberately NOT keyed off build_candidate's per-company "next period"
    candidate matching -- a company can have more than one open engagement of
    the same type simultaneously (e.g. its Filed Until cursor is stuck behind
    on an older unfiled period while a newer period's engagement already
    exists), and only the single candidate the cursor currently points at
    would ever be visited that way. This walks every open engagement directly
    instead, so none of them get silently skipped.

    Does NOT use the Companies-level urgency override build_candidate uses --
    see the comment at the call site below.
    """
    refreshed, candidates = [], []
    for engagement in engagements:
        engagement_type = normalize_value(engagement.get(settings.NOCODB_ENGAGEMENT_FIELD_TYPE))
        if engagement_type not in TAX_CONFIGS:
            continue
        if is_done(engagement, engagement_type):
            continue

        # P0 Needs Review is a sticky exception flag -- a human set it
        # (whether by hand, or as a side effect of the now-disabled urgency
        # override) and should be the one to clear it, not this automated
        # date-based recalculation.
        if normalize_value(engagement.get(settings.NOCODB_ENGAGEMENT_FIELD_PRIORITY)) == "P0 Needs Review":
            continue

        due = parse_date(engagement.get(settings.NOCODB_ENGAGEMENT_FIELD_DUE_DATE))
        if due is None:
            continue

        company_name = normalize_value(
            engagement.get(settings.NOCODB_ENGAGEMENT_FIELD_COMPANY)
        )

        # Deliberately NOT using the Companies-level urgency override here --
        # confirmed 2026-06-25 it's stale batch-imported data (164/166
        # companies have a non-blank value, none of it ever written by any
        # script/webhook), not a maintained signal. Pure date math only.
        new_priority = priority_for_due_date(due, today)
        old_priority = normalize_value(engagement.get(settings.NOCODB_ENGAGEMENT_FIELD_PRIORITY))
        if new_priority == old_priority:
            continue

        record = {
            "company": company_name,
            "type": engagement_type,
            "engagement_id": engagement.get("Id"),
            "old_priority": old_priority,
            "new_priority": new_priority,
        }
        if not apply:
            candidates.append(record)
            continue

        try:
            data_access.update_record(
                table="Engagements",
                record_id=engagement.get("Id"),
                fields={settings.NOCODB_ENGAGEMENT_FIELD_PRIORITY: new_priority},
                actor=actor,
            )
            refreshed.append(record)
        except Exception as exc:
            logger.exception(
                "Failed refreshing Priority for %s %s", engagement_type, company_name
            )
            record["error"] = str(exc)
            candidates.append(record)

    return refreshed, candidates


def identity_keys(row: dict) -> tuple[tuple[str, str, str], tuple[str, str, str]]:
    """Return period-end and due-date keys used for conservative deduplication."""
    company = normalize_value(row.get(settings.NOCODB_ENGAGEMENT_FIELD_COMPANY)).casefold()
    engagement_type = normalize_value(
        row.get(settings.NOCODB_ENGAGEMENT_FIELD_TYPE)
    )
    period_end = normalize_value(
        row.get(settings.NOCODB_ENGAGEMENT_FIELD_TAX_PERIOD_END)
    )
    due_date = normalize_value(row.get(settings.NOCODB_ENGAGEMENT_FIELD_DUE_DATE))
    return (
        (company, engagement_type, period_end),
        (company, engagement_type, due_date),
    )


def refresh_tax_view_filters(today: date) -> dict:
    """Set relative-day filters to exact adjacent calendar-month bounds."""
    lower, upper = month_window(today)
    lower_days = (today - lower).days
    upper_days = (upper - today).days
    changes = {}
    for engagement_type, config in TAX_CONFIGS.items():
        changes[engagement_type] = data_access.update_tax_workbench_date_filters(
            config["view_id"],
            lower_sub_op=(
                None if engagement_type == "T2" else "daysAgo"
            ),
            lower_value=None if engagement_type == "T2" else lower_days,
            upper_sub_op="daysFromNow",
            upper_value=upper_days,
        )
    for engagement_type, view_id in {
        "Bookkeeping": settings.NOCODB_BOOKKEEPING_WORKBENCH_VIEW_ID,
        "Annual Return": settings.NOCODB_ANNUAL_RETURN_WORKBENCH_VIEW_ID,
    }.items():
        changes[engagement_type] = data_access.update_tax_workbench_date_filters(
            view_id,
            lower_sub_op="daysAgo",
            lower_value=lower_days,
            upper_sub_op="daysFromNow",
            upper_value=upper_days,
        )
    return changes


@contextlib.contextmanager
def _apply_lock():
    """Hold an exclusive, crash-safe lock for the duration of an apply run.

    Locks a byte range of a dedicated file via msvcrt.locking rather than
    using a create/delete lock-file pattern: the OS releases the lock
    automatically if this process dies or is killed, so there is no stale
    lock file to clean up after a crash. Raises OSError if another apply
    run already holds the lock.
    """
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(LOCK_PATH, "a+b")
    try:
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock_file.close()
        raise
    try:
        yield
    finally:
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        lock_file.close()


def scan_tax_engagements(today: date, apply: bool = False, actor: str = "Tax Engagement Prepare") -> dict:
    """Scan Companies and optionally create missing GST/PST/T2 Engagements.

    When ``apply`` is True, this holds a crash-safe file lock (see
    ``_apply_lock``) for the duration of the run, so two overlapping apply
    runs (e.g. a future cron run and an on-demand bot trigger) cannot race
    past each other's in-memory dedupe check and create duplicate
    Engagements. If another apply run already holds the lock, this returns
    ``{"error": ..., "locked": True}`` immediately instead of the normal
    ``{stats, created, skipped_existing, errors, ...}`` shape — callers
    must check for the ``locked`` key before assuming the normal shape.
    """
    if apply:
        try:
            with _apply_lock():
                return _scan_tax_engagements(today, apply=True, actor=actor)
        except OSError:
            return {"error": "refresh already in progress", "locked": True}
    return _scan_tax_engagements(today, apply=False, actor=actor)


def _scan_tax_engagements(today: date, apply: bool = False, actor: str = "Tax Engagement Prepare") -> dict:
    lower, upper = month_window(today)
    summary = {
        "run_date": today.isoformat(),
        "dry_run": not apply,
        "window": {
            "GST": {
                "from": lower.isoformat(),
                "through": upper.isoformat(),
                "native_filters": ["daysAgo", "daysFromNow"],
            },
            "PST": {
                "from": lower.isoformat(),
                "through": upper.isoformat(),
                "native_filters": ["daysAgo", "daysFromNow"],
            },
            "T2": {
                "from": "all past due",
                "through": upper.isoformat(),
                "native_filters": ["daysFromNow"],
            },
        },
        "companies_scanned": 0,
        "candidates": [],
        "created": [],
        "skipped_existing": [],
        "skipped": [],
        "errors": [],
        "view_filter_changes": {},
    }

    if apply:
        summary["view_filter_changes"] = refresh_tax_view_filters(today)

    # Retried: absorb transient NocoDB connection blips (same risk class as payroll_prepare.py)
    companies = retry(data_access.list_companies)
    engagements = retry(data_access.list_engagements)
    summary["companies_scanned"] = len(companies)

    period_keys = set()
    due_keys = set()
    for engagement in engagements:
        period_key, due_key = identity_keys(engagement)
        if period_key[2]:
            period_keys.add(period_key)
        if due_key[2]:
            due_keys.add(due_key)

    summary["priority_refreshed"], summary["priority_refresh_candidates"] = refresh_stale_priorities(
        engagements, today, actor=actor, apply=apply
    )

    for company in companies:
        company_name = normalize_value(
            company.get(settings.NOCODB_COMPANY_FIELD_LEGAL_NAME)
        ) or f"<Id {company.get('Id', '?')}>"
        for engagement_type in TAX_CONFIGS:
            fields, reason = build_candidate(company, engagement_type, today)
            if fields is None:
                summary["skipped"].append({
                    "company": company_name,
                    "type": engagement_type,
                    "reason": reason,
                })
                continue

            period_key, due_key = identity_keys(fields)
            if period_key in period_keys or due_key in due_keys:
                summary["skipped_existing"].append({
                    "company": company_name,
                    "type": engagement_type,
                    "period_end": fields[settings.NOCODB_ENGAGEMENT_FIELD_TAX_PERIOD_END],
                    "due_date": fields[settings.NOCODB_ENGAGEMENT_FIELD_DUE_DATE],
                })
                continue

            record = {
                "company": company_name,
                "type": engagement_type,
                "period_start": fields[settings.NOCODB_ENGAGEMENT_FIELD_TAX_PERIOD_START],
                "period_end": fields[settings.NOCODB_ENGAGEMENT_FIELD_TAX_PERIOD_END],
                "due_date": fields[settings.NOCODB_ENGAGEMENT_FIELD_DUE_DATE],
                "priority": fields[settings.NOCODB_ENGAGEMENT_FIELD_PRIORITY],
            }
            summary["candidates"].append(record)

            if not apply:
                period_keys.add(period_key)
                due_keys.add(due_key)
                continue

            try:
                created = data_access.create_recurring_engagement(fields)
                created_row = created[0] if isinstance(created, list) and created else created
                created_id = (
                    created_row.get("Id") if isinstance(created_row, dict) else fields["Name"]
                )
                data_access.create_event(
                    engagement_id=created_id,
                    event_type=EVENT_TYPE,
                    description=(
                        f"Auto-created {engagement_type} Engagement for "
                        f"{company_name}; due {record['due_date']}"
                    ),
                    actor=actor,
                )
                summary["created"].append({**record, "engagement_id": created_id})
                period_keys.add(period_key)
                due_keys.add(due_key)
            except Exception as exc:
                logger.exception("Failed creating %s for %s", engagement_type, company_name)
                summary["errors"].append({
                    "company": company_name,
                    "type": engagement_type,
                    "error": str(exc),
                })

    summary["stats"] = {
        "companies_scanned": summary["companies_scanned"],
        "candidate_count": len(summary["candidates"]),
        "created_count": len(summary["created"]),
        "skipped_existing_count": len(summary["skipped_existing"]),
        "skipped_count": len(summary["skipped"]),
        "error_count": len(summary["errors"]),
        "priority_refreshed_count": len(summary["priority_refreshed"]),
        "priority_refresh_candidate_count": len(summary["priority_refresh_candidates"]),
    }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Daily GST/PST/T2 Engagement preparation"
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD; defaults to today")
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        today = date.fromisoformat(args.date) if args.date else datetime.now(TIMEZONE).date()
    except ValueError:
        logger.error("Invalid --date value: %s", args.date)
        return 2

    try:
        summary = scan_tax_engagements(today, apply=args.apply)
    except Exception as exc:
        logger.exception("Tax Engagement prepare failed")
        summary = {
            "run_date": today.isoformat(),
            "dry_run": not args.apply,
            "fatal_error": str(exc),
        }

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    if args.output:
        output_path = Path(args.output)
    else:
        timestamp = datetime.now(TIMEZONE).strftime("%Y%m%d_%H%M%S")
        mode = "apply" if args.apply else "dryrun"
        output_path = LOG_DIR / f"tax_engagement_prepare_{mode}_{timestamp}.json"
    output_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Summary written to %s", output_path)

    if summary.get("fatal_error"):
        return 2
    if summary.get("stats", {}).get("error_count", 0):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

