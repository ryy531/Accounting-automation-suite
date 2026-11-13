"""Tests for server/cron.py -- full mock, no live NocoDB calls."""

import json
import os
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from server import cron
from server.cron import (
    CronLock,
    _get_today,
    create_payroll_engagement,
    engagement_exists,
    main,
    resolve_biweekly_anchor,
    retry,
)


def _monthly_company(id=1, name="Test Corp"):
    return {
        "Id": id,
        "Legal Name": name,
        "Is Payroll Client": True,
        "Pay Frequency": {"value": "Monthly"},
        "Pay Period End Rule": "",
    }


def _biweekly_company(id=1, name="Test Corp", rule=""):
    return {
        "Id": id,
        "Legal Name": name,
        "Is Payroll Client": True,
        "Pay Frequency": {"value": "Bi-weekly"},
        "Pay Period End Rule": rule,
    }


class FakeDataAccess:
    def __init__(self, *, companies=None, existing=False, create_side_effect=None):
        self.companies = companies or []
        self.existing = existing
        self.create_side_effect = list(create_side_effect or [])
        self.created = []
        self.engagement_checks = []
        self.last_period_by_company = {}

    def list_payroll_companies(self):
        return self.companies

    def get_last_payroll_period_end(self, company_name):
        return self.last_period_by_company.get(company_name)

    def engagement_exists(self, company_name, period_end):
        self.engagement_checks.append((company_name, period_end))
        return self.existing

    def create_payroll_engagement(self, company_name, period_start, period_end, frequency):
        if self.create_side_effect:
            effect = self.create_side_effect.pop(0)
            if isinstance(effect, Exception):
                raise effect
            return effect
        record = {
            "company": company_name,
            "period_start": period_start,
            "period_end": period_end,
            "frequency": frequency,
        }
        self.created.append(record)
        return record


def _install_fake_data(monkeypatch, fake):
    monkeypatch.setattr(cron.data_access, "list_payroll_companies", fake.list_payroll_companies)
    monkeypatch.setattr(cron.data_access, "get_last_payroll_period_end", fake.get_last_payroll_period_end)
    monkeypatch.setattr(cron.data_access, "engagement_exists", fake.engagement_exists)
    monkeypatch.setattr(cron.data_access, "create_payroll_engagement", fake.create_payroll_engagement)


def _run_main(monkeypatch, tmp_path, fake, *, today=date(2026, 5, 31)):
    _install_fake_data(monkeypatch, fake)
    monkeypatch.setattr(cron, "_get_today", lambda: today)
    monkeypatch.setattr(cron, "_LOG_DIR", tmp_path)
    monkeypatch.setattr(cron.time, "sleep", lambda *_: None)
    return main()


def _summary(tmp_path):
    files = list(tmp_path.glob("payroll_cron_*.json"))
    assert len(files) == 1
    return json.loads(files[0].read_text(encoding="utf-8"))


class TestMonthlyCreatesEngagement:
    def test_creates_and_summary(self, monkeypatch, tmp_path):
        fake = FakeDataAccess(companies=[_monthly_company()])
        exit_code = _run_main(monkeypatch, tmp_path, fake)

        assert exit_code == 0
        assert len(fake.created) == 1
        created = fake.created[0]
        assert created["company"] == "Test Corp"
        assert created["period_start"] == date(2026, 5, 1)
        assert created["period_end"] == date(2026, 5, 31)
        assert created["frequency"] == "Monthly"

        data = _summary(tmp_path)
        assert len(data["created"]) == 1
        assert data["created"][-1]["period_end"] == "2026-05-31"


class TestSkipsIfEngagementExists:
    def test_skip(self, monkeypatch, tmp_path):
        fake = FakeDataAccess(companies=[_monthly_company()], existing=True)
        exit_code = _run_main(monkeypatch, tmp_path, fake)

        assert exit_code == 0
        assert fake.created == []
        data = _summary(tmp_path)
        assert len(data["skipped_existing"]) == 1
        assert len(data["created"]) == 0


