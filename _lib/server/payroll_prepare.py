"""
server/payroll_prepare.py -- Daily Payroll preparation job.

Scans NocoDB for upcoming payroll periods and prepares engagements.
Default: dry-run mode (no writes). Use --apply to create engagements.

Triggered: Daily manual run or future cron job.
"""

import argparse
from html import escape
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config.settings as settings
import data_access
from connectors.email_client import EmailClient
from server.payroll_periods import (
    calculate_period_start,
    upcoming_due_periods,
    FREQ_MONTHLY, FREQ_SEMI_MONTHLY, FREQ_BI_WEEKLY,
    FREQ_QUARTERLY, FREQ_YEARLY,
)
from server.tax_engagement_prepare import scan_tax_engagements
from utils.retry import retry

# ReminderAutomation is a top-level package (sibling of _lib), not under
# the sys.path entry above -- add the Scripts root too (same pattern as bot_routes.py).
_SCRIPTS_ROOT = Path(__file__).resolve().parents[2]
if str(_SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_ROOT))

from ReminderAutomation.interfaces import whatsapp as rem_whatsapp  # noqa: E402

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

_LOG_DIR = Path(__file__).resolve().parents[1] / "logs"
try:
    _TIMEZONE = ZoneInfo(settings.PAYROLL_TIMEZONE)
except ZoneInfoNotFoundError:
    _TIMEZONE = timezone(timedelta(hours=-6), name="America/Regina")


# ── Helper Functions ───────────────────────────────────────────────────────────

def _get_today() -> date:
    """Get today's date in payroll timezone."""
    return datetime.now(_TIMEZONE).date()


def _resolve_biweekly_anchor(company_name: str, period_end_rule: str | None = None) -> date | None:
    """
    Get the anchor date for Bi-weekly frequency.

    Returns:
        Most recent payroll period end from NocoDB, or None if no history.
    """
    try:
        last_period_end = data_access.get_last_payroll_period_end(company_name)
        if last_period_end is not None:
            return last_period_end
    except Exception as e:
        logger.error(f"Failed to get last period for {company_name}: {e}")

    if period_end_rule:
        try:
            return date.fromisoformat(str(period_end_rule).strip())
        except ValueError:
            logger.warning(
                "[%s] invalid bi-weekly anchor fallback: %s",
                company_name,
                period_end_rule,
            )

    return None


# ── Main Logic ────────────────────────────────────────────────────────────────

def _email_credentials_from_settings() -> dict | None:
    """Build EmailClient credentials from settings, or return None if incomplete."""
    auth_mode = getattr(settings, "EMAIL_AUTH_MODE", "password")
    required = {"EMAIL_ADDRESS": settings.EMAIL_ADDRESS}
    if auth_mode == "gmail_api":
        required.update({
            "GMAIL_OAUTH_CLIENT_SECRET_PATH": (
                settings.GMAIL_OAUTH_CLIENT_SECRET_PATH
                if Path(settings.GMAIL_OAUTH_CLIENT_SECRET_PATH).is_file()
                else ""
            ),
            "GMAIL_OAUTH_REFRESH_TOKEN_PATH": (
                settings.GMAIL_OAUTH_REFRESH_TOKEN_PATH
                if Path(settings.GMAIL_OAUTH_REFRESH_TOKEN_PATH).is_file()
                else ""
            ),
        })
    else:
        required.update({
            "EMAIL_PASSWORD": settings.EMAIL_PASSWORD,
            "EMAIL_SMTP_SERVER": settings.EMAIL_SMTP_SERVER,
            "EMAIL_SMTP_PORT": settings.EMAIL_SMTP_PORT,
        })
    missing = [name for name, value in required.items() if not value]

    if missing:
        logger.warning(
            "Payroll email report skipped; missing email config: %s",
            ", ".join(missing),
        )
        return None

    credentials = {
        "auth_mode": auth_mode,
        "sender_name": "ExampleCo Daily Automation",
        "sender_email": settings.EMAIL_ADDRESS,
    }
    if auth_mode == "gmail_api":
        credentials.update({
            "oauth_client_secret_path": settings.GMAIL_OAUTH_CLIENT_SECRET_PATH,
            "oauth_refresh_token_path": settings.GMAIL_OAUTH_REFRESH_TOKEN_PATH,
        })
    else:
        credentials.update({
            "sender_password": settings.EMAIL_PASSWORD,
            "smtp_host": settings.EMAIL_SMTP_SERVER,
            "smtp_port": settings.EMAIL_SMTP_PORT,
            "imap_host": settings.EMAIL_IMAP_SERVER,
            "imap_port": settings.EMAIL_IMAP_PORT,
        })
    return credentials


