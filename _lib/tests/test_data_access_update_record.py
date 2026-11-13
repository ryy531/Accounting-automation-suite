"""Tests for the generic Companies/Engagements/People field-update tool."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import data_access


class _FakeClient:
    """Stand-in for nocodb.NocoDBClient capturing calls instead of hitting the network."""

    columns = [
        {"title": "Id", "uidt": "ID"},
        {"title": "CreatedAt", "uidt": "CreatedTime"},
        {"title": "Notes", "uidt": "LongText"},
        {"title": "Legal Name", "uidt": "SingleLineText"},
    ]
    update_row_calls = []

    def get_table_metadata(self, table_id):
        return {"columns": self.columns}

    def update_row(self, table_id, row_id, fields):
        self.update_row_calls.append((table_id, row_id, fields))
        return {"Id": row_id, **fields}


def _patch_client(monkeypatch):
    monkeypatch.setattr(data_access.nocodb, "NocoDBClient", _FakeClient)
    _FakeClient.update_row_calls = []
    events = []
    monkeypatch.setattr(
        data_access,
        "create_event",
        lambda **kwargs: events.append(kwargs),
    )
    return events


def test_get_protected_field_names_uses_column_type_not_a_static_list(monkeypatch):
    _patch_client(monkeypatch)
    protected = data_access.get_protected_field_names("Companies")
    assert protected == {"Id", "CreatedAt"}


def test_update_record_rejects_protected_field_without_calling_update_row(monkeypatch):
    _patch_client(monkeypatch)
    try:
        data_access.update_record(
            "Companies", 1, {"CreatedAt": "2026-01-01", "Notes": "ok"}, actor="Riley"
        )
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "CreatedAt" in str(exc)
    assert _FakeClient.update_row_calls == []


def test_update_record_writes_allowed_field_and_logs_event(monkeypatch):
    events = _patch_client(monkeypatch)
    result = data_access.update_record(
        "Companies", 42, {"Notes": "called client about T2"}, actor="Riley"
    )
    assert result == {"Id": 42, "Notes": "called client about T2"}
    assert _FakeClient.update_row_calls == [
        (data_access.settings.NOCODB_COMPANIES_TABLE_ID, 42, {"Notes": "called client about T2"})
    ]
    assert len(events) == 1
    assert events[0]["actor"] == "Riley"
    assert events[0]["engagement_id"] == "Companies:42"
    assert "Notes -> called client about T2" in events[0]["description"]
    # Events.Event Type is a fixed NocoDB SingleSelect -- "Record Updated" is
    # not a valid option and would 400 against the real API.
    assert events[0]["event_type"] == "Manual Action"


def test_table_id_rejects_unknown_table():
    try:
        data_access._table_id("Invoices")
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "Invoices" in str(exc)


class _FakeCreateClient:
    """nocodb_client.create_row() posts a bulk-create body -- NocoDB always
    returns a list of created rows, even for a single row, e.g. [{"Id": 171}].
    create_person/create_company must index into that list, not call .get()
    on it directly (regression: AttributeError on real Seavision backfill,
    2026-06-26 -- the mocked tests never exercised the real client shape)."""

    create_row_calls = []

    def create_row(self, table_id, fields):
        self.create_row_calls.append((table_id, fields))
        return [{"Id": 171, **fields}]


def test_create_person_handles_list_response_from_create_row(monkeypatch):
    fake = _FakeCreateClient()
    fake.create_row_calls = []
    monkeypatch.setattr(data_access.nocodb, "NocoDBClient", lambda: fake)
    events = []
    monkeypatch.setattr(data_access, "create_event", lambda **kwargs: events.append(kwargs))

    result = data_access.create_person(full_name="Test Person", email="t@example.com")
    assert result["Id"] == 171
    assert events[0]["engagement_id"] == "People:171"


def test_create_company_handles_list_response_from_create_row(monkeypatch):
    fake = _FakeCreateClient()
    fake.create_row_calls = []
    monkeypatch.setattr(data_access.nocodb, "NocoDBClient", lambda: fake)
    events = []
    monkeypatch.setattr(data_access, "create_event", lambda **kwargs: events.append(kwargs))

    result = data_access.create_company(legal_name="Test Co Ltd.", owner_name="Test Owner")
    assert result["Id"] == 171
    assert events[0]["engagement_id"] == "Companies:171"
