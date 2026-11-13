"""Tests for status-driven period advancement helpers."""

from datetime import date
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.period_automation import (
    EVENT_TYPE_MANUAL_ACTION,
    STATUS_COMPLETE,
    STATUS_NOT_STARTED,
    TYPE_GST,
    TYPE_PST,
    TYPE_T2,
    FREQ_MONTHLY,
    FREQ_QUARTERLY,
    FREQ_ANNUAL,
    BUSINESS_STATUS_COMPLETED,
    build_business_status_filed_until_update,
    build_next_engagement_fields,
    build_company_filed_until_update_fields,
    advance_completed_engagement,
    calculate_t2_due,
    calculate_t2_year_end,
    calculate_only_forward_filed_until,
    calculate_next_engagement,
    get_business_status_config,
    is_business_completed_transition,
    is_complete_transition,
    is_exact_business_completed,
    next_period,
    parse_multiselect_status_values,
)


def test_complete_transition_only_when_newly_complete():
    assert is_complete_transition("In Progress", STATUS_COMPLETE)
    assert not is_complete_transition(STATUS_COMPLETE, STATUS_COMPLETE)
    assert not is_complete_transition("In Progress", "Waiting Info")


def test_next_monthly_period():
    start, end = next_period(date(2026, 3, 31), FREQ_MONTHLY)
    assert start == date(2026, 4, 1)
    assert end == date(2026, 4, 30)


def test_next_quarterly_period():
    start, end = next_period(date(2026, 3, 31), FREQ_QUARTERLY)
    assert start == date(2026, 4, 1)
    assert end == date(2026, 6, 30)


def test_next_annual_period():
    start, end = next_period(date(2026, 12, 31), FREQ_ANNUAL)
    assert start == date(2027, 1, 1)
    assert end == date(2027, 12, 31)


def test_gst_complete_builds_next_engagement():
    result = calculate_next_engagement({
        "Type": TYPE_GST,
        "Company": "Example Co",
        "Frequency": FREQ_QUARTERLY,
        "Period End": "2026-03-31",
    })
    assert result.period_start == date(2026, 4, 1)
    assert result.period_end == date(2026, 6, 30)
    assert result.due_date == date(2026, 7, 31)
    assert result.status == STATUS_NOT_STARTED

    fields = build_next_engagement_fields(result)
    assert fields["Name"] == "Example Co GST 2026-06-30"
    assert fields["Due Date"] == "2026-07-31"


def test_pst_complete_uses_end_of_next_month_due():
    result = calculate_next_engagement({
        "Type": TYPE_PST,
        "Company": "Example Co",
        "Frequency": FREQ_MONTHLY,
        "Period End": "2026-01-31",
    })
    assert result.period_start == date(2026, 2, 1)
    assert result.period_end == date(2026, 2, 28)
    assert result.due_date == date(2026, 3, 31)


def test_missing_required_fields_block_advancement():
    with pytest.raises(ValueError, match="Frequency"):
        calculate_next_engagement({
            "Type": TYPE_GST,
            "Company": "Example Co",
            "Period End": "2026-03-31",
        })


@pytest.mark.parametrize(
    "frequency,filed_until,next_end,due",
    [
        (FREQ_MONTHLY, "2026-01-31", "2026-02-28", "2026-03-31"),
        (FREQ_QUARTERLY, "2026-03-31", "2026-06-30", "2026-07-31"),
        (FREQ_ANNUAL, "2025-12-31", "2026-12-31", "2027-03-31"),
    ],
)
def test_company_gst_filed_until_calculates_next_end_and_due(
    frequency, filed_until, next_end, due
):
    result = build_company_filed_until_update_fields({
        "GST Filed Until": filed_until,
        "GST Frequency": frequency,
    })

    assert result.fields["GST Next Period End"] == next_end
    assert result.fields["GST Due Next Period"] == due