def _build_email_subject(summary: dict) -> str:
    """Build the combined Payroll and Tax report email subject."""
    tax_summary = summary.get("tax_engagements") or {}
    payroll_created = summary["stats"]["actually_created_count"]
    tax_created = tax_summary.get("stats", {}).get("created_count", 0)
    report_date = date.fromisoformat(summary["run_date"]).strftime("%b %d")
    subject = (
        f"[ExampleCo Daily] {report_date}: {payroll_created} Payroll + "
        f"{tax_created} Tax created"
    )
    tax_errors = tax_summary.get("stats", {}).get("error_count", 0)
    if (
        summary.get("fatal_error")
        or summary["stats"]["errors_count"] > 0
        or tax_summary.get("fatal_error")
        or tax_errors > 0
    ):
        subject = f"[ACTION NEEDED] {subject}"
    return subject


def _build_email_html(summary: dict, output_path: Path) -> str:
    """Build an Outlook-friendly accountant dashboard email."""
    stats = summary["stats"]
    tax_summary = summary.get("tax_engagements") or {}
    tax_stats = tax_summary.get("stats", {})
    tax_counts = _tax_counts_by_type(tax_summary)
    tax_months = _tax_month_counts(tax_summary, summary["run_date"])
    run_date = date.fromisoformat(summary["run_date"])
    current_start = run_date.replace(day=1)
    previous_start = (current_start - timedelta(days=1)).replace(day=1)
    next_start = (current_start + timedelta(days=32)).replace(day=1)
    total_errors = stats["errors_count"] + tax_stats.get("error_count", 0)

    def safe(value) -> str:
        return escape(str(value or ""))

    def metric(label: str, value: int, color: str) -> str:
        return (
            '<td style="width:20%;padding:12px 8px;border:1px solid #d9dee5;'
            'background:#ffffff;text-align:center;vertical-align:top">'
            f'<div style="font-size:24px;font-weight:700;color:{color}">'
            f'{value}</div><div style="font-size:12px;color:#52606d;'
            f'padding-top:4px">{safe(label)}</div></td>'
        )

    payroll_rows = []
    for record in (summary.get("actually_created") or [])[:20]:
        payroll_rows.append(
            '<tr>'
            f'<td style="padding:9px;border-bottom:1px solid #e6e9ed">{safe(record.get("company"))}</td>'
            f'<td style="padding:9px;border-bottom:1px solid #e6e9ed">{safe(record.get("period_start"))} to {safe(record.get("period_end"))}</td>'
            f'<td style="padding:9px;border-bottom:1px solid #e6e9ed">{safe(record.get("frequency"))}</td>'
            f'<td style="padding:9px;border-bottom:1px solid #e6e9ed">{safe(record.get("status"))}</td>'
            '</tr>'
        )
    if not payroll_rows:
        payroll_rows.append(
            '<tr><td colspan="4" style="padding:14px;color:#52606d">'
            'No Payroll engagements were created today.</td></tr>'
        )

    tax_rows = []
    for record in (tax_summary.get("created") or [])[:20]:
        tax_rows.append(
            '<tr>'
            f'<td style="padding:9px;border-bottom:1px solid #e6e9ed;font-weight:700">{safe(record.get("type"))}</td>'
            f'<td style="padding:9px;border-bottom:1px solid #e6e9ed">{safe(record.get("company"))}</td>'
            f'<td style="padding:9px;border-bottom:1px solid #e6e9ed">{safe(record.get("period_end"))}</td>'
            f'<td style="padding:9px;border-bottom:1px solid #e6e9ed">{safe(record.get("due_date"))}</td>'
            f'<td style="padding:9px;border-bottom:1px solid #e6e9ed">{safe(record.get("priority"))}</td>'
            '</tr>'
        )
    if not tax_rows:
        tax_rows.append(
            '<tr><td colspan="5" style="padding:14px;color:#52606d">'
            'No GST, PST, or T2 engagements were created today.</td></tr>'
        )

    exception_items = []
    exception_counts = (
        ("Payroll records with errors", stats["errors_count"]),
        ("Tax records with errors", tax_stats.get("error_count", 0)),
        ("Payroll clients missing frequency", stats["skipped_no_frequency_count"]),
        (
            "Schedule 2 clients missing frequency",
            stats["skipped_schedule2_no_frequency_count"],
        ),
        (
            "Bi-weekly clients missing an anchor date",
            stats["skipped_biweekly_no_anchor_count"],
        ),
    )
    for label, count in exception_counts:
        if count:
            exception_items.append(f"<li>{safe(label)}: <strong>{count}</strong></li>")
    exceptions_html = (
        '<ul style="margin:8px 0 0 20px;padding:0;color:#7a271a">'
        + "".join(exception_items)
        + "</ul>"
        if exception_items
        else '<div style="color:#2f6b3b">No scan errors or configuration exceptions.</div>'
    )

    mode_label = "DRY RUN - no records written" if summary["dry_run"] else "LIVE RUN"
    month_table = "".join((
        '<tr><td style="padding:9px;border-bottom:1px solid #e6e9ed;font-weight:700">GST</td>'
        f'<td style="padding:9px;border-bottom:1px solid #e6e9ed">{previous_start:%b}: {tax_months["GST"]["previous"]}</td>'
        f'<td style="padding:9px;border-bottom:1px solid #e6e9ed">{current_start:%b}: {tax_months["GST"]["current"]}</td>'
        f'<td style="padding:9px;border-bottom:1px solid #e6e9ed">{next_start:%b}: {tax_months["GST"]["next"]}</td></tr>',
        '<tr><td style="padding:9px;border-bottom:1px solid #e6e9ed;font-weight:700">PST</td>'
        f'<td style="padding:9px;border-bottom:1px solid #e6e9ed">{previous_start:%b}: {tax_months["PST"]["previous"]}</td>'
        f'<td style="padding:9px;border-bottom:1px solid #e6e9ed">{current_start:%b}: {tax_months["PST"]["current"]}</td>'
        f'<td style="padding:9px;border-bottom:1px solid #e6e9ed">{next_start:%b}: {tax_months["PST"]["next"]}</td></tr>',
        '<tr><td style="padding:9px;font-weight:700">T2</td>'
        f'<td style="padding:9px">Past due: {tax_months["T2"]["past_due"]}</td>'
        f'<td style="padding:9px">{current_start:%b}: {tax_months["T2"]["current"]}</td>'
        f'<td style="padding:9px">{next_start:%b}: {tax_months["T2"]["next"]}</td></tr>',
    ))

    return f'''<!doctype html>
<html><body style="margin:0;background:#f3f5f7;font-family:Arial,sans-serif;color:#202a33">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f3f5f7"><tr><td align="center" style="padding:24px 10px">
<table role="presentation" width="720" cellspacing="0" cellpadding="0" style="width:100%;max-width:720px;background:#ffffff;border:1px solid #d9dee5">
<tr><td style="padding:24px 28px;background:#173f35;color:#ffffff">
  <div style="font-size:12px;text-transform:uppercase;color:#c7ddd5">ExampleCo daily operations</div>
  <div style="font-size:26px;font-weight:700;padding-top:5px">Accounting Daily Report</div>
  <div style="font-size:14px;padding-top:7px;color:#e4efeb">{run_date:%B %d, %Y} &nbsp;|&nbsp; <span style="color:#ffffff;font-weight:700">{mode_label}</span></div>
</td></tr>
<tr><td style="padding:24px 28px 10px">
  <div style="font-size:18px;font-weight:700;margin-bottom:12px">Today at a glance</div>
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr>
    {metric("Payroll created", stats["actually_created_count"], "#1f5f4a")}
    {metric("GST created", tax_counts["GST"]["created"], "#245a8d")}
    {metric("PST created", tax_counts["PST"]["created"], "#6d4c8d")}
    {metric("T2 created", tax_counts["T2"]["created"], "#8a5a00")}
    {metric("Errors", total_errors, "#a33a2b" if total_errors else "#2f6b3b")}
  </tr></table>
</td></tr>
<tr><td style="padding:18px 28px 6px"><div style="font-size:18px;font-weight:700">Payroll engagements created today</div></td></tr>
<tr><td style="padding:0 28px 18px"><table width="100%" cellspacing="0" cellpadding="0" style="border:1px solid #d9dee5;font-size:13px">
<tr style="background:#eef2f4;text-align:left"><th style="padding:9px">Company</th><th style="padding:9px">Pay period</th><th style="padding:9px">Frequency</th><th style="padding:9px">Status</th></tr>
{''.join(payroll_rows)}</table></td></tr>
<tr><td style="padding:12px 28px 6px"><div style="font-size:18px;font-weight:700">Tax due-window monitor</div><div style="font-size:12px;color:#52606d;padding-top:4px">Counts include new candidates and engagements already present in the workbench.</div></td></tr>
<tr><td style="padding:0 28px 18px"><table width="100%" cellspacing="0" cellpadding="0" style="border:1px solid #d9dee5;font-size:13px">
<tr style="background:#eef2f4;text-align:left"><th style="padding:9px">Type</th><th style="padding:9px">Previous / overdue</th><th style="padding:9px">Current month</th><th style="padding:9px">Next month</th></tr>
{month_table}</table></td></tr>
<tr><td style="padding:12px 28px 6px"><div style="font-size:18px;font-weight:700">Tax engagements created today</div></td></tr>
<tr><td style="padding:0 28px 18px"><table width="100%" cellspacing="0" cellpadding="0" style="border:1px solid #d9dee5;font-size:13px">
<tr style="background:#eef2f4;text-align:left"><th style="padding:9px">Type</th><th style="padding:9px">Company</th><th style="padding:9px">Period end</th><th style="padding:9px">Due date</th><th style="padding:9px">Priority</th></tr>
{''.join(tax_rows)}</table></td></tr>
<tr><td style="padding:16px 28px;background:#f8faf9;border-top:1px solid #d9dee5"><div style="font-size:16px;font-weight:700">Follow-up check</div><div style="padding-top:5px;font-size:13px">{exceptions_html}</div></td></tr>
<tr><td style="padding:16px 28px;color:#64717d;font-size:11px;line-height:1.6">
Scanned: {stats["companies_scanned"]} companies / {stats["schedules_scanned"]} payroll schedules / {tax_stats.get("companies_scanned", 0)} tax companies.<br>
Existing Payroll engagements skipped: {stats["would_skip_existing_count"]}. Existing Tax engagements skipped: {tax_stats.get("skipped_existing_count", 0)}.<br>
Technical report: {safe(output_path)}
</td></tr></table></td></tr></table></body></html>'''


