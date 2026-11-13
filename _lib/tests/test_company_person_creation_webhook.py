"""Tests for the Company-created / Person-created NocoDB insert webhooks."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_client(monkeypatch):
    import server.webhooks as wh
    from server.app import create_app

    monkeypatch.setattr(wh, "NOCODB_WEBHOOK_SECRET", "test-secret")
    app = create_app()
    app.config["TESTING"] = True
    return wh, app.test_client()


def _post(client, path, payload, secret="test-secret"):
    headers = {"X-Webhook-Secret": secret} if secret is not None else {}
    return client.post(path, json=payload, headers=headers)


# ── company-created ──────────────────────────────────────────────────────────

def test_company_created_rejects_bad_secret(monkeypatch):
    _, client = _make_client(monkeypatch)
    resp = _post(client, "/webhook/company-created", {}, secret="wrong")
    assert resp.status_code == 401


def test_company_created_ignores_wrong_event_type(monkeypatch):
    wh, client = _make_client(monkeypatch)
    resp = _post(client, "/webhook/company-created", {"type": "records.after.update", "data": {}})
    assert resp.status_code == 200
    assert resp.get_json()["reason"] == "wrong_event_type"


def test_company_created_ignores_missing_rows(monkeypatch):
    wh, client = _make_client(monkeypatch)
    resp = _post(client, "/webhook/company-created", {"type": "records.after.insert", "data": {}})
    assert resp.status_code == 200
    assert resp.get_json()["reason"] == "invalid_rows"


def test_company_created_dispatches_each_row_to_resolver(monkeypatch):
    wh, client = _make_client(monkeypatch)
    calls = []

    def fake_resolver(row):
        calls.append(row.get("Id"))
        return {"action": "linked"}

    monkeypatch.setattr(wh.people_bridging, "resolve_or_create_primary_owner_for_company", fake_resolver)

    payload = {
        "type": "records.after.insert",
        "data": {"rows": [{"Id": 1, "Owner Name": "A"}, {"Id": 2, "Owner Name": "B"}]},
    }
    resp = _post(client, "/webhook/company-created", payload)
    assert resp.status_code == 200
    assert resp.get_json()["results"] == ["linked", "linked"]
    assert calls == [1, 2]


def test_company_created_one_row_failure_does_not_block_others(monkeypatch):
    wh, client = _make_client(monkeypatch)

    def flaky_resolver(row):
        if row.get("Id") == 1:
            raise RuntimeError("boom")
        return {"action": "linked"}

    monkeypatch.setattr(wh.people_bridging, "resolve_or_create_primary_owner_for_company", flaky_resolver)

    payload = {"type": "records.after.insert", "data": {"rows": [{"Id": 1}, {"Id": 2}]}}
    resp = _post(client, "/webhook/company-created", payload)
    assert resp.status_code == 200
    assert resp.get_json()["results"] == ["error", "linked"]


# ── person-created ───────────────────────────────────────────────────────────

def test_person_created_rejects_bad_secret(monkeypatch):
    _, client = _make_client(monkeypatch)
    resp = _post(client, "/webhook/person-created", {}, secret="wrong")
    assert resp.status_code == 401


def test_person_created_dispatches_each_row_to_resolver(monkeypatch):
    wh, client = _make_client(monkeypatch)
    calls = []

    def fake_resolver(row):
        calls.append(row.get("Id"))
        return {"action": "created_and_linked"}

    monkeypatch.setattr(wh.people_bridging, "resolve_or_create_company_for_person", fake_resolver)

    payload = {"type": "records.after.insert", "data": {"rows": [{"Id": 7, "Company": "ABC Ltd."}]}}
    resp = _post(client, "/webhook/person-created", payload)
    assert resp.status_code == 200
    assert resp.get_json()["results"] == ["created_and_linked"]
    assert calls == [7]


def test_person_created_ignores_non_dict_payload(monkeypatch):
    wh, client = _make_client(monkeypatch)
    resp = client.post(
        "/webhook/person-created", data="not json",
        headers={"X-Webhook-Secret": "test-secret", "Content-Type": "text/plain"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["reason"] == "invalid_json"


# ── company-owner-updated (the bug fix: NocoDB UI inserts blank then fills via update) ──

def test_company_owner_updated_fires_when_owner_email_changes(monkeypatch):
    wh, client = _make_client(monkeypatch)
    calls = []

    def fake_resolver(row, actor=None):
        calls.append((row.get("Id"), actor))
        return {"action": "created_and_linked"}

    monkeypatch.setattr(wh.people_bridging, "resolve_or_create_primary_owner_for_company", fake_resolver)

    payload = {
        "type": "records.after.update",
        "data": {
            "rows": [{"Id": 169, "Owner Name": "Jane Doe", "Owner Email": "jane.doe@example.com"}],
            "previous_rows": [{"Id": 169, "Owner Name": None, "Owner Email": None}],
        },
    }
    resp = _post(client, "/webhook/company-owner-updated", payload)
    assert resp.status_code == 200
    assert resp.get_json()["results"] == ["created_and_linked"]
    assert calls == [(169, "Webhook: Company Owner Updated")]


def test_company_owner_updated_skips_when_owner_fields_unchanged(monkeypatch):
    wh, client = _make_client(monkeypatch)
    calls = []
    monkeypatch.setattr(
        wh.people_bridging, "resolve_or_create_primary_owner_for_company",
        lambda row, actor=None: calls.append(row.get("Id")),
    )

    payload = {
        "type": "records.after.update",
        "data": {
            "rows": [{"Id": 169, "Owner Name": "YuQi He", "GST Frequency": "Monthly"}],
            "previous_rows": [{"Id": 169, "Owner Name": "YuQi He", "GST Frequency": "Quarterly"}],
        },
    }
    resp = _post(client, "/webhook/company-owner-updated", payload)
    assert resp.status_code == 200
    assert resp.get_json()["results"] == []
    assert calls == []


def test_company_owner_updated_rejects_bad_secret(monkeypatch):
    _, client = _make_client(monkeypatch)
    resp = _post(client, "/webhook/company-owner-updated", {}, secret="wrong")
    assert resp.status_code == 401


# ── person-company-updated ───────────────────────────────────────────────────

def test_person_company_updated_fires_when_company_field_changes(monkeypatch):
    wh, client = _make_client(monkeypatch)
    calls = []

    def fake_resolver(row, actor=None):
        calls.append((row.get("Id"), actor))
        return {"action": "linked"}

    monkeypatch.setattr(wh.people_bridging, "resolve_or_create_company_for_person", fake_resolver)

    payload = {
        "type": "records.after.update",
        "data": {
            "rows": [{"Id": 7, "Company": "ABC Ltd."}],
            "previous_rows": [{"Id": 7, "Company": None}],
        },
    }
    resp = _post(client, "/webhook/person-company-updated", payload)
    assert resp.status_code == 200
    assert resp.get_json()["results"] == ["linked"]
    assert calls == [(7, "Webhook: Person Company Updated")]


def test_person_company_updated_skips_when_company_field_unchanged(monkeypatch):
    wh, client = _make_client(monkeypatch)
    calls = []
    monkeypatch.setattr(
        wh.people_bridging, "resolve_or_create_company_for_person",
        lambda row, actor=None: calls.append(row.get("Id")),
    )

    payload = {
        "type": "records.after.update",
        "data": {
            "rows": [{"Id": 7, "Company": "ABC Ltd.", "Phone": "111"}],
            "previous_rows": [{"Id": 7, "Company": "ABC Ltd.", "Phone": "000"}],
        },
    }
    resp = _post(client, "/webhook/person-company-updated", payload)
    assert resp.status_code == 200
    assert resp.get_json()["results"] == []
    assert calls == []
