"""Tests for safe read retries in the NocoDB connector."""

import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from connectors import nocodb_client


class FakeResponse:
    def __init__(self, status_code, payload, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


def test_get_retries_timeout_then_returns_json(monkeypatch):
    client = nocodb_client.NocoDBClient()
    results = iter([
        requests.Timeout("slow"),
        FakeResponse(200, {"list": [1]}),
    ])

    def fake_get(*args, **kwargs):
        result = next(results)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(client._session, "get", fake_get)
    monkeypatch.setattr(nocodb_client.time, "sleep", lambda seconds: None)

    assert client._get("http://example.test") == {"list": [1]}


def test_get_retries_sqlite_busy_then_returns_json(monkeypatch):
    client = nocodb_client.NocoDBClient()
    results = iter([
        FakeResponse(
            400,
            {},
            '{"code":"SQLITE_BUSY","message":"database is locked"}',
        ),
        FakeResponse(200, {"ok": True}),
    ])

    monkeypatch.setattr(
        client._session,
        "get",
        lambda *args, **kwargs: next(results),
    )
    monkeypatch.setattr(nocodb_client.time, "sleep", lambda seconds: None)

    assert client._get("http://example.test") == {"ok": True}