@pytest.mark.parametrize(
    "frequency,filed_until,next_end,pst_due",
    [
        (FREQ_MONTHLY, "2026-01-31", "2026-02-28", "2026-03-31"),
        (FREQ_QUARTERLY, "2026-03-31", "2026-06-30", "2026-07-31"),
        (FREQ_ANNUAL, "2025-12-31", "2026-12-31", "2027-01-31"),
    ],
)
def test_company_pst_filed_until_calculates_next_end_and_saskatchewan_due(
    frequency, filed_until, next_end, pst_due
):
    result = build_company_filed_until_update_fields({
        "PST Filed Until": filed_until,
        "PST Frequency": frequency,
    })

    assert result.fields["PST Next Period End"] == next_end
    assert result.fields["PST Due Next Period"] == pst_due


def test_t2_year_end_is_filed_until_plus_12_months():
    assert calculate_t2_year_end(date(2025, 12, 31)) == date(2026, 12, 31)


def test_t2_due_is_year_end_plus_3_months():
    assert calculate_t2_due(date(2026, 12, 31)) == date(2027, 3, 31)
    assert calculate_t2_due(date(2024, 2, 29)) == date(2024, 5, 29)

    result = build_company_filed_until_update_fields({
        "T2 Filed Until": "2025-12-31",
    })

    assert result.fields["T2 Due Next Period"] == "2026-12-31"


def test_company_filed_until_blank_values_are_safely_skipped():
    result = build_company_filed_until_update_fields({
        "GST Frequency": FREQ_MONTHLY,
        "PST Frequency": FREQ_QUARTERLY,
    })

    assert result.fields == {}
    assert "GST Filed Until is blank or invalid" in result.skipped
    assert "PST Filed Until is blank or invalid" in result.skipped
    assert "T2 Filed Until is blank or invalid" in result.skipped
    assert result.review_reasons == []


def test_company_filed_until_unknown_frequency_needs_review():
    result = build_company_filed_until_update_fields({
        "GST Filed Until": "2026-01-31",
        "GST Frequency": "Every So Often",
        "PST Filed Until": "2026-01-31",
        "PST Frequency": "",
    })

    assert "GST Next Period End" not in result.fields
    assert "PST Next Period End" not in result.fields
    assert "GST Frequency is blank or unsupported" in result.review_reasons
    assert "PST Frequency is blank or unsupported" in result.review_reasons


def test_advance_completed_engagement_creates_next(monkeypatch):
    calls = {}

    def fake_exists(company, engagement_type, period_end):
        calls["exists"] = (company, engagement_type, period_end)
        return False

    def fake_create(fields):
        calls["create"] = fields
        return {"Id": 99, **fields}

    def fake_event(**kwargs):
        calls["event"] = kwargs
        return {"Id": 100}

    import server.period_automation as pa
    monkeypatch.setattr(pa.data_access, "recurring_engagement_exists", fake_exists)
    monkeypatch.setattr(pa.data_access, "create_recurring_engagement", fake_create)
    monkeypatch.setattr(pa.data_access, "create_event", fake_event)

    result = advance_completed_engagement(
        {
            "Id": 10,
            "Type": TYPE_GST,
            "Company": "Example Co",
            "Frequency": FREQ_QUARTERLY,
            "Period End": "2026-03-31",
            "Status": STATUS_COMPLETE,
        },
        {"Status": "In Progress"},
    )

    assert result["status"] == "created"
    assert calls["create"]["Period Start"] == "2026-04-01"
    assert calls["create"]["Period End"] == "2026-06-30"
    assert result["company_workbench_updated"] is False
    assert calls["event"]["event_type"] == EVENT_TYPE_MANUAL_ACTION
    assert "Created GST 2026-06-30 after Complete" in calls["event"]["description"]


