"""Regression test: Payroll engagements have no "Due Date" field in NocoDB --
they carry their due reference in "Payroll Period End" instead. query_engagements
must fall back to that field, or Payroll items are silently dropped from every
due_period-filtered query (this_week/this_month/overdue) and show a blank
due_date placeholder in the daily/weekly NocoDB briefing."""

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import data_access


def _payroll_row(period_end: str) -> dict:
    return {
        "Id": 1,
        "Name": f"Acme Payroll {period_end}",
        "Company": "Acme Inc",
        "Type": "Payroll",
        "Status": "Payroll | Waiting Info",
        "Due Date": None,
        "Payroll Period End": period_end,
        "Priority": None,
        "Assigned To": "Riley",
    }


def _gst_row(due_date: str) -> dict:
    return {
        "Id": 2,
        "Name": "Acme GST 2026-06-30",
        "Company": "Acme Inc",
        "Type": "GST",
        "GST Status": "stage | not started",
        "Due Date": due_date,
        "Payroll Period End": None,
        "Priority": "P1 Overdue",
        "Assigned To": None,
    }


def test_payroll_falls_back_to_period_end_for_due_date(monkeypatch):
    monkeypatch.setattr(data_access, "list_engagements", lambda: [_payroll_row("2026-06-15")])
    results = data_access.query_engagements(types=["Payroll"], exclude_done=False)
    assert len(results) == 1
    assert results[0]["due_date"] == "2026-06-15"


def test_payroll_no_longer_excluded_from_overdue_filter(monkeypatch):
    overdue_period_end = (date.today() - timedelta(days=10)).isoformat()
    monkeypatch.setattr(data_access, "list_engagements", lambda: [_payroll_row(overdue_period_end)])
    results = data_access.query_engagements(due_period="overdue", exclude_done=False)
    assert len(results) == 1


def test_payroll_no_longer_excluded_from_this_week_filter(monkeypatch):
    soon = (date.today() + timedelta(days=2)).isoformat()
    monkeypatch.setattr(data_access, "list_engagements", lambda: [_payroll_row(soon)])
    results = data_access.query_engagements(due_period="this_week", exclude_done=False)
    assert len(results) == 1


def test_gst_due_date_unaffected_by_fallback(monkeypatch):
    monkeypatch.setattr(data_access, "list_engagements", lambda: [_gst_row("2026-07-30")])
    results = data_access.query_engagements(types=["GST"], exclude_done=False)
    assert len(results) == 1
    assert results[0]["due_date"] == "2026-07-30"


def test_engagement_with_neither_date_field_has_blank_due_date(monkeypatch):
    row = _payroll_row(None)
    monkeypatch.setattr(data_access, "list_engagements", lambda: [row])
    results = data_access.query_engagements(exclude_done=False)
    assert results[0]["due_date"] == ""