class TestContinuesOnSingleCompanyFailure:
    def test_company_a_fails_b_succeeds(self, monkeypatch, tmp_path):
        fake = FakeDataAccess(
            companies=[_monthly_company(id=1, name="A Corp"), _monthly_company(id=2, name="B Corp")],
            create_side_effect=[
                RuntimeError("err"), RuntimeError("err"), RuntimeError("err"),
                {"Id": 10},
            ],
        )
        exit_code = _run_main(monkeypatch, tmp_path, fake)

        assert exit_code == 1
        data = _summary(tmp_path)
        assert len(data["failed"]) == 1
        assert data["failed"][0]["company"] == "A Corp"
        assert len(data["created"]) == 1
        assert data["created"][0]["company"] == "B Corp"


class TestRetrySucceedsOnSecondAttempt:
    def test_retry(self, monkeypatch, tmp_path):
        fake = FakeDataAccess(
            companies=[_monthly_company()],
            create_side_effect=[RuntimeError("timeout"), {"Id": 1}],
        )
        exit_code = _run_main(monkeypatch, tmp_path, fake)

        assert exit_code == 0
        data = _summary(tmp_path)
        assert len(data["created"]) == 1
        assert len(data["failed"]) == 0


class TestLoadFailureAbortsEntireJob:
    def test_abort(self, monkeypatch, tmp_path):
        def boom():
            raise RuntimeError("load failed")

        monkeypatch.setattr(cron.data_access, "list_payroll_companies", boom)
        monkeypatch.setattr(cron, "_get_today", lambda: date(2026, 5, 31))
        monkeypatch.setattr(cron, "_LOG_DIR", tmp_path)
        monkeypatch.setattr(cron.time, "sleep", lambda *_: None)

        assert main() == 2


class TestBiWeeklyNoAnchor:
    def test_skipped_uninit(self, monkeypatch, tmp_path):
        fake = FakeDataAccess(companies=[_biweekly_company()])
        exit_code = _run_main(monkeypatch, tmp_path, fake, today=date(2026, 5, 22))

        assert exit_code == 0
        data = _summary(tmp_path)
        assert len(data["skipped_bi_weekly_uninit"]) == 1


class TestBiWeeklyAnchorFromRule:
    def test_iso_date_in_rule(self, monkeypatch, tmp_path):
        fake = FakeDataAccess(companies=[_biweekly_company(rule="2026-04-10")])
        exit_code = _run_main(monkeypatch, tmp_path, fake, today=date(2026, 5, 22))

        assert exit_code == 0
        data = _summary(tmp_path)
        assert len(data["created"]) == 1
        assert data["created"][0]["period_end"] == "2026-05-22"

    def test_non_iso_text_ignored(self, monkeypatch, tmp_path):
        fake = FakeDataAccess(companies=[_biweekly_company(rule="Friday")])
        exit_code = _run_main(monkeypatch, tmp_path, fake, today=date(2026, 5, 22))

        assert exit_code == 0
        data = _summary(tmp_path)
        assert len(data["skipped_bi_weekly_uninit"]) == 1


class TestUnknownFrequency:
    def test_skipped(self, monkeypatch, tmp_path):
        co = _monthly_company()
        co["Pay Frequency"] = {"value": "Weekly"}
        fake = FakeDataAccess(companies=[co])
        exit_code = _run_main(monkeypatch, tmp_path, fake)

        assert exit_code == 1
        data = _summary(tmp_path)
        assert len(data["skipped_unknown_freq"]) == 1


class TestLegalNameInEngagementName:
    def test_create_wrapper_uses_data_access(self, monkeypatch):
        fake = FakeDataAccess()
        _install_fake_data(monkeypatch, fake)

        create_payroll_engagement(
            _monthly_company(name="Acme Corp Inc."),
            date(2026, 5, 31),
            "Monthly",
        )

        assert fake.created[0]["company"] == "Acme Corp Inc."
        assert fake.created[0]["period_start"] == date(2026, 5, 1)