def _tax_counts_by_type(tax_summary: dict) -> dict:
    """Build compact GST/PST/T2 counts for the combined email."""
    counts = {
        tax_type: {
            "candidates": 0,
            "created": 0,
            "existing": 0,
            "skipped": 0,
            "errors": 0,
        }
        for tax_type in ("GST", "PST", "T2")
    }
    for key, target in (
        ("candidates", "candidates"),
        ("created", "created"),
        ("skipped_existing", "existing"),
        ("skipped", "skipped"),
        ("errors", "errors"),
    ):
        for record in tax_summary.get(key) or []:
            tax_type = record.get("type")
            if tax_type in counts:
                counts[tax_type][target] += 1
    return counts


def _tax_month_counts(tax_summary: dict, run_date: str) -> dict:
    """Group in-window Tax items into accountant-friendly month buckets."""
    today = date.fromisoformat(run_date)
    current_start = today.replace(day=1)
    previous_start = (current_start - timedelta(days=1)).replace(day=1)
    next_start = (current_start + timedelta(days=32)).replace(day=1)
    after_next = (next_start + timedelta(days=32)).replace(day=1)

    counts = {
        "GST": {"previous": 0, "current": 0, "next": 0},
        "PST": {"previous": 0, "current": 0, "next": 0},
        "T2": {"past_due": 0, "current": 0, "next": 0},
    }
    records = (tax_summary.get("skipped_existing") or []) + (
        tax_summary.get("candidates") or []
    )
    for record in records:
        tax_type = record.get("type")
        if tax_type not in counts:
            continue
        try:
            due_date = date.fromisoformat(str(record.get("due_date") or "")[:10])
        except ValueError:
            continue

        if tax_type == "T2":
            if due_date < current_start:
                counts[tax_type]["past_due"] += 1
            elif due_date < next_start:
                counts[tax_type]["current"] += 1
            elif due_date < after_next:
                counts[tax_type]["next"] += 1
        elif previous_start <= due_date < current_start:
            counts[tax_type]["previous"] += 1
        elif current_start <= due_date < next_start:
            counts[tax_type]["current"] += 1
        elif next_start <= due_date < after_next:
            counts[tax_type]["next"] += 1
    return counts


