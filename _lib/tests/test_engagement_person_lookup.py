"""Tests for the read-only Engagement -> Company -> Person lookup (D4: does
not touch Engagements.Company/Person text fields, see _doc/cross_validation_*)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import data_access


def test_resolves_through_existing_text_join(monkeypatch):
    monkeypatch.setattr(data_access, "list_engagements", lambda: [
        {"Id": 1, "Company": "ABC Ltd."},
    ])
    monkeypatch.setattr(data_access, "list_companies_by_legal_name", lambda name: (
        [{"Id": 42}] if name == "ABC Ltd." else []
    ))
    monkeypatch.setattr(data_access, "list_people_for_company", lambda company_id: (
        [{"Id": 7, "Full Name": "Jane Doe"}] if company_id == 42 else []
    ))

    result = data_access.get_person_for_engagement(1)
    assert result == [{"Id": 7, "Full Name": "Jane Doe"}]


def test_engagement_not_found_returns_empty():
    data_access.list_engagements = lambda: []
    assert data_access.get_person_for_engagement(999) == []


def test_empty_company_name_returns_empty():
    data_access.list_engagements = lambda: [{"Id": 1, "Company": ""}]
    assert data_access.get_person_for_engagement(1) == []


def test_ambiguous_company_match_returns_empty():
    data_access.list_engagements = lambda: [{"Id": 1, "Company": "Dup Ltd."}]
    data_access.list_companies_by_legal_name = lambda name: [{"Id": 1}, {"Id": 2}]
    assert data_access.get_person_for_engagement(1) == []


def test_list_people_for_company_returns_empty_without_field_id(monkeypatch):
    monkeypatch.setattr(data_access.settings, "NOCODB_COMPANY_OWNERS_LINK_FIELD_ID", "")
    assert data_access.list_people_for_company(1) == []