def test_advance_completed_engagement_skips_existing(monkeypatch):
    import server.period_automation as pa
    monkeypatch.setattr(pa.data_access, "recurring_engagement_exists", lambda *args: True)

    result = advance_completed_engagement(
        {
            "Type": TYPE_GST,
            "Company": "Example Co",
            "Frequency": FREQ_QUARTERLY,
            "Period End": "2026-03-31",
            "Status": STATUS_COMPLETE,
        },
        {"Status": "In Progress"},
    )

    assert result["status"] == "skipped"
    assert result["reason"] == "next_engagement_exists"


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, []),
        ("stage | completed", ["stage | completed"]),
        (
            "stage | completed,blocker | payment missing",
            ["stage | completed", "blocker | payment missing"],
        ),
        (["stage | completed"], ["stage | completed"]),
        ({"title": "stage | completed"}, ["stage | completed"]),
        ({"value": "stage | completed"}, ["stage | completed"]),
        ([{"title": "stage | completed"}], ["stage | completed"]),
        (" stage   |   completed ", ["stage | completed"]),
    ],
)
def test_parse_multiselect_status_values(raw, expected):
    assert parse_multiselect_status_values(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        BUSINESS_STATUS_COMPLETED,
        [BUSINESS_STATUS_COMPLETED],
        {"title": BUSINESS_STATUS_COMPLETED},
    ],
)
def test_exact_business_completed_true_only_for_sole_completed(raw):
    assert is_exact_business_completed(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "stage | filed",
        "stage | completed, blocker | payment missing",
        ["stage | completed", "blocker | payment missing"],
        "blocker | payment missing",
    ],
)
def test_exact_business_completed_rejects_non_final_states(raw):
    assert not is_exact_business_completed(raw)


def test_business_completed_transition_only_when_newly_exact_completed():
    assert is_business_completed_transition(
        "stage | preparing",
        BUSINESS_STATUS_COMPLETED,
    )
    assert not is_business_completed_transition(
        BUSINESS_STATUS_COMPLETED,
        BUSINESS_STATUS_COMPLETED,
    )
    assert not is_business_completed_transition(
        "stage | preparing",
        "stage | completed, blocker | payment missing",
    )


def test_business_status_config_maps_to_company_filed_until_fields():
    assert get_business_status_config("GST Status") == {
        "type": TYPE_GST,
        "company_field": "GST Filed Until",
    }
    assert get_business_status_config("PST Status")["company_field"] == "PST Filed Until"
    assert get_business_status_config("T2 Status")["company_field"] == "T2 Filed Until"
    assert get_business_status_config("Payroll Status") is None


def test_only_forward_filed_until_advances_only_later_periods():
    assert calculate_only_forward_filed_until(
        "2026-03-31",
        date(2026, 4, 30),
    ) == date(2026, 4, 30)
    assert calculate_only_forward_filed_until(
        "2026-05-31",
        date(2026, 4, 30),
    ) is None
    assert calculate_only_forward_filed_until(None, date(2026, 4, 30)) == date(2026, 4, 30)


def test_completed_gst_status_builds_company_filed_until_update():
    result = build_business_status_filed_until_update(
        {
            "Type": TYPE_GST,
            "Company": "Example Co",
            "Period End": "2026-04-30",
            "GST Status": [BUSINESS_STATUS_COMPLETED],
        },
        {"GST Status": ["stage | preparing"]},
        "GST Status",
        existing_filed_until="2026-03-31",
    )

    assert result.status == "update"
    assert result.company == "Example Co"
    assert result.company_field == "GST Filed Until"
    assert result.fields == {"GST Filed Until": "2026-04-30"}


def test_completed_status_does_not_move_cursor_backward():
    result = build_business_status_filed_until_update(
        {
            "Type": TYPE_GST,
            "Company": "Example Co",
            "Period End": "2026-04-30",
            "GST Status": BUSINESS_STATUS_COMPLETED,
        },
        {"GST Status": "stage | preparing"},
        "GST Status",
        existing_filed_until="2026-05-31",
    )

    assert result.status == "skipped"
    assert result.reason == "not_after_existing_filed_until"


def test_completed_business_status_blocks_missing_period_end():
    result = build_business_status_filed_until_update(
        {
            "Type": TYPE_T2,
            "Company": "Example Co",
            "Period End": "",
            "T2 Status": BUSINESS_STATUS_COMPLETED,
        },
        {"T2 Status": "stage | preparing"},
        "T2 Status",
    )

    assert result.status == "needs_review"
    assert result.reason == "invalid_period_end"


def test_completed_business_status_blocks_mismatched_type():
    result = build_business_status_filed_until_update(
        {
            "Type": TYPE_PST,
            "Company": "Example Co",
            "Period End": "2026-04-30",
            "GST Status": BUSINESS_STATUS_COMPLETED,
        },
        {"GST Status": "stage | preparing"},
        "GST Status",
    )

    assert result.status == "needs_review"
    assert result.reason == "type_status_mismatch"
