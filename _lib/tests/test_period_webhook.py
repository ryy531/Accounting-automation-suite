"""Tests for the recurring engagement period webhook."""

import os
import sys
import unittest.mock as mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestEngagementPeriodWebhook:
    @pytest.fixture(autouse=True)
    def _setup_app(self):
        import server.webhooks as wh
        from server.app import create_app

        with mock.patch.object(wh, "NOCODB_WEBHOOK_SECRET", "test-secret"):
            self.webhooks = wh
            self.app = create_app()
            self.app.config["TESTING"] = True
            self.client = self.app.test_client()
            yield

    def _post(self, payload):
        return self.client.post(
            "/webhook/engagement-period-updated",
            json=payload,
            headers={"X-Webhook-Secret": "test-secret"},
        )

    def test_complete_transition_calls_period_service(self):
        payload = {
            "type": "records.after.update",
            "data": {
                "rows": [{"Id": 1, "Status": "Complete"}],
                "previous_rows": [{"Id": 1, "Status": "In Progress"}],
            },
        }

        with mock.patch.object(
            self.webhooks.period_automation,
            "advance_completed_engagement",
            return_value={"status": "created"},
        ) as advance:
            resp = self._post(payload)

        assert resp.status_code == 200
        assert resp.get_json()["created"] == 1
        advance.assert_called_once()

    def test_wrong_event_type_ignored(self):
        resp = self._post({"type": "records.after.insert", "data": {}})
        assert resp.status_code == 200
        assert resp.get_json()["reason"] == "wrong_event_type"

    def test_service_failure_returns_200(self):
        payload = {
            "type": "records.after.update",
            "data": {
                "rows": [{"Id": 1, "Status": "Complete"}],
                "previous_rows": [{"Id": 1, "Status": "In Progress"}],
            },
        }

        with mock.patch.object(
            self.webhooks.period_automation,
            "advance_completed_engagement",
            side_effect=RuntimeError("boom"),
        ):
            resp = self._post(payload)

        assert resp.status_code == 200
        assert resp.get_json()["errors"] == 1

    def test_invalid_secret_rejected(self):
        resp = self.client.post(
            "/webhook/engagement-period-updated",
            json={"type": "records.after.update"},
            headers={"X-Webhook-Secret": "wrong"},
        )
        assert resp.status_code == 401


class TestCompanyFiledUntilWebhook:
    @pytest.fixture(autouse=True)
    def _setup_app(self):
        import server.webhooks as wh
        from server.app import create_app

        with mock.patch.object(wh, "NOCODB_WEBHOOK_SECRET", "test-secret"):
            self.webhooks = wh
            self.app = create_app()
            self.app.config["TESTING"] = True
            self.client = self.app.test_client()
            yield

    def _post(self, payload):
        return self.client.post(
            "/webhook/company-filed-until-updated",
            json=payload,
            headers={"X-Webhook-Secret": "test-secret"},
        )

    def test_filed_until_change_updates_calculated_company_fields(self):
        payload = {
            "type": "records.after.update",
            "data": {
                "rows": [{
                    "Id": 1,
                    "Legal Name": "Example Co",
                    "GST Filed Until": "2026-01-31",
                    "GST Frequency": "Monthly",
                    "PST Filed Until": "2026-01-31",
                    "PST Frequency": "Monthly",
                    "T2 Filed Until": "2025-12-31",
                }],
                "previous_rows": [{
                    "Id": 1,
                    "Legal Name": "Example Co",
                    "GST Filed Until": "2025-12-31",
                    "GST Frequency": "Monthly",
                    "PST Filed Until": "2026-01-31",
                    "PST Frequency": "Monthly",
                    "T2 Filed Until": "2025-12-31",
                }],
            },
        }

        with mock.patch.object(
            self.webhooks.data_access,
            "update_company_by_legal_name",
            return_value={"Id": 1},
        ) as update, mock.patch.object(
            self.webhooks.data_access,
            "create_event",
            return_value={"Id": 10},
        ) as create_event:
            resp = self._post(payload)

        assert resp.status_code == 200
        assert resp.get_json()["updated"] == 1
        update.assert_called_once()
        company_name, fields = update.call_args.args
        assert company_name == "Example Co"
        assert fields["GST Next Period End"] == "2026-02-28"
        assert fields["GST Due Next Period"] == "2026-03-31"
        assert fields["PST Next Period End"] == "2026-02-28"
        assert fields["PST Due Next Period"] == "2026-03-31"
        assert fields["T2 Due Next Period"] == "2026-12-31"
        create_event.assert_not_called()

    def test_non_filed_until_change_is_ignored(self):
        payload = {
            "type": "records.after.update",
            "data": {
                "rows": [{"Id": 1, "Legal Name": "Example Co", "Operating Name": "New"}],
                "previous_rows": [{"Id": 1, "Legal Name": "Example Co", "Operating Name": "Old"}],
            },
        }

        with mock.patch.object(
            self.webhooks.data_access,
            "update_company_by_legal_name",
        ) as update:
            resp = self._post(payload)

        assert resp.status_code == 200
        assert resp.get_json()["skipped"] == 1
        update.assert_not_called()