def _build_email_body(summary: dict, output_path: Path) -> str:
    """Build the combined Payroll and Tax plain-text report body."""
    stats = summary["stats"]
    mode = "dry-run" if summary["dry_run"] else "apply"
    lines = [
        "ExampleCo Daily Business Summary",
        "",
        f"Mode: {mode}",
        f"Run date: {summary['run_date']}",
        f"Started: {summary['started_at']}",
        f"Finished: {summary.get('finished_at', '')}",
        "",
        "Today's overview",
        f"Payroll created: {stats['actually_created_count']}",
        f"Payroll upcoming/existing: {stats['would_skip_existing_count']}",
        f"Tax Engagements created: {(summary.get('tax_engagements') or {}).get('stats', {}).get('created_count', 0)}",
        f"Total errors: {stats['errors_count'] + (summary.get('tax_engagements') or {}).get('stats', {}).get('error_count', 0)}",
        "",
        "Payroll",
        f"Companies scanned: {stats['companies_scanned']}",
        f"Payroll clients: {stats['payroll_clients']}",
        f"Schedules scanned: {stats['schedules_scanned']}",
        f"Would create: {stats['would_create_count']}",
        f"Actually created: {stats['actually_created_count']}",
        f"Skipped existing: {stats['would_skip_existing_count']}",
        f"Skipped no frequency: {stats['skipped_no_frequency_count']}",
        f"Skipped schedule 2 no frequency: {stats['skipped_schedule2_no_frequency_count']}",
        f"Skipped biweekly no anchor: {stats['skipped_biweekly_no_anchor_count']}",
        f"Skipped unknown frequency: {stats['skipped_unknown_frequency_count']}",
        f"Duplicate periods between schedules: {stats['duplicate_periods_between_schedules_count']}",
        f"Errors: {stats['errors_count']}",
    ]

    if summary.get("fatal_error"):
        lines.extend([f"Payroll fatal error: {summary['fatal_error']}"])

    tax_summary = summary.get("tax_engagements") or {}
    tax_stats = tax_summary.get("stats", {})
    tax_counts = _tax_counts_by_type(tax_summary)
    tax_months = _tax_month_counts(tax_summary, summary["run_date"])
    lines.extend([
        "",
        "GST / PST / T2",
        f"Companies scanned: {tax_stats.get('companies_scanned', 0)}",
    ])
    for tax_type in ("GST", "PST", "T2"):
        item = tax_counts[tax_type]
        lines.append(
            f"{tax_type}: candidates={item['candidates']}; "
            f"created={item['created']}; existing={item['existing']}; "
            f"skipped={item['skipped']}; errors={item['errors']}"
        )
    lines.extend([
        "",
        "Tax items by due month",
        (
            "GST: previous month={previous}; current month={current}; "
            "next month={next}"
        ).format(**tax_months["GST"]),
        (
            "PST: previous month={previous}; current month={current}; "
            "next month={next}"
        ).format(**tax_months["PST"]),
        (
            "T2: past due={past_due}; current month={current}; "
            "next month={next}"
        ).format(**tax_months["T2"]),
    ])
    lines.extend([
        f"Tax total candidates: {tax_stats.get('candidate_count', 0)}",
        f"Tax actually created: {tax_stats.get('created_count', 0)}",
        f"Tax skipped existing: {tax_stats.get('skipped_existing_count', 0)}",
        f"Tax errors: {tax_stats.get('error_count', 0)}",
    ])
    if tax_summary.get("fatal_error"):
        lines.append(f"Tax fatal error: {tax_summary['fatal_error']}")

    lines.extend(["", f"Summary JSON path: {output_path}"])

    created = summary.get("actually_created") or []
    if created:
        lines.extend(["", "Payroll records created (max 20):"])
        for record in created[:20]:
            lines.append(
                "- Company: {company}; Slot: {slot}; Period end: {period_end}; Frequency: {frequency}".format(
                    company=record["company"],
                    slot=record["slot"],
                    period_end=record["period_end"],
                    frequency=record["frequency"],
                )
            )
        if len(created) > 20:
            lines.append(f"- ... and {len(created) - 20} more")

    tax_created = tax_summary.get("created") or []
    if tax_created:
        lines.extend(["", "Tax Engagements created (max 20):"])
        for record in tax_created[:20]:
            lines.append(
                f"- {record['type']}: {record['company']}; "
                f"Period end: {record['period_end']}; "
                f"Due: {record['due_date']}; Priority: {record['priority']}"
            )
        if len(tax_created) > 20:
            lines.append(f"- ... and {len(tax_created) - 20} more")

    return "\n".join(lines)


