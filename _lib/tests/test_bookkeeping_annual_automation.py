"""Tests for Bookkeeping catch-up and Annual Return rollover automation."""

from datetime import date
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import bookkeeping_annual_automation as automation


def test_next_bookkeeping_period_advances_one_month_until_gst_cursor():
    assert automation.next_bookkeeping_period("2026-03-31", "2026-06-30") == (
        date(2026, 4, 1),
        date(2026, 4, 30),
    )
    assert automation.next_bookkeeping_period("2026-06-30", "2026-06-30") is None


def test_next_bookkeeping_period_rejects_cursor_after_gst():
    with pytest.raises(ValueError, match="after GST"):
        automation.next_bookkeeping_period("2026-07-31", "2026-06-30")


def test_completed_bookkeeping_period_must_be_sequential():
    assert automation.validate_completed_bookkeeping_period(
        "2026-03-31", "2026-04-30"
    ) == date(2026, 4, 30)
    with pytest.raises(ValueError, match="expected 2026-04-30"):
        automation.validate_completed_bookkeeping_period(
            "2026-03-31", "2026-05-31"
        )


def test_build_next_bookkeeping_uses_company_gst_due_date():
    fields, review = automation.build_next_bookkeeping_fields(
        {
            "Legal Name": "Example Co",
            "Bookkeeping Filed Until": "2026-03-31",
            "GST Filed Until": "2026-06-30",
            "GST Due Next Period": "2026-07-31",
        },
    )
    assert review is None
    assert fields["Period End"] == "2026-04-30"
    assert fields["Due Date"] == "2026-07-31"
    assert fields["Bookkeeping Status"] == "stage | not started"


def test_build_next_bookkeeping_marks_missing_gst_due_for_review():
    fields, review = automation.build_next_bookkeeping_fields(
        {
            "Legal Name": "Example Co",
            "Bookkeeping Filed Until": "2026-03-31",
            "GST Filed Until": "2026-06-30",
        },
    )
    assert "GST Due Next Period is blank or invalid" in review
    assert fields["Needs Review"] is True
    assert fields["Priority"] == "P0 Needs Review"
    assert "Due Date" not in fields

def test_bookkeeping_completion_updates_cursor_and_creates_next(monkeypatch):
    calls = {}
    company = {
        "Legal Name": "Example Co",
        "Bookkeeping Filed Until": "2026-03-31",
        "GST Filed Until": "2026-06-30",
        "GST Due Next Period": "2026-07-31",
    }
    monkeypatch.setattr(
        automation.data_access, "list_companies_by_legal_name", lambda name: [company]
    )
    monkeypatch.setattr(
        automation.data_access, "update_company_by_legal_name",
        lambda name, fields: calls.setdefault("update", (name, fields)),
    )
    monkeypatch.setattr(
        automation.data_access, "recurring_engagement_exists", lambda *args: False
    )
    monkeypatch.setattr(
        automation.data_access, "create_recurring_engagement",
        lambda fields: calls.setdefault("create", fields),
    )
    monkeypatch.setattr(
        automation.data_access, "create_event", lambda **kwargs: calls.setdefault("event", kwargs)
    )

    result = automation.complete_bookkeeping_engagement(
        {
            "Id": 10,
            "Type": "Bookkeeping",
            "Company": "Example Co",
            "Period End": "2026-04-30",
            "Bookkeeping Status": "stage | completed",
        },
        {"Bookkeeping Status": "stage | review"},
    )

    assert result["status"] == "updated"
    assert calls["update"] == (
        "Example Co", {"Bookkeeping Filed Until": "2026-04-30"}
    )
    assert calls["create"]["Period End"] == "2026-05-31"
    assert calls["create"]["Due Date"] == "2026-07-31"


def test_annual_return_rollover_adds_one_year():
    fields = automation.build_next_annual_return_fields({
        "Company": "Example Co",
        "Period Start": "2026-06-01",
        "Period End": "2026-06-30",
        "Due Date": "2026-06-30",
    })
    assert fields["Period Start"] == "2027-06-01"
    assert fields["Period End"] == "2027-06-30"
    assert fields["Due Date"] == "2027-06-30"
    assert fields["Annual Return Status"] == "stage | not started"


def test_annual_return_completion_skips_existing_next_year(monkeypatch):
    monkeypatch.setattr(
        automation.data_access, "recurring_engagement_exists", lambda *args: True
    )
    result = automation.complete_annual_return_engagement(
        {
            "Type": "Annual Return",
            "Company": "Example Co",
            "Period Start": "2026-06-01",
            "Period End": "2026-06-30",
            "Due Date": "2026-06-30",
            "Annual Return Status": "stage | completed",
        },
        {"Annual Return Status": "stage | filed"},
    )
    assert result == {"status": "skipped", "reason": "next_engagement_exists"}



def test_next_bookkeeping_period_rejects_non_month_end_cursor():
    with pytest.raises(ValueError, match="calendar month end"):
        automation.next_bookkeeping_period("2024-12-23", "2026-06-30")