class TestEngagementBusinessStatusCompletedWebhook:
    @pytest.fixture(autouse=True)
    def _setup_app(self):
        import server.webhooks as wh
        from server.app import create_app

        with mock.patch.object(wh, "NOCODB_WEBHOOK_SECRET", "test-secret"):
            self.webhooks = wh
            self.app = create_app()
            self.app.config["TESTING"] = True
            self.client = self.app.test_client()
            yield

    def _post(self, payload):
        return self.client.post(
            "/webhook/engagement-business-status-completed",
            json=payload,
            headers={"X-Webhook-Secret": "test-secret"},
        )

    def test_gst_status_exact_completed_updates_company_filed_until(self):
        payload = {
            "type": "records.after.update",
            "data": {
                "rows": [{
                    "Id": 1,
                    "Type": "GST",
                    "Company": "Example Co",
                    "Period End": "2026-04-30",
                    "GST Status": ["stage | completed"],
                }],
                "previous_rows": [{
                    "Id": 1,
                    "Type": "GST",
                    "Company": "Example Co",
                    "Period End": "2026-04-30",
                    "GST Status": ["stage | preparing"],
                }],
            },
        }

        with mock.patch.object(
            self.webhooks.data_access,
            "list_companies_by_legal_name",
            return_value=[{"Id": 10, "Legal Name": "Example Co", "GST Filed Until": "2026-03-31"}],
        ), mock.patch.object(
            self.webhooks.data_access,
            "update_company_by_legal_name",
            return_value={"Id": 10},
        ) as update, mock.patch.object(
            self.webhooks.data_access,
            "create_event",
            return_value={"Id": 99},
        ) as create_event:
            resp = self._post(payload)

        assert resp.status_code == 200
        assert resp.get_json()["updated"] == 1
        update.assert_called_once_with("Example Co", {"GST Filed Until": "2026-04-30"})
        create_event.assert_not_called()

    def test_completed_with_blocker_does_not_update(self):
        payload = {
            "type": "records.after.update",
            "data": {
                "rows": [{
                    "Id": 1,
                    "Type": "GST",
                    "Company": "Example Co",
                    "Period End": "2026-04-30",
                    "GST Status": "stage | completed, blocker | payment missing",
                }],
                "previous_rows": [{
                    "Id": 1,
                    "Type": "GST",
                    "Company": "Example Co",
                    "Period End": "2026-04-30",
                    "GST Status": "stage | preparing",
                }],
            },
        }

        with mock.patch.object(
            self.webhooks.data_access,
            "update_company_by_legal_name",
        ) as update:
            resp = self._post(payload)

        assert resp.status_code == 200
        assert resp.get_json()["updated"] == 0
        update.assert_not_called()

    def test_legacy_status_complete_is_ignored(self):
        payload = {
            "type": "records.after.update",
            "data": {
                "rows": [{"Id": 1, "Type": "GST", "Status": "Complete"}],
                "previous_rows": [{"Id": 1, "Type": "GST", "Status": "In Progress"}],
            },
        }

        with mock.patch.object(
            self.webhooks.period_automation,
            "advance_completed_engagement",
        ) as advance, mock.patch.object(
            self.webhooks.data_access,
            "update_company_by_legal_name",
        ) as update:
            resp = self._post(payload)

        assert resp.status_code == 200
        assert resp.get_json()["ignored"] == 1
        advance.assert_not_called()
        update.assert_not_called()

    def test_mismatched_type_creates_needs_review(self):
        payload = {
            "type": "records.after.update",
            "data": {
                "rows": [{
                    "Id": 1,
                    "Type": "PST",
                    "Company": "Example Co",
                    "Period End": "2026-04-30",
                    "GST Status": "stage | completed",
                }],
                "previous_rows": [{
                    "Id": 1,
                    "Type": "PST",
                    "Company": "Example Co",
                    "Period End": "2026-04-30",
                    "GST Status": "stage | preparing",
                }],
            },
        }

        with mock.patch.object(
            self.webhooks.data_access,
            "update_company_by_legal_name",
        ) as update, mock.patch.object(
            self.webhooks.data_access,
            "create_event",
            return_value={"Id": 99},
        ) as create_event:
            resp = self._post(payload)

        assert resp.status_code == 200
        assert resp.get_json()["reviews"] == 1
        update.assert_not_called()
        create_event.assert_called_once()

    def test_missing_period_end_creates_needs_review(self):
        payload = {
            "type": "records.after.update",
            "data": {
                "rows": [{
                    "Id": 1,
                    "Type": "T2",
                    "Company": "Example Co",
                    "Period End": "",
                    "T2 Status": "stage | completed",
                }],
                "previous_rows": [{
                    "Id": 1,
                    "Type": "T2",
                    "Company": "Example Co",
                    "Period End": "",
                    "T2 Status": "stage | preparing",
                }],
            },
        }

        with mock.patch.object(
            self.webhooks.data_access,
            "update_company_by_legal_name",
        ) as update, mock.patch.object(
            self.webhooks.data_access,
            "create_event",
            return_value={"Id": 99},
        ) as create_event:
            resp = self._post(payload)

        assert resp.status_code == 200
        assert resp.get_json()["reviews"] == 1
        update.assert_not_called()
        create_event.assert_called_once()

    def test_non_status_update_is_ignored(self):
        payload = {
            "type": "records.after.update",
            "data": {
                "rows": [{"Id": 1, "Type": "GST", "Company": "Example Co", "Notes": "new"}],
                "previous_rows": [{"Id": 1, "Type": "GST", "Company": "Example Co", "Notes": "old"}],
            },
        }

        with mock.patch.object(
            self.webhooks.data_access,
            "update_company_by_legal_name",
        ) as update:
            resp = self._post(payload)

        assert resp.status_code == 200
        assert resp.get_json()["ignored"] == 1
        update.assert_not_called()
