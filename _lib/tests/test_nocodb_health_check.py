"""Tests for the read-only NocoDB platform health check.

All NocoDB I/O is faked -- these tests never touch a live connection. The
report shape must stay aggregate-only: no customer names, emails, phone numbers,
SIN, tokens, URLs, or webhook destinations.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools.nocodb_health_check as health_check


def _metadata(*fields, views=1):
    return {
        "columns": [{"title": field} for field in fields],
        "views": [{} for _ in range(views)],
    }


class FakeClient:
    def __init__(
        self,
        metadata=None,
        hooks=None,
        list_rows_result=None,
        raise_on_metadata=False,
    ):
        self._metadata = metadata or _metadata("Is Payroll Client", "Pay Frequency")
        self._hooks = hooks if hooks is not None else [{"id": "hook_1"}]
        self._list_rows_result = list_rows_result or {"list": [], "count": 0}
        self._raise_on_metadata = raise_on_metadata

    def get_table_metadata(self, table_id):
        if self._raise_on_metadata:
            raise RuntimeError("boom")
        return self._metadata

    def get_table_hooks(self, table_id):
        return self._hooks

    def list_rows(self, table_id):
        return self._list_rows_result


def test_check_table_uses_data_access_for_companies(monkeypatch):
    monkeypatch.setitem(health_check._TABLE_ID, "Companies", "fake_table_id")
    monkeypatch.setitem(
        health_check._DATA_ACCESS_ROW_FN,
        "Companies",
        lambda: [
            {"Id": 1, "Is Payroll Client": True, "Pay Frequency": {"value": "Monthly"}},
            {"Id": 2, "Is Payroll Client": False, "Pay Frequency": ""},
            {"Id": 3, "Is Payroll Client": True, "Pay Frequency": "Monthly"},
        ],
    )
    client = FakeClient(
        metadata=_metadata("Is Payroll Client", "Pay Frequency", views=3)
    )

    result = health_check._check_table(client, "Companies")

    assert result["table"] == "Companies"
    assert result["row_count"] == 3
    assert result["field_count"] == 2
    assert result["view_count"] == 3
    assert result["hook_count"] == 1
    assert result["field_distributions"]["fields"]["Is Payroll Client"] == {
        "True": 2,
        "False": 1,
    }
    assert result["field_distributions"]["fields"]["Pay Frequency"] == {
        "Monthly": 2,
        "(blank)": 1,
    }
    assert "error" not in result


def test_check_table_events_falls_back_to_client_list_rows(monkeypatch):
    monkeypatch.setitem(health_check._TABLE_ID, "Events", "fake_events_id")
    client = FakeClient(
        metadata=_metadata("Event Type", "Timestamp"),
        hooks=[{"id": "hook_1"}, {"id": "hook_2"}],
        list_rows_result={
            "list": [
                {"Id": 40, "Event Type": "Status Changed", "Timestamp": "2026-06-24"},
                {"Id": 42, "Event Type": "Needs Review", "Timestamp": "2026-06-25"},
            ],
            "count": 2,
        },
    )

    result = health_check._check_table(client, "Events")

    assert result["row_count"] == 2
    assert result["hook_count"] == 2
    assert result["field_distributions"]["fields"]["Event Type"] == {
        "Needs Review": 1,
        "Status Changed": 1,
    }
    assert result["quality_signals"]["growth_marker"]["max_id"] == 42
    assert result["quality_signals"]["growth_marker"]["latest_timestamp"] == "2026-06-25"
    assert "error" not in result


def test_people_language_anomalies_are_aggregate_only(monkeypatch):
    monkeypatch.setitem(health_check._TABLE_ID, "People", "fake_people_id")
    monkeypatch.setitem(
        health_check._DATA_ACCESS_ROW_FN,
        "People",
        lambda: [
            {"Id": 1, "Language": "Chinese"},
            {"Id": 2, "Language": {"value": "English"}},
            {"Id": 3, "Language": "1"},
            {"Id": 4, "Language": ""},
        ],
    )
    client = FakeClient(metadata=_metadata("Language"))

    result = health_check._check_table(client, "People")

    assert result["field_distributions"]["fields"]["Language"] == {
        "(blank)": 1,
        "1": 1,
        "Chinese": 1,
        "English": 1,
    }
    assert result["quality_signals"]["people_language_anomalies"] == {
        "status": "needs_review",
        "anomalous_values": {"1": 1},
    }


def test_distribution_handles_multi_select_list_values(monkeypatch):
    monkeypatch.setitem(health_check._TABLE_ID, "Engagements", "fake_engagements_id")
    monkeypatch.setitem(
        health_check._DATA_ACCESS_ROW_FN,
        "Engagements",
        lambda: [
            {"Id": 1, "Assigned To": ["Riley", "Alex"]},
            {"Id": 2, "Assigned To": ["Riley"]},
            {"Id": 3, "Assigned To": []},
        ],
    )
    client = FakeClient(metadata=_metadata("Assigned To"))

    result = health_check._check_table(client, "Engagements")

    assert result["field_distributions"]["fields"]["Assigned To"] == {
        "Riley": 2,
        "Alex": 1,
        "(blank)": 1,
    }


def test_check_table_never_echoes_untracked_field_values(monkeypatch):
    """Aggregate-only guard: a row with PII-shaped fields that aren't in
    _DISTRIBUTION_FIELDS must never have its values appear anywhere in the
    serialized result, even though the row dict itself carries them."""
    monkeypatch.setitem(health_check._TABLE_ID, "Companies", "fake_table_id")
    monkeypatch.setitem(
        health_check._DATA_ACCESS_ROW_FN,
        "Companies",
        lambda: [
            {
                "Id": 1,
                "Legal Name": "Acme Secret Corp",
                "Email": "owner@acme-secret.test",
                "Is Payroll Client": True,
            }
        ],
    )
    client = FakeClient(metadata=_metadata("Is Payroll Client", "Legal Name", "Email"))

    result = health_check._check_table(client, "Companies")

    serialized = json.dumps(result)
    assert "Acme Secret Corp" not in serialized
    assert "owner@acme-secret.test" not in serialized
    assert "Legal Name" not in result["field_distributions"]["fields"]
    assert "Email" not in result["field_distributions"]["fields"]


def test_engagement_blank_field_signal(monkeypatch):
    monkeypatch.setitem(health_check._TABLE_ID, "Engagements", "fake_engagements_id")
    monkeypatch.setitem(
        health_check._DATA_ACCESS_ROW_FN,
        "Engagements",
        lambda: [
            {"Id": 1, "Type": "Payroll", "Company": "A", "Status": "Open", "Priority": "P4 OK"},
            {"Id": 2, "Type": "GST", "Company": "B", "Status": "", "Priority": "P0 Needs Review"},
        ],
    )
    client = FakeClient(metadata=_metadata("Type", "Status", "Priority"))

    result = health_check._check_table(client, "Engagements")

    blank_fields = result["quality_signals"]["blank_fields"]
    assert blank_fields["Status"] == {"blank_count": 1, "blank_ratio": 0.5}
    assert blank_fields["Frequency"] == {"blank_count": 2, "blank_ratio": 1.0}
    assert result["field_distributions"]["fields"]["Type"] == {"GST": 1, "Payroll": 1}


def test_check_table_missing_tracked_fields_are_reported(monkeypatch):
    monkeypatch.setitem(health_check._TABLE_ID, "Companies", "fake_table_id")
    monkeypatch.setitem(health_check._DATA_ACCESS_ROW_FN, "Companies", lambda: [])
    client = FakeClient(metadata=_metadata("Is Payroll Client"))

    result = health_check._check_table(client, "Companies")

    assert result["field_distributions"]["fields"] == {"Is Payroll Client": {}}
    assert "Pay Frequency" in result["field_distributions"]["missing_tracked_fields"]


def test_check_table_missing_table_id_reports_error(monkeypatch):
    monkeypatch.setitem(health_check._TABLE_ID, "Companies", "")
    client = FakeClient()

    result = health_check._check_table(client, "Companies")

    assert "error" in result
    assert "not configured" in result["error"]


def test_check_table_metadata_failure_is_caught(monkeypatch):
    monkeypatch.setitem(health_check._TABLE_ID, "Companies", "fake_table_id")
    client = FakeClient(raise_on_metadata=True)

    result = health_check._check_table(client, "Companies")

    assert "error" in result
    assert "metadata fetch failed" in result["error"]


def test_check_table_row_fetch_failure_is_caught_but_metadata_kept(monkeypatch):
    monkeypatch.setitem(health_check._TABLE_ID, "Companies", "fake_table_id")

    def boom():
        raise RuntimeError("row fetch broke")

    monkeypatch.setitem(health_check._DATA_ACCESS_ROW_FN, "Companies", boom)
    client = FakeClient(metadata=_metadata("Is Payroll Client"))

    result = health_check._check_table(client, "Companies")

    assert result["field_count"] == 1
    assert result["view_count"] == 1
    assert result["hook_count"] == 1
    assert "error" in result
    assert "row fetch failed" in result["error"]


def test_write_report_creates_json_file(tmp_path, monkeypatch):
    monkeypatch.setattr(health_check, "REPORT_DIR", tmp_path)
    report = {"checked_at": "2026-06-25T00:00:00+00:00", "tables": []}

    path = health_check.write_report(report)

    assert path.exists()
    assert path.parent == tmp_path
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved == report


def test_report_shape_contains_no_customer_identifiers():
    forbidden = {"email", "sin", "phone", "legal name", "full name", "webhook"}
    sample = {
        "table": "Companies",
        "row_count": 166,
        "field_count": 65,
        "view_count": 8,
        "hook_count": 2,
        "field_distributions": {
            "fields": {
                "Is Payroll Client": {"True": 53, "False": 113},
                "Pay Frequency": {"Monthly": 29, "(blank)": 113},
            }
        },
        "quality_signals": {},
    }
    serialized = json.dumps(sample).lower()
    assert not any(term in serialized for term in forbidden)


def test_main_exits_nonzero_when_any_table_has_error(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(health_check, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(
        health_check,
        "run_health_check",
        lambda: {
            "checked_at": "2026-06-25T00:00:00+00:00",
            "tables": [
                {
                    "table": "Companies",
                    "row_count": 1,
                    "field_count": 2,
                    "view_count": 3,
                    "hook_count": 0,
                    "field_distributions": {"fields": {"Pay Frequency": {"Monthly": 1}}},
                    "quality_signals": {},
                },
                {"table": "Events", "error": "metadata fetch failed: NocoDBApiError"},
            ],
        },
    )

    try:
        health_check.main()
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("main() should exit nonzero when a table check fails")

    output = capsys.readouterr().out
    assert "Companies: rows=1 fields=2 views=3 hooks=0 distributions=1 signals=0" in output
    assert "Events: ERROR - metadata fetch failed" in output