def _send_email_report(summary: dict, output_path: Path) -> bool:
    """Send the combined report and file the sender mailbox's inbox copy."""
    recipients = [
        value for value in (
            settings.PAYROLL_REPORT_EMAIL_TO,
            settings.DAILY_REPORT_ADDITIONAL_EMAIL_TO,
        )
        if value
    ]
    recipients = list(dict.fromkeys(recipients))
    if not recipients:
        logger.warning(
            "Payroll email report skipped; PAYROLL_REPORT_EMAIL_TO is not set."
        )
        return False

    credentials = _email_credentials_from_settings()
    if credentials is None:
        return False

    client = EmailClient.from_credentials(credentials)
    message_id = client.send_html(
        to=recipients,
        subject=_build_email_subject(summary),
        html_body=_build_email_html(summary, output_path),
        text_body=_build_email_body(summary, output_path),
    )

    sender_email = credentials.get("sender_email", "").strip().lower()
    archive_recipient = (
        settings.DAILY_REPORT_ADDITIONAL_EMAIL_TO or ""
    ).strip().lower()
    should_archive = (
        bool(settings.DAILY_REPORT_ARCHIVE_FOLDER)
        and archive_recipient == sender_email
        and archive_recipient in {item.lower() for item in recipients}
    )
    if should_archive:
        try:
            client.connect_imap()
            archived = client.move_inbox_message(
                message_id,
                settings.DAILY_REPORT_ARCHIVE_FOLDER,
            )
            if not archived:
                logger.warning(
                    "Daily report sent but its inbox copy was not moved to: %s",
                    settings.DAILY_REPORT_ARCHIVE_FOLDER,
                )
        except Exception as exc:
            logger.warning("Daily report IMAP filing failed: %s", exc)
        finally:
            client.close_imap()

    logger.info("Daily report sent to %d configured recipient(s)", len(recipients))
    return True


