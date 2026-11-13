"""Tests for People-Company bridging matching/dedup logic (no NocoDB calls)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server.people_bridging as people_bridging
from server.people_bridging import (
    match_primary_owner,
    match_secondary_owner,
    resolve_or_create_company_for_person,
    resolve_or_create_primary_owner_for_company,
    scan_people_company_gaps,
    scan_people_duplicates,
)


class _RecordingDataAccess:
    """Stand-in for data_access capturing calls instead of hitting NocoDB."""

    def __init__(self, people=None, companies=None):
        self._people = people or []
        self._companies = companies or []
        self.created_persons = []
        self.created_companies = []
        self.links = []
        self.events = []
        self._next_person_id = 1000
        self._next_company_id = 2000

    def list_people(self):
        return self._people

    def list_companies(self):
        return self._companies

    def create_person(self, full_name, email="", actor="System"):
        self._next_person_id += 1
        person = {"Id": self._next_person_id, "Full Name": full_name, "Email": email}
        self.created_persons.append(person)
        return person

    def create_company(self, legal_name, owner_name="", owner_email="", actor="System"):
        self._next_company_id += 1
        company = {"Id": self._next_company_id, "Legal Name": legal_name,
                   "Owner Name": owner_name, "Owner Email": owner_email}
        self.created_companies.append(company)
        return company

    def link_person_to_company(self, company_id, person_id, role="", actor="System"):
        self.links.append((company_id, person_id, role))
        return {"action": "linked", "company_id": company_id, "person_id": person_id}

    def create_event(self, **kwargs):
        self.events.append(kwargs)
        return {"stubbed": True}


# ── match_primary_owner ──────────────────────────────────────────────────────

def test_primary_owner_email_unique_match_links():
    company = {"Owner Name": "Jane Doe", "Owner Email": "jane@example.com"}
    people = [{"Id": 1, "Full Name": "Jane Doe", "Email": "jane@example.com"}]
    result = match_primary_owner(company, people)
    assert result == {"action": "link", "person_id": 1, "reason": "email match"}


def test_primary_owner_email_ambiguous_needs_review():
    company = {"Owner Name": "Jane Doe", "Owner Email": "shared@example.com"}
    people = [
        {"Id": 1, "Full Name": "Jane Doe", "Email": "shared@example.com"},
        {"Id": 2, "Full Name": "John Smith", "Email": "shared@example.com"},
    ]
    result = match_primary_owner(company, people)
    assert result["action"] == "needs_review"


def test_primary_owner_no_email_falls_back_to_name():
    company = {"Owner Name": "Jane Doe", "Owner Email": ""}
    people = [{"Id": 1, "Full Name": "  jane   doe ", "Email": ""}]
    result = match_primary_owner(company, people)
    assert result == {"action": "link", "person_id": 1, "reason": "name match (no/ambiguous email)"}


def test_primary_owner_zero_match_needs_review():
    company = {"Owner Name": "Nobody Here", "Owner Email": "nobody@example.com"}
    people = [{"Id": 1, "Full Name": "Someone Else", "Email": "someone@example.com"}]
    result = match_primary_owner(company, people)
    assert result["action"] == "needs_review"


def test_primary_owner_skip_when_empty():
    result = match_primary_owner({"Owner Name": "", "Owner Email": ""}, [])
    assert result["action"] == "skip"


# ── match_secondary_owner ────────────────────────────────────────────────────

def test_secondary_owner_skip_when_empty():
    result = match_secondary_owner({"2nd Owner Name": ""}, [])
    assert result["action"] == "skip"


def test_secondary_owner_unique_name_no_blob_needs_review():
    company = {"2nd Owner Name": "Bob Lee", "Legal Name": "ABC Holdings Ltd."}
    people = [{"Id": 5, "Full Name": "Bob Lee", "Related Companies": ""}]
    result = match_secondary_owner(company, people)
    assert result["action"] == "needs_review"


def test_secondary_owner_full_word_blob_match_links():
    company = {"2nd Owner Name": "Bob Lee", "Legal Name": "ABC Holdings Ltd."}
    people = [{
        "Id": 5, "Full Name": "Bob Lee",
        "Related Companies": "Owns ABC Holdings and also XYZ Inc.",
    }]
    result = match_secondary_owner(company, people)
    assert result == {
        "action": "link", "person_id": 5,
        "reason": "unique name match + Related Companies full-word match (historical confirmation)",
    }


def test_secondary_owner_substring_false_positive_rejected():
    """round3 Q17: 'ABC' must not match 'ABCD Holdings Ltd.' (no full-word match)."""
    company = {"2nd Owner Name": "Bob Lee", "Legal Name": "ABC"}
    people = [{
        "Id": 5, "Full Name": "Bob Lee",
        "Related Companies": "Owns ABCD Holdings Ltd.",
    }]
    result = match_secondary_owner(company, people)
    assert result["action"] == "needs_review"


def test_secondary_owner_ambiguous_name_needs_review():
    company = {"2nd Owner Name": "Bob Lee", "Legal Name": "ABC Ltd."}
    people = [
        {"Id": 5, "Full Name": "Bob Lee", "Related Companies": "ABC Ltd."},
        {"Id": 6, "Full Name": "Bob Lee", "Related Companies": ""},
    ]
    result = match_secondary_owner(company, people)
    assert result["action"] == "needs_review"


# ── scan_people_duplicates / scan_people_company_gaps (monkeypatched data_access) ──

def test_scan_people_duplicates_by_email(monkeypatch):
    monkeypatch.setattr(people_bridging.data_access, "list_people", lambda: [
        {"Id": 1, "Full Name": "Jane Doe", "Email": "jane@example.com"},
        {"Id": 2, "Full Name": "J. Doe", "Email": "jane@example.com"},
        {"Id": 3, "Full Name": "Unrelated", "Email": "other@example.com"},
    ])
    clusters = scan_people_duplicates()
    assert len(clusters) == 1
    assert clusters[0]["confidence"] == "high"
    assert {row["Id"] for row in clusters[0]["rows"]} == {1, 2}


def test_scan_people_duplicates_by_name_only_when_no_email():
    people_bridging.data_access.list_people = lambda: [
        {"Id": 1, "Full Name": "Jane Doe", "Email": ""},
        {"Id": 2, "Full Name": " jane  doe ", "Email": ""},
        {"Id": 3, "Full Name": "Jane Doe", "Email": "jane@example.com"},
    ]
    clusters = scan_people_duplicates()
    assert len(clusters) == 1
    assert clusters[0]["confidence"] == "medium"
    assert {row["Id"] for row in clusters[0]["rows"]} == {1, 2}


def test_scan_people_company_gaps_flags_missing_owner():
    people_bridging.data_access.list_companies = lambda: [
        {"Id": 10, "Legal Name": "ABC Ltd.", "Owner Name": "Nobody Here",
         "Owner Email": "nobody@example.com", "2nd Owner Name": ""},
    ]
    people_bridging.data_access.list_people = lambda: [
        {"Id": 1, "Full Name": "Someone Else", "Email": "someone@example.com"},
    ]
    missing = scan_people_company_gaps()
    assert len(missing) == 1
    assert missing[0]["company_id"] == 10
    assert missing[0]["role"] == "primary_owner"


# ── resolve_or_create_primary_owner_for_company ──────────────────────────────

def test_company_created_unique_match_links_no_create(monkeypatch):
    fake = _RecordingDataAccess(people=[{"Id": 1, "Full Name": "Jane Doe", "Email": "jane@example.com"}])
    monkeypatch.setattr(people_bridging, "data_access", fake)

    result = resolve_or_create_primary_owner_for_company(
        {"Id": 50, "Owner Name": "Jane Doe", "Owner Email": "jane@example.com"}
    )
    assert result == {"action": "linked", "person_id": 1, "reason": "email match"}
    assert fake.links == [(50, 1, "primary_owner")]
    assert fake.created_persons == []


def test_company_created_zero_match_creates_and_links(monkeypatch):
    fake = _RecordingDataAccess(people=[])
    monkeypatch.setattr(people_bridging, "data_access", fake)

    result = resolve_or_create_primary_owner_for_company(
        {"Id": 51, "Owner Name": "New Owner", "Owner Email": "new@example.com"}
    )
    assert result["action"] == "created_and_linked"
    new_id = result["person_id"]
    assert fake.created_persons == [{"Id": new_id, "Full Name": "New Owner", "Email": "new@example.com"}]
    assert fake.links == [(51, new_id, "primary_owner")]


def test_company_created_ambiguous_match_needs_review_no_create(monkeypatch):
    fake = _RecordingDataAccess(people=[
        {"Id": 1, "Full Name": "Jane Doe", "Email": "shared@example.com"},
        {"Id": 2, "Full Name": "John Smith", "Email": "shared@example.com"},
    ])
    monkeypatch.setattr(people_bridging, "data_access", fake)

    result = resolve_or_create_primary_owner_for_company(
        {"Id": 52, "Owner Name": "Jane Doe", "Owner Email": "shared@example.com"}
    )
    assert result["action"] == "needs_review"
    assert fake.created_persons == []
    assert fake.links == []
    assert len(fake.events) == 1
    assert fake.events[0]["event_type"] == "Needs Review"


def test_company_created_email_only_no_name_needs_review_no_create(monkeypatch):
    fake = _RecordingDataAccess(people=[])
    monkeypatch.setattr(people_bridging, "data_access", fake)

    result = resolve_or_create_primary_owner_for_company(
        {"Id": 53, "Owner Name": "", "Owner Email": "lonely@example.com"}
    )
    assert result["action"] == "needs_review"
    assert fake.created_persons == []


def test_company_created_no_owner_info_skips(monkeypatch):
    fake = _RecordingDataAccess()
    monkeypatch.setattr(people_bridging, "data_access", fake)

    result = resolve_or_create_primary_owner_for_company({"Id": 54, "Owner Name": "", "Owner Email": ""})
    assert result["action"] == "skip"
    assert fake.created_persons == []
    assert fake.events == []


# ── resolve_or_create_company_for_person ─────────────────────────────────────

def test_person_created_unique_match_links_no_create(monkeypatch):
    fake = _RecordingDataAccess(companies=[{"Id": 10, "Legal Name": "ABC Ltd."}])
    monkeypatch.setattr(people_bridging, "data_access", fake)

    result = resolve_or_create_company_for_person({"Id": 7, "Full Name": "Jane Doe", "Company": "ABC Ltd."})
    assert result == {"action": "linked", "company_id": 10, "reason": "legal name match"}
    assert fake.links == [(10, 7, "primary_owner")]
    assert fake.created_companies == []


def test_person_created_zero_match_creates_stub_company(monkeypatch):
    fake = _RecordingDataAccess(companies=[])
    monkeypatch.setattr(people_bridging, "data_access", fake)

    result = resolve_or_create_company_for_person({
        "Id": 8, "Full Name": "New Owner", "Email": "new@example.com", "Company": "New Co Ltd.",
    })
    assert result["action"] == "created_and_linked"
    new_id = result["company_id"]
    assert fake.created_companies == [{
        "Id": new_id, "Legal Name": "New Co Ltd.",
        "Owner Name": "New Owner", "Owner Email": "new@example.com",
    }]
    assert fake.links == [(new_id, 8, "primary_owner")]


def test_person_created_ambiguous_match_needs_review_no_create(monkeypatch):
    fake = _RecordingDataAccess(companies=[
        {"Id": 10, "Legal Name": "Dup Ltd."},
        {"Id": 11, "Legal Name": "Dup Ltd."},
    ])
    monkeypatch.setattr(people_bridging, "data_access", fake)

    result = resolve_or_create_company_for_person({"Id": 9, "Full Name": "Bob", "Company": "Dup Ltd."})
    assert result["action"] == "needs_review"
    assert fake.created_companies == []
    assert fake.links == []
    assert len(fake.events) == 1
    assert fake.events[0]["event_type"] == "Needs Review"


def test_person_created_empty_company_field_skips(monkeypatch):
    fake = _RecordingDataAccess()
    monkeypatch.setattr(people_bridging, "data_access", fake)

    result = resolve_or_create_company_for_person({"Id": 12, "Full Name": "No Company", "Company": ""})
    assert result["action"] == "skip"
    assert fake.created_companies == []
