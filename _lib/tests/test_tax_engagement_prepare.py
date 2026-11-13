"""Tests for daily GST/PST/T2 Engagement preparation."""

from datetime import date
import msvcrt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import tax_engagement_prepare as prepare


def test_month_window_uses_calendar_months_across_year_boundary():
    assert prepare.month_window(date(2026, 1, 15)) == (
        date(2025, 12, 1),
        date(2026, 2, 28),
    )


def test_due_date_windows_match_approved_rules():
    today = date(2026, 6, 17)
    assert prepare.due_date_in_window("GST", date(2026, 5, 1), today)
    assert prepare.due_date_in_window("PST", date(2026, 7, 31), today)
    assert not prepare.due_date_in_window("GST", date(2026, 4, 30), today)
    assert prepare.due_date_in_window("T2", date(2020, 1, 1), today)
    assert prepare.due_date_in_window("T2", date(2026, 7, 31), today)
    assert not prepare.due_date_in_window("T2", date(2026, 8, 1), today)


def test_priority_uses_company_urgency_then_due_date_fallback():
    today = date(2026, 6, 17)
    assert prepare.priority_for_due_date(
        date(2026, 8, 1), today, "Blocked"
    ) == "Blocked"
    assert prepare.priority_for_due_date(
        date(2026, 6, 16), today
    ) == "P1 Overdue"
    assert prepare.priority_for_due_date(
        date(2026, 6, 24), today
    ) == "P2 Due This Week"
    assert prepare.priority_for_due_date(
        date(2026, 7, 10), today
    ) == "P3 Due Soon"


def test_build_gst_candidate_contains_operational_fields():
    fields, reason = prepare.build_candidate(
        {
            "Legal Name": "Example Ltd.",
            "GST Frequency": "Quarterly",
            "GST Filed Until": "2026-03-31",
            "GST Next Period End": "2026-06-30",
            "GST Due Next Period": "2026-07-17",
            "GST Urgency": "Due Soon",
        },
        "GST",
        date(2026, 6, 17),
    )
    assert reason is None
    assert fields["Period Start"] == "2026-04-01"
    assert fields["Period End"] == "2026-06-30"
    assert fields["Due Date"] == "2026-07-17"
    assert fields["Priority"] == "P3 Due Soon"
    assert fields["GST Status"] == "stage | not started"
    assert fields["Source"] == "Auto"


def test_build_t2_candidate_maps_year_end_and_real_due_date():
    fields, reason = prepare.build_candidate(
        {
            "Legal Name": "Example Ltd.",
            "T2 Filed Until": "2025-04-30",
            "T2 Due Next Period": "2026-04-30",
            "T2 Urgency": "OK",
        },
        "T2",
        date(2026, 6, 17),
    )
    assert reason is None
    assert fields["Frequency"] == "Annual"
    assert fields["Period Start"] == "2025-05-01"
    assert fields["Period End"] == "2026-04-30"
    assert fields["Due Date"] == "2026-07-30"
    assert fields["T2 Year End"] == "2026-04-30"
    assert fields["T2 Status"] == "stage | not started"


def test_build_candidate_skips_incomplete_or_outside_rows():
    fields, reason = prepare.build_candidate(
        {
            "Legal Name": "Example Ltd.",
            "PST Frequency": "Monthly",
            "PST Filed Until": "2026-04-30",
            "PST Next Period End": "2026-05-31",
            "PST Due Next Period": "2026-04-30",
        },
        "PST",
        date(2026, 6, 17),
    )
    assert fields is None
    assert reason == "outside_due_window"


def test_dry_run_is_idempotent_against_existing_due_date(monkeypatch):
    company = {
        "Legal Name": "Example Ltd.",
        "GST Frequency": "Quarterly",
        "GST Filed Until": "2026-03-31",
        "GST Next Period End": "2026-06-30",
        "GST Due Next Period": "2026-07-17",
        "GST Urgency": "OK",
    }
    existing = {
        "Company": "Example Ltd.",
        "Type": "GST",
        "Period End": "2026-06-29",
        "Due Date": "2026-07-17",
    }
    monkeypatch.setattr(prepare.data_access, "list_companies", lambda: [company])
    monkeypatch.setattr(prepare.data_access, "list_engagements", lambda: [existing])

    summary = prepare.scan_tax_engagements(date(2026, 6, 17), apply=False)

    assert summary["stats"]["candidate_count"] == 0
    assert summary["stats"]["skipped_existing_count"] == 1


def test_refresh_updates_tax_and_adjacent_month_business_views(monkeypatch):
    calls = []

    def fake_update(view_id, **kwargs):
        calls.append((view_id, kwargs))
        return []

    monkeypatch.setattr(
        prepare.data_access,
        "update_tax_workbench_date_filters",
        fake_update,
    )

    changes = prepare.refresh_tax_view_filters(date(2026, 6, 22))

    assert set(changes) == {"GST", "PST", "T2", "Bookkeeping", "Annual Return"}
    by_view = dict(calls)
    for view_id in (
        prepare.settings.NOCODB_BOOKKEEPING_WORKBENCH_VIEW_ID,
        prepare.settings.NOCODB_ANNUAL_RETURN_WORKBENCH_VIEW_ID,
    ):
        assert by_view[view_id] == {
            "lower_sub_op": "daysAgo",
            "lower_value": 52,
            "upper_sub_op": "daysFromNow",
            "upper_value": 39,
        }
    assert by_view[prepare.settings.NOCODB_T2_WORKBENCH_VIEW_ID]["lower_value"] is None