def scan_payroll_prepares(today: date, apply: bool = False) -> dict:
    """
    Scan for upcoming payroll periods and optionally create engagements.

    Args:
        today: Reference date for calculations
        apply: If True, create engagements; if False, dry-run only

    Returns:
        Summary dict with statistics and sample records.
    """
    summary = {
        "run_date": today.isoformat(),
        "dry_run": not apply,
        "started_at": datetime.now(_TIMEZONE).isoformat(),
        "companies_scanned": 0,
        "payroll_clients": 0,
        "schedules_scanned": 0,
        "would_create": [],
        "would_skip_existing": [],
        "skipped_no_frequency": [],
        "skipped_schedule2_no_frequency": [],
        "skipped_biweekly_no_anchor": [],
        "skipped_unknown_frequency": [],
        "duplicate_periods_between_schedules": [],
        "actually_created": [] if apply else None,
        "errors": [],
    }

    try:
        # Load all payroll companies (retried: absorb transient NocoDB connection blips)
        companies = retry(data_access.list_payroll_companies)
        summary["companies_scanned"] = len(companies)

        logger.info(f"Scanning {len(companies)} companies for payroll preparation")

        for company in companies:
            company_name = company.get(settings.NOCODB_COMPANY_FIELD_LEGAL_NAME)
            if not company_name:
                logger.warning("Company missing Legal Name, skipping")
                summary["skipped_no_frequency"].append({
                    "company": "<missing>",
                    "reason": "No Legal Name"
                })
                continue

            summary["payroll_clients"] += 1

            try:
                schedules, skipped_schedules = data_access.extract_payroll_schedules(
                    company
                )
                has_primary_schedule = any(s["slot"] == 1 for s in schedules)

                if not has_primary_schedule:
                    logger.warning(f"[{company_name}] no Pay Frequency")
                    summary["skipped_no_frequency"].append({
                        "company": company_name,
                        "slot": 1,
                        "reason": "No Pay Frequency"
                    })

                for skipped in skipped_schedules:
                    logger.warning(
                        "[%s] schedule %s skipped: %s",
                        company_name,
                        skipped["slot"],
                        skipped["reason"],
                    )
                    summary["skipped_schedule2_no_frequency"].append({
                        "company": company_name,
                        **skipped,
                    })

                if not schedules:
                    continue

                company_periods_seen = {}

                for schedule in schedules:
                    summary["schedules_scanned"] += 1
                    slot = schedule["slot"]
                    freq = schedule["frequency"]
                    period_end_rule = schedule.get("period_end_rule")

                    if freq not in settings.NOCODB_VALID_FREQUENCIES:
                        logger.warning(
                            f"[{company_name}] schedule {slot} unknown frequency: {freq}"
                        )
                        summary["skipped_unknown_frequency"].append({
                            "company": company_name,
                            "slot": slot,
                            "frequency": freq
                        })
                        continue

                    last_period_end = None
                    if freq == FREQ_BI_WEEKLY:
                        last_period_end = _resolve_biweekly_anchor(
                            company_name,
                            period_end_rule,
                        )
                        if last_period_end is None:
                            logger.warning(
                                "[%s] schedule %s Bi-weekly without anchor, skipping",
                                company_name,
                                slot,
                            )
                            summary["skipped_biweekly_no_anchor"].append({
                                "company": company_name,
                                "slot": slot,
                                "reason": "No previous payroll period or valid anchor"
                            })
                            continue

                    upcoming = upcoming_due_periods(
                        freq, today, last_period_end, lookahead_days=7
                    )

                    if not upcoming:
                        logger.debug(
                            f"[{company_name}] schedule {slot} no upcoming periods"
                        )
                        continue

                    for period_end in upcoming:
                        previous_slot = company_periods_seen.get(period_end)
                        if previous_slot is not None:
                            logger.debug(
                                "[%s] schedule %s duplicates period %s from schedule %s",
                                company_name,
                                slot,
                                period_end,
                                previous_slot,
                            )
                            summary["duplicate_periods_between_schedules"].append({
                                "company": company_name,
                                "period_end": period_end.isoformat(),
                                "slot": slot,
                                "duplicate_of_slot": previous_slot,
                                "frequency": freq,
                            })
                            continue
                        company_periods_seen[period_end] = slot

                        exists = data_access.engagement_exists(company_name, period_end)

                        if exists:
                            logger.debug(
                                f"[{company_name}] engagement exists for {period_end}"
                            )
                            summary["would_skip_existing"].append({
                                "company": company_name,
                                "slot": slot,
                                "period_end": period_end.isoformat(),
                                "frequency": freq,
                            })
                            continue

                        period_start = calculate_period_start(period_end, freq)

                        record = {
                            "company": company_name,
                            "slot": slot,
                            "frequency": freq,
                            "period_start": period_start.isoformat(),
                            "period_end": period_end.isoformat(),
                            "status": settings.NOCODB_ENGAGEMENT_STATUS_WAITING_INFO,
                        }

                        if apply:
                            try:
                                result = data_access.create_payroll_engagement(
                                    company_name, period_start, period_end, freq
                                )
                                summary["actually_created"].append(record)
                                logger.info(
                                    f"[{company_name}] created engagement for {period_end}"
                                )
                            except Exception as e:
                                logger.error(
                                    f"[{company_name}] failed to create engagement: {e}"
                                )
                                summary["errors"].append({
                                    "company": company_name,
                                    "slot": slot,
                                    "period_end": period_end.isoformat(),
                                    "error": str(e),
                                })
                        else:
                            summary["would_create"].append(record)
                            logger.info(
                                f"[{company_name}] would create engagement for {period_end}"
                            )

            except Exception as e:
                logger.error(f"[{company_name}] processing error: {e}", exc_info=True)
                summary["errors"].append({
                    "company": company_name,
                    "error": str(e),
                })

    except Exception as e:
        logger.error(f"Fatal error in scan: {e}", exc_info=True)
        summary["fatal_error"] = str(e)

    summary["finished_at"] = datetime.now(_TIMEZONE).isoformat()

    # Statistics
    summary["stats"] = {
        "companies_scanned": summary["companies_scanned"],
        "payroll_clients": summary["payroll_clients"],
        "schedules_scanned": summary["schedules_scanned"],
        "would_create_count": len(summary["would_create"]),
        "would_skip_existing_count": len(summary["would_skip_existing"]),
        "skipped_no_frequency_count": len(summary["skipped_no_frequency"]),
        "skipped_schedule2_no_frequency_count": len(
            summary["skipped_schedule2_no_frequency"]
        ),
        "skipped_biweekly_no_anchor_count": len(summary["skipped_biweekly_no_anchor"]),
        "skipped_unknown_frequency_count": len(summary["skipped_unknown_frequency"]),
        "duplicate_periods_between_schedules_count": len(
            summary["duplicate_periods_between_schedules"]
        ),
        "actually_created_count": len(summary["actually_created"]) if apply else 0,
        "errors_count": len(summary["errors"]),
    }

    return summary


