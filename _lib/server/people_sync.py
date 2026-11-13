"""
server/people_sync.py -- Safe People sync classification helpers.

These helpers decide whether owner data is ready for human review. They do not
create or link People records by themselves.
"""

from dataclasses import dataclass
import re


STATUS_NOT_READY = "Not Ready"
STATUS_READY_FOR_REVIEW = "Ready for Review"
STATUS_BLOCKED = "Blocked"


@dataclass(frozen=True)
class OwnerCandidate:
    """Owner details extracted from a Companies row."""

    role: str
    full_name: str
    email: str


def normalize_text(value) -> str:
    return " ".join(str(value or "").split())


def normalize_email(value) -> str:
    return normalize_text(value).lower()


def looks_like_email(value: str) -> bool:
    """Small email sanity check; avoids pretending to validate deliverability."""
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value or ""))


def extract_owner_candidates(company_row: dict) -> list[OwnerCandidate]:
    """Extract primary and secondary owner candidates from a Companies row."""
    candidates = []
    primary_name = normalize_text(company_row.get("Owner Name"))
    primary_email = normalize_email(company_row.get("Owner Email"))
    if primary_name or primary_email:
        candidates.append(OwnerCandidate("primary_owner", primary_name, primary_email))

    secondary_name = normalize_text(company_row.get("2nd Owner Name"))
    secondary_email = normalize_email(company_row.get("2nd Owner Email"))
    if secondary_name or secondary_email:
        candidates.append(OwnerCandidate("secondary_owner", secondary_name, secondary_email))

    return candidates


def classify_people_sync(company_row: dict, people_rows: list[dict]) -> dict:
    """
    Classify Companies owner data for semi-automatic People review.

    Returns fields suitable for Companies review status, not People creation.
    """
    candidates = extract_owner_candidates(company_row)
    if not candidates:
        return {
            "People Sync Status": STATUS_NOT_READY,
            "People Sync Reason": "No owner name/email entered",
            "candidates": [],
        }

    people_by_email = {}
    for person in people_rows:
        email = normalize_email(person.get("Email"))
        if email:
            people_by_email.setdefault(email, []).append(person)

    reasons = []
    ready = []
    blocked = []

    for candidate in candidates:
        if not candidate.full_name or not candidate.email:
            blocked.append(candidate)
            reasons.append(f"{candidate.role}: missing name or email")
            continue
        if not looks_like_email(candidate.email):
            blocked.append(candidate)
            reasons.append(f"{candidate.role}: invalid email")
            continue

        matches = people_by_email.get(candidate.email, [])
        if len(matches) > 1:
            blocked.append(candidate)
            reasons.append(f"{candidate.role}: duplicate People email match")
        else:
            ready.append(candidate)
            if len(matches) == 1:
                reasons.append(f"{candidate.role}: unique People email match")
            else:
                reasons.append(f"{candidate.role}: new People candidate")

    status = STATUS_READY_FOR_REVIEW if ready and not blocked else STATUS_BLOCKED
    return {
        "People Sync Status": status,
        "People Sync Reason": "; ".join(reasons),
        "candidates": [candidate.__dict__ for candidate in candidates],
    }
