"""Webhook tests for dedicated Bookkeeping and Annual Return automation."""

import os
import sys
import unittest.mock as mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def webhook_client():
    import server.webhooks as webhooks
    from server.app import create_app

    with mock.patch.object(webhooks, "NOCODB_WEBHOOK_SECRET", "test-secret"):
        app = create_app()
        app.config["TESTING"] = True
        yield webhooks, app.test_client()


def post(client, payload):
    return client.post(
        "/webhook/engagement-business-status-completed",
        json=payload,
        headers={"X-Webhook-Secret": "test-secret"},
    )


def test_bookkeeping_completed_updates_cursor_and_creates_next(webhook_client):
    webhooks, client = webhook_client
    payload = {
        "type": "records.after.update",
        "data": {
            "rows": [{
                "Id": 20,
                "Type": "Bookkeeping",
                "Bookkeeping Status": "stage | completed",
            }],
            "previous_rows": [{
                "Id": 20,
                "Type": "Bookkeeping",
                "Bookkeeping Status": "stage | review",
            }],
        },
    }
    with mock.patch.object(
        webhooks.bookkeeping_annual_automation,
        "complete_bookkeeping_engagement",
        return_value={"status": "updated", "next": {"status": "created"}},
    ) as complete:
        response = post(client, payload)

    assert response.status_code == 200
    assert response.get_json()["updated"] == 1
    assert response.get_json()["created"] == 1
    complete.assert_called_once()


def test_annual_return_completed_creates_next_year(webhook_client):
    webhooks, client = webhook_client
    payload = {
        "type": "records.after.update",
        "data": {
            "rows": [{
                "Id": 21,
                "Type": "Annual Return",
                "Annual Return Status": "stage | completed",
            }],
            "previous_rows": [{
                "Id": 21,
                "Type": "Annual Return",
                "Annual Return Status": "stage | filed",
            }],
        },
    }
    with mock.patch.object(
        webhooks.bookkeeping_annual_automation,
        "complete_annual_return_engagement",
        return_value={"status": "created"},
    ) as complete:
        response = post(client, payload)

    assert response.status_code == 200
    assert response.get_json()["created"] == 1
    complete.assert_called_once()


def test_gst_cursor_calculation_then_ensures_bookkeeping(webhook_client):
    webhooks, client = webhook_client
    payload = {
        "type": "records.after.update",
        "data": {
            "rows": [{
                "Id": 10,
                "Legal Name": "Example Co",
                "GST Filed Until": "2026-06-30",
                "GST Frequency": "Quarterly",
                "Bookkeeping Filed Until": "2026-03-31",
            }],
            "previous_rows": [{
                "Id": 10,
                "Legal Name": "Example Co",
                "GST Filed Until": "2026-03-31",
                "GST Frequency": "Quarterly",
                "Bookkeeping Filed Until": "2026-03-31",
            }],
        },
    }
    with mock.patch.object(
        webhooks.data_access,
        "update_company_by_legal_name",
        return_value={"Id": 10},
    ), mock.patch.object(
        webhooks.bookkeeping_annual_automation,
        "ensure_next_bookkeeping_engagement",
        return_value={"status": "created"},
    ) as ensure:
        response = client.post(
            "/webhook/company-filed-until-updated",
            json=payload,
            headers={"X-Webhook-Secret": "test-secret"},
        )

    assert response.status_code == 200
    assert response.get_json()["updated"] == 1
    assert response.get_json()["created"] == 1
    assert ensure.call_args.args[0]["GST Filed Until"] == "2026-06-30"
    assert ensure.call_args.args[0]["GST Due Next Period"] == "2026-10-31"

