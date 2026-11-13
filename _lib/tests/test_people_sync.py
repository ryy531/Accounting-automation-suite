"""Tests for safe People sync classification."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.people_sync import (
    STATUS_BLOCKED,
    STATUS_NOT_READY,
    STATUS_READY_FOR_REVIEW,
    classify_people_sync,
)


def test_no_owner_data_not_ready():
    result = classify_people_sync({}, [])
    assert result["People Sync Status"] == STATUS_NOT_READY


def test_new_owner_candidate_ready_for_review():
    result = classify_people_sync(
        {"Owner Name": "Jane Doe", "Owner Email": "Jane@Example.com"},
        [],
    )
    assert result["People Sync Status"] == STATUS_READY_FOR_REVIEW
    assert "new People candidate" in result["People Sync Reason"]


def test_existing_unique_email_ready_for_review():
    result = classify_people_sync(
        {"Owner Name": "Jane Doe", "Owner Email": "jane@example.com"},
        [{"Full Name": "Jane Doe", "Email": "jane@example.com"}],
    )
    assert result["People Sync Status"] == STATUS_READY_FOR_REVIEW
    assert "unique People email match" in result["People Sync Reason"]


def test_duplicate_email_blocked():
    result = classify_people_sync(
        {"Owner Name": "Jane Doe", "Owner Email": "jane@example.com"},
        [
            {"Full Name": "Jane Doe", "Email": "jane@example.com"},
            {"Full Name": "Jane D.", "Email": "jane@example.com"},
        ],
    )
    assert result["People Sync Status"] == STATUS_BLOCKED
    assert "duplicate People email match" in result["People Sync Reason"]


def test_missing_email_blocked():
    result = classify_people_sync({"Owner Name": "Jane Doe"}, [])
    assert result["People Sync Status"] == STATUS_BLOCKED
    assert "missing name or email" in result["People Sync Reason"]
