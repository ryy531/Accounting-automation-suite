"""Tests for the combined Payroll and Tax internal report."""

from pathlib import Path
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import payroll_prepare
from server.payroll_prepare import _build_email_body, _build_email_subject


def _summary():
    return {
        "run_date": "2026-06-18",
        "dry_run": False,
        "started_at": "2026-06-18T06:00:00-06:00",
        "finished_at": "2026-06-18T06:01:00-06:00",
        "stats": {
            "companies_scanned": 53,
            "payroll_clients": 53,
            "schedules_scanned": 54,
            "would_create_count": 2,
            "actually_created_count": 2,
            "would_skip_existing_count": 4,
            "skipped_no_frequency_count": 0,
            "skipped_schedule2_no_frequency_count": 0,
            "skipped_biweekly_no_anchor_count": 0,
            "skipped_unknown_frequency_count": 0,
            "duplicate_periods_between_schedules_count": 0,
            "errors_count": 0,
        },
        "actually_created": [],
        "tax_engagements": {
            "stats": {
                "companies_scanned": 166,
                "candidate_count": 2,
                "created_count": 2,
                "skipped_existing_count": 10,
                "skipped_count": 20,
                "error_count": 0,
            },
            "candidates": [
                {"type": "GST", "due_date": "2026-06-30"},
                {"type": "T2", "due_date": "2026-05-31"},
            ],
            "created": [
                {
                    "type": "GST",
                    "company": "Example GST Ltd.",
                    "period_end": "2026-06-30",
                    "due_date": "2026-07-31",
                    "priority": "P4 OK",
                },
                {
                    "type": "T2",
                    "company": "Example T2 Ltd.",
                    "period_end": "2026-07-31",
                    "due_date": "2026-07-31",
                    "priority": "P3 Due Soon",
                },
            ],
            "skipped_existing": [
                {"type": "GST", "due_date": "2026-05-31"},
                {"type": "PST", "due_date": "2026-06-30"},
                {"type": "T2", "due_date": "2026-07-31"},
            ],
            "skipped": [
                {"type": "GST"},
                {"type": "PST"},
            ],
            "errors": [],
        },
    }


def test_combined_subject_names_payroll_and_tax():
    subject = _build_email_subject(_summary())
    assert subject == "[ExampleCo Daily] Jun 18: 2 Payroll + 2 Tax created"


def test_tax_error_marks_combined_subject_action_needed():
    summary = _summary()
    summary["tax_engagements"]["stats"]["error_count"] = 1
    subject = _build_email_subject(summary)
    assert subject.startswith("[ACTION NEEDED]")


def test_combined_body_contains_each_tax_type_and_created_details():
    body = _build_email_body(_summary(), Path("combined.json"))
    assert "ExampleCo Daily Business Summary" in body
    assert "GST: candidates=1; created=1; existing=1; skipped=1; errors=0" in body
    assert "PST: candidates=0; created=0; existing=1; skipped=1; errors=0" in body
    assert "T2: candidates=1; created=1; existing=1; skipped=0; errors=0" in body
    assert "GST: Example GST Ltd." in body
    assert "T2: Example T2 Ltd." in body
    assert "GST: previous month=1; current month=1; next month=0" in body
    assert "PST: previous month=0; current month=1; next month=0" in body
    assert "T2: past due=1; current month=0; next month=1" in body


def test_send_report_uses_both_recipients_and_archive_folder(monkeypatch):
    calls = {}

    class FakeClient:
        def connect_imap(self):
            calls["imap_connected"] = True

        def send_html(self, **kwargs):
            calls["send"] = kwargs
            return "<daily-report@example-firm.com>"

        def move_inbox_message(self, message_id, folder):
            calls["move"] = (message_id, folder)
            return True

        def close_imap(self):
            calls["imap_closed"] = True

    monkeypatch.setattr(
        payroll_prepare,
        "_email_credentials_from_settings",
        lambda: {"sender_email": "infosk@example-firm.com"},
    )
    monkeypatch.setattr(
        payroll_prepare.EmailClient,
        "from_credentials",
        lambda credentials: FakeClient(),
    )
    monkeypatch.setattr(
        payroll_prepare.settings,
        "PAYROLL_REPORT_EMAIL_TO",
        "Riley@example.com",
    )
    monkeypatch.setattr(
        payroll_prepare.settings,
        "DAILY_REPORT_ADDITIONAL_EMAIL_TO",
        "infosk@example-firm.com",
    )
    monkeypatch.setattr(
        payroll_prepare.settings,
        "DAILY_REPORT_ARCHIVE_FOLDER",
        "ExampleCo Daily Reports",
    )

    assert payroll_prepare._send_email_report(
        _summary(), Path("combined.json")
    )
    assert calls["send"]["to"] == [
        "Riley@example.com",
        "infosk@example-firm.com",
    ]
    assert "archive_folder" not in calls["send"]
    assert calls["move"] == (
        "<daily-report@example-firm.com>",
        "ExampleCo Daily Reports",
    )
    assert calls["imap_connected"]
    assert calls["imap_closed"]