# ── CLI Entry Point ───────────────────────────────────────────────────────────

def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Daily Payroll Preparation - Scan and prepare upcoming payroll periods"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually create engagements in NocoDB (default: dry-run)"
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Reference date (YYYY-MM-DD, default: today)"
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output JSON file path (default: auto-generated in logs/)"
    )
    parser.add_argument(
        "--email-report",
        action="store_true",
        help="Send internal summary report email after writing the JSON summary"
    )

    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    logger.info("=" * 60)
    logger.info("Payroll Prepare starting")
    logger.info(f"  mode:     {'apply' if args.apply else 'dry-run'}")
    logger.info(f"  timezone: {settings.PAYROLL_TIMEZONE}")

    # Parse date
    if args.date:
        try:
            today = date.fromisoformat(args.date)
        except ValueError:
            logger.error(f"Invalid date format: {args.date}")
            return 1
    else:
        today = _get_today()

    logger.info(f"  date:     {today}")
    logger.info("=" * 60)

    # Run Payroll and Tax scans before writing or emailing the combined report.
    summary = scan_payroll_prepares(today, apply=args.apply)
    try:
        summary["tax_engagements"] = scan_tax_engagements(
            today,
            apply=args.apply,
        )
    except Exception as e:
        logger.exception("Tax Engagement prepare failed")
        summary["tax_engagements"] = {
            "run_date": today.isoformat(),
            "dry_run": not args.apply,
            "fatal_error": str(e),
            "stats": {"error_count": 1},
        }

    # Determine output path
    if args.output:
        output_path = Path(args.output)
    else:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(_TIMEZONE).strftime("%Y%m%d_%H%M%S")
        mode = "apply" if args.apply else "dryrun"
        output_path = _LOG_DIR / f"payroll_prepare_{mode}_{timestamp}.json"

    # Write summary
    try:
        output_path.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(f"Summary written to: {output_path}")
    except OSError as e:
        logger.error(f"Failed to write summary: {e}")
        return 1

    if args.email_report:
        try:
            _send_email_report(summary, output_path)
        except Exception as e:
            logger.warning("Payroll email report failed: %s", e)

    # Fatal errors mean the whole run was skipped (not just a per-company error) --
    # email may go unread, so also alert the staff WhatsApp group directly.
    fatal_payroll = summary.get("fatal_error")
    fatal_tax = summary["tax_engagements"].get("fatal_error")
    if fatal_payroll or fatal_tax:
        alert_lines = [f"Payroll Prepare FAILED ({summary['run_date']})"]
        if fatal_payroll:
            alert_lines.append(f"Payroll: {fatal_payroll}")
        if fatal_tax:
            alert_lines.append(f"Tax engagements: {fatal_tax}")
        try:
            rem_whatsapp.send_to_group("\n".join(alert_lines))
        except Exception as e:
            logger.warning("Payroll WhatsApp alert failed: %s", e)

    # Print summary
    logger.info("=" * 60)
    logger.info("Payroll Prepare summary:")
    logger.info(f"  companies_scanned:            {summary['stats']['companies_scanned']}")
    logger.info(f"  payroll_clients:              {summary['stats']['payroll_clients']}")
    logger.info(f"  schedules_scanned:            {summary['stats']['schedules_scanned']}")
    logger.info(f"  would_create:                 {summary['stats']['would_create_count']}")
    logger.info(f"  would_skip_existing:           {summary['stats']['would_skip_existing_count']}")
    logger.info(f"  skipped_no_frequency:         {summary['stats']['skipped_no_frequency_count']}")
    logger.info(f"  skipped_schedule2_no_frequency: {summary['stats']['skipped_schedule2_no_frequency_count']}")
    logger.info(f"  skipped_biweekly_no_anchor:    {summary['stats']['skipped_biweekly_no_anchor_count']}")
    logger.info(f"  skipped_unknown_frequency:    {summary['stats']['skipped_unknown_frequency_count']}")
    logger.info(f"  duplicate_periods_between_schedules: {summary['stats']['duplicate_periods_between_schedules_count']}")

    if args.apply:
        logger.info(f"  actually_created:              {summary['stats']['actually_created_count']}")

    logger.info(f"  errors:                        {summary['stats']['errors_count']}")
    tax_stats = summary["tax_engagements"].get("stats", {})
    logger.info(f"  tax_candidates:                 {tax_stats.get('candidate_count', 0)}")
    logger.info(f"  tax_created:                    {tax_stats.get('created_count', 0)}")
    logger.info(f"  tax_errors:                     {tax_stats.get('error_count', 0)}")
    logger.info("=" * 60)

    # Show sample would_create records
    if summary["would_create"]:
        logger.info("Sample would_create records (max 20):")
        samples = summary["would_create"][:20]
        for i, record in enumerate(samples, 1):
            logger.info(
                f"  {i}. {record['company']} - {record['period_end']} "
                f"(slot {record['slot']}, {record['frequency']})"
            )
        if len(summary["would_create"]) > 20:
            logger.info(f"  ... and {len(summary['would_create']) - 20} more")
        logger.info("")

    # Exit code
    if summary.get("fatal_error"):
        return 2
    if summary["tax_engagements"].get("fatal_error"):
        return 2
    if summary["stats"]["errors_count"] > 0:
        return 1
    if summary["tax_engagements"].get("stats", {}).get("error_count", 0) > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