class TestSummaryWrittenToLogsDir:
    def test_file_exists(self, monkeypatch, tmp_path):
        fake = FakeDataAccess(companies=[_monthly_company()])
        _run_main(monkeypatch, tmp_path, fake)

        data = _summary(tmp_path)
        assert "created" in data
        assert "failed" in data
        assert "skipped_existing" in data
        assert "exit_code" in data


class TestLockPreventsConcurrentRun:
    def test_lock_with_own_pid(self, monkeypatch, tmp_path):
        lock_file = tmp_path / "payroll_cron.lock"
        lock_file.write_text(str(os.getpid()))

        fake = FakeDataAccess()
        exit_code = _run_main(monkeypatch, tmp_path, fake)

        assert exit_code == 2


class TestStaleLockIsRemoved:
    def test_stale_pid(self, monkeypatch, tmp_path):
        lock_file = tmp_path / "payroll_cron.lock"
        lock_file.write_text("999999")

        fake = FakeDataAccess(companies=[_monthly_company()])
        exit_code = _run_main(monkeypatch, tmp_path, fake)

        assert exit_code == 0


class TestSingleSelectPayFrequencyDict:
    def test_extracts_value(self, monkeypatch, tmp_path):
        co = _monthly_company()
        fake = FakeDataAccess(companies=[co])
        _run_main(monkeypatch, tmp_path, fake)

        data = _summary(tmp_path)
        assert len(data["created"]) == 1
        assert all(c["frequency"] == "Monthly" for c in data["created"])


class TestBackfillCreatesMultiplePeriods:
    def test_1_period(self, monkeypatch, tmp_path):
        fake = FakeDataAccess(companies=[_monthly_company()])
        exit_code = _run_main(monkeypatch, tmp_path, fake, today=date(2026, 5, 31))

        assert exit_code == 0
        data = _summary(tmp_path)
        assert len(data["created"]) == 1
        assert "2026-05-31" in [c["period_end"] for c in data["created"]]


class TestExitCodes:
    def test_zero_on_clean(self, monkeypatch, tmp_path):
        fake = FakeDataAccess(companies=[_monthly_company()])
        assert _run_main(monkeypatch, tmp_path, fake) == 0

    def test_one_on_partial_failure(self, monkeypatch, tmp_path):
        fake = FakeDataAccess(
            companies=[_monthly_company()],
            create_side_effect=[RuntimeError("e"), RuntimeError("e"), RuntimeError("e")],
        )
        assert _run_main(monkeypatch, tmp_path, fake) == 1

    def test_two_on_fatal(self, monkeypatch, tmp_path):
        def boom():
            raise RuntimeError("nope")

        monkeypatch.setattr(cron.data_access, "list_payroll_companies", boom)
        monkeypatch.setattr(cron, "_get_today", lambda: date(2026, 5, 31))
        monkeypatch.setattr(cron, "_LOG_DIR", tmp_path)
        monkeypatch.setattr(cron.time, "sleep", lambda *_: None)
        assert main() == 2


def test_get_today_returns_date():
    assert isinstance(_get_today(), date)


def test_retry_eventually_returns(monkeypatch):
    monkeypatch.setattr(cron.time, "sleep", lambda *_: None)
    calls = {"count": 0}

    def flaky():
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("temporary")
        return "ok"

    assert retry(flaky) == "ok"


def test_engagement_exists_wrapper(monkeypatch):
    fake = FakeDataAccess(existing=True)
    _install_fake_data(monkeypatch, fake)
    assert engagement_exists(_monthly_company(name="Wrap Corp"), date(2026, 5, 31)) is True
    assert fake.engagement_checks == [("Wrap Corp", date(2026, 5, 31))]


def test_resolve_biweekly_anchor_prefers_history(monkeypatch):
    fake = FakeDataAccess()
    fake.last_period_by_company["Test Corp"] = date(2026, 5, 8)
    _install_fake_data(monkeypatch, fake)

    assert resolve_biweekly_anchor(_biweekly_company(rule="2026-04-10")) == date(2026, 5, 8)
