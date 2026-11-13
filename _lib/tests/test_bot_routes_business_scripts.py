"""Tests for the bot-triggered Tax Reminder preview/deliver and Bookkeeping
catch-up routes added to server/bot_routes.py."""

import os
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.app import create_app
from server import bot_routes


@pytest.fixture
def client():
    app = create_app()
    app.testing = True
    return app.test_client()


# ── /bot/tax-reminders (preview, read-only) ─────────────────────────────────

def test_tax_reminders_preview_unknown_sheet(client):
    resp = client.get("/bot/tax-reminders?sheet=NotASheet")
    assert resp.status_code == 400


def test_tax_reminders_preview_returns_count_and_text(client, monkeypatch):
    monkeypatch.setattr(bot_routes, "TAX_REMINDER_SHEETS", ["GST"])
    monkeypatch.setattr(
        bot_routes.rem_nocodb, "get_tax_reminder_clients", lambda sheet: None
    )
    monkeypatch.setattr(
        bot_routes.tracker_reader,
        "load_tax_tracker",
        lambda sheet: [
            {"Legal Name": "Acme Inc", "Owner Email": "a@x.com",
             "Assigned": "Riley", "Language": "EN"},
        ],
    )
    sent = []
    monkeypatch.setattr(bot_routes.rem_whatsapp, "send_to_group", lambda text: sent.append(text) or True)

    resp = client.get("/bot/tax-reminders?sheet=GST")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["sheets"]["GST"]["count"] >= 0
    assert "preview" in body["sheets"]["GST"]
    assert sent == []  # preview must never deliver


def test_tax_reminders_preview_missing_tracker_reports_error_not_500(client, monkeypatch):
    monkeypatch.setattr(bot_routes, "TAX_REMINDER_SHEETS", ["GST"])
    monkeypatch.setattr(
        bot_routes.rem_nocodb, "get_tax_reminder_clients", lambda sheet: None
    )

    def _raise(sheet):
        raise FileNotFoundError("tracker missing")

    monkeypatch.setattr(bot_routes.tracker_reader, "load_tax_tracker", _raise)

    resp = client.get("/bot/tax-reminders?sheet=GST")
    assert resp.status_code == 200
    assert "error" in resp.get_json()["sheets"]["GST"]


# ── /bot/tax-reminders/deliver (confirm-gated send) ─────────────────────────

def test_tax_reminders_deliver_requires_actor(client):
    resp = client.post("/bot/tax-reminders/deliver", json={"sheet": "GST"})
    assert resp.status_code == 400


def test_tax_reminders_deliver_rejects_bad_sheet(client):
    resp = client.post(
        "/bot/tax-reminders/deliver", json={"sheet": "Nope", "actor": "Riley"}
    )
    assert resp.status_code == 400


def test_tax_reminders_deliver_sends_when_clients_due(client, monkeypatch):
    monkeypatch.setattr(bot_routes, "TAX_REMINDER_SHEETS", ["GST"])
    monkeypatch.setattr(
        bot_routes.rem_nocodb, "get_tax_reminder_clients", lambda sheet: None
    )
    monkeypatch.setattr(
        bot_routes.tracker_reader,
        "load_tax_tracker",
        lambda sheet: [
            {"Legal Name": "Acme Inc", "Owner Email": "a@x.com",
             "Assigned": "Riley", "Language": "EN"},
        ],
    )
    fake_client = bot_routes.tax_reminder.TaxReminderClient(
        legal_name="Acme Inc", owner_name="Bob", owner_email="a@x.com",
        round_num=1, assigned="Riley", language="EN",
    )
    monkeypatch.setattr(
        bot_routes.tax_reminder, "get_due_clients",
        lambda rows, sheet, today: [fake_client],
    )
    sent = []
    monkeypatch.setattr(
        bot_routes.rem_whatsapp, "send_to_group", lambda text: sent.append(text) or True
    )
    monkeypatch.setattr(bot_routes.rem_wechat, "send_to_group", lambda text: True)

    resp = client.post(
        "/bot/tax-reminders/deliver", json={"sheet": "GST", "actor": "Riley"}
    )
    assert resp.status_code == 200
    assert resp.get_json()["sent"] is True
    assert len(sent) == 1