def test_build_candidate_ignores_company_urgency_for_priority():
    """Companies-level GST/PST/T2 Urgency is confirmed stale batch-imported
    data (164/166 companies have a non-blank value, nothing ever writes it) --
    priority must come from due_date math alone, never this field."""
    fields, reason = prepare.build_candidate(
        {
            "Legal Name": "Example Ltd.",
            "GST Frequency": "Quarterly",
            "GST Filed Until": "2026-03-31",
            "GST Next Period End": "2026-06-30",
            "GST Due Next Period": "2026-07-17",
            "GST Urgency": "Needs Review",  # would force P0 under the old behavior
        },
        "GST",
        date(2026, 6, 17),
    )
    assert reason is None
    assert fields["Priority"] == "P3 Due Soon"  # pure due_date math, not P0


def test_refresh_stale_priorities_updates_drifted_engagement(monkeypatch):
    calls = []
    monkeypatch.setattr(
        prepare.data_access,
        "update_record",
        lambda **kwargs: calls.append(kwargs),
    )
    engagement = {
        "Id": 219,
        "Company": "Example Ltd.",
        "Type": "PST",
        "PST Status": "stage | not started",
        "Due Date": "2026-06-30",
        "Priority": "P1 Overdue",  # stale -- due_date is 5 days out, not overdue
    }
    refreshed, candidates = prepare.refresh_stale_priorities(
        [engagement], date(2026, 6, 25), actor="Test", apply=True
    )
    assert candidates == []
    assert len(refreshed) == 1
    assert refreshed[0]["new_priority"] == "P2 Due This Week"
    assert calls == [{
        "table": "Engagements",
        "record_id": 219,
        "fields": {"Priority": "P2 Due This Week"},
        "actor": "Test",
    }]


def test_refresh_stale_priorities_dry_run_does_not_write(monkeypatch):
    monkeypatch.setattr(
        prepare.data_access, "update_record",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not write in dry-run")),
    )
    engagement = {
        "Id": 219, "Company": "Example Ltd.", "Type": "PST",
        "PST Status": "stage | not started",
        "Due Date": "2026-06-30", "Priority": "P1 Overdue",
    }
    refreshed, candidates = prepare.refresh_stale_priorities(
        [engagement], date(2026, 6, 25), actor="Test", apply=False
    )
    assert refreshed == []
    assert len(candidates) == 1
    assert candidates[0]["new_priority"] == "P2 Due This Week"


def test_refresh_stale_priorities_skips_done_engagements(monkeypatch):
    monkeypatch.setattr(prepare.data_access, "update_record", lambda **kwargs: None)
    engagement = {
        "Id": 1, "Company": "Example Ltd.", "Type": "PST",
        "PST Status": "stage | completed",
        "Due Date": "2026-06-30", "Priority": "P1 Overdue",
    }
    refreshed, candidates = prepare.refresh_stale_priorities(
        [engagement], date(2026, 6, 25), actor="Test", apply=True
    )
    assert refreshed == [] and candidates == []


def test_refresh_stale_priorities_skips_p0_needs_review():
    """P0 is a sticky exception flag -- only a human should clear it."""
    engagement = {
        "Id": 1, "Company": "Example Ltd.", "Type": "T2",
        "T2 Status": "stage | not started",
        "Due Date": "2026-06-30", "Priority": "P0 Needs Review",
    }
    refreshed, candidates = prepare.refresh_stale_priorities(
        [engagement], date(2026, 6, 25), actor="Test", apply=True
    )
    assert refreshed == [] and candidates == []


def test_refresh_stale_priorities_skips_unparseable_due_date():
    engagement = {
        "Id": 1, "Company": "Example Ltd.", "Type": "GST",
        "GST Status": "stage | not started",
        "Due Date": None, "Priority": "P1 Overdue",
    }
    refreshed, candidates = prepare.refresh_stale_priorities(
        [engagement], date(2026, 6, 25), actor="Test", apply=True
    )
    assert refreshed == [] and candidates == []


def test_refresh_stale_priorities_no_op_when_already_correct():
    engagement = {
        "Id": 1, "Company": "Example Ltd.", "Type": "GST",
        "GST Status": "stage | not started",
        "Due Date": "2026-07-30", "Priority": "P4 OK",
    }
    refreshed, candidates = prepare.refresh_stale_priorities(
        [engagement], date(2026, 6, 25), actor="Test", apply=True
    )
    assert refreshed == [] and candidates == []


def test_apply_run_rejected_while_lock_already_held():
    """An apply run must not race past another apply run's dedupe check.

    Holds the same byte range _apply_lock() would lock, then confirms a
    concurrent apply call returns the locked-conflict shape immediately
    instead of touching data_access at all.
    """
    prepare.LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(prepare.LOCK_PATH, "a+b")
    lock_file.seek(0)
    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    try:
        summary = prepare.scan_tax_engagements(date(2026, 6, 17), apply=True)
        assert summary == {"error": "refresh already in progress", "locked": True}
    finally:
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        lock_file.close()