def test_tax_reminders_deliver_stays_silent_when_nothing_due(client, monkeypatch):
    monkeypatch.setattr(bot_routes, "TAX_REMINDER_SHEETS", ["GST"])
    monkeypatch.setattr(
        bot_routes.rem_nocodb, "get_tax_reminder_clients", lambda sheet: None
    )
    monkeypatch.setattr(bot_routes.tracker_reader, "load_tax_tracker", lambda sheet: [])
    sent = []
    monkeypatch.setattr(
        bot_routes.rem_whatsapp, "send_to_group", lambda text: sent.append(text) or True
    )

    resp = client.post(
        "/bot/tax-reminders/deliver", json={"sheet": "GST", "actor": "Riley"}
    )
    assert resp.status_code == 200
    assert resp.get_json()["sent"] is False
    assert sent == []


def test_tax_reminders_deliver_reports_whatsapp_failure(client, monkeypatch):
    monkeypatch.setattr(bot_routes, "TAX_REMINDER_SHEETS", ["GST"])
    monkeypatch.setattr(
        bot_routes.rem_nocodb, "get_tax_reminder_clients", lambda sheet: None
    )
    monkeypatch.setattr(
        bot_routes.tracker_reader,
        "load_tax_tracker",
        lambda sheet: [{"Legal Name": "Acme Inc"}],
    )
    fake_client = bot_routes.tax_reminder.TaxReminderClient(
        legal_name="Acme Inc", owner_name="Bob", owner_email="a@x.com",
        round_num=1, assigned="Riley", language="EN",
    )
    monkeypatch.setattr(
        bot_routes.tax_reminder, "get_due_clients",
        lambda rows, sheet, today: [fake_client],
    )
    monkeypatch.setattr(bot_routes.rem_whatsapp, "send_to_group", lambda text: False)
    monkeypatch.setattr(bot_routes.rem_wechat, "send_to_group", lambda text: True)

    resp = client.post(
        "/bot/tax-reminders/deliver", json={"sheet": "GST", "actor": "Riley"}
    )
    assert resp.status_code == 500


# ── /bot/bookkeeping/refresh ─────────────────────────────────────────────────

def test_bookkeeping_refresh_requires_company_and_actor(client):
    assert client.post("/bot/bookkeeping/refresh", json={"actor": "Riley"}).status_code == 400
    assert client.post("/bot/bookkeeping/refresh", json={"company": "Acme"}).status_code == 400


def test_bookkeeping_refresh_no_match(client, monkeypatch):
    monkeypatch.setattr(bot_routes.data_access, "search_companies_by_ref", lambda ref: [])
    resp = client.post(
        "/bot/bookkeeping/refresh", json={"company": "Nonexistent Co", "actor": "Riley"}
    )
    assert resp.status_code == 200
    assert resp.get_json()["found"] == 0


def test_bookkeeping_refresh_ambiguous_match(client, monkeypatch):
    monkeypatch.setattr(
        bot_routes.data_access, "search_companies_by_ref",
        lambda ref: [{"Legal Name": "Acme A"}, {"Legal Name": "Acme B"}],
    )
    resp = client.post(
        "/bot/bookkeeping/refresh", json={"company": "Acme", "actor": "Riley"}
    )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["found"] == 2
    assert body["companies"] == ["Acme A", "Acme B"]


def test_bookkeeping_refresh_calls_ensure_next_with_actor(client, monkeypatch):
    company_row = {"Legal Name": "Acme Inc"}
    monkeypatch.setattr(
        bot_routes.data_access, "search_companies_by_ref", lambda ref: [company_row]
    )
    calls = []

    def _fake_ensure(row, trigger_ref=None):
        calls.append((row, trigger_ref))
        return {"status": "created", "company": "Acme Inc", "period_end": "2026-06-30"}

    monkeypatch.setattr(
        bot_routes.bookkeeping_annual_automation,
        "ensure_next_bookkeeping_engagement",
        _fake_ensure,
    )

    resp = client.post(
        "/bot/bookkeeping/refresh", json={"company": "Acme", "actor": "Riley"}
    )
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "created"
    assert calls == [(company_row, "Bot:Riley")]
