"""
server/people_bridging.py -- People-Company topology bridging business logic.

Step 1-pre (dedup scan, read-only) and Step 1d (backfill matching) for the
People <-> Companies relation bridging project (see
_doc/cross_validation_*_2026-06-23*.md for the design discussion).

Calls data_access only -- never imports connectors.nocodb_client directly
(AGENTS.md red line: business code must go through data_access.py).

No auto-merge anywhere in this file. Dedup scan output is suggestions for
Riley/Alex; backfill matching returns "needs_review" rather than guessing
whenever more than one candidate is plausible.
"""

import csv
import logging
import re
from collections import defaultdict

import data_access

logger = logging.getLogger(__name__)

_COMPANY_SUFFIX_RE = re.compile(r"\b(ltd|inc|corp|corporation|limited|llc|co)\.?\s*$", re.IGNORECASE)
_NON_WORD_RE = re.compile(r"[^\w\s]")
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_name(name: str | None) -> str:
    """Trim + collapse whitespace + lowercase. No pinyin/simplified-traditional
    conversion -- under-report duplicates rather than risk false merges."""
    if not name:
        return ""
    return _WHITESPACE_RE.sub(" ", name.strip()).lower()


def _normalize_company_token(name: str | None) -> str:
    """Strip Ltd/Inc/Corp suffixes + punctuation for full-word company matching."""
    if not name:
        return ""
    n = name.strip().lower()
    n = _COMPANY_SUFFIX_RE.sub("", n).strip()
    n = _NON_WORD_RE.sub("", n)
    n = _WHITESPACE_RE.sub(" ", n)
    return n.strip()


def _full_word_company_match(company_name: str | None, blob_text: str | None) -> bool:
    """Normalized full-word match (not substring) of a company name inside a
    free-text blob. round3 Q17: "ABC" must not match "ABCD Holdings Ltd."."""
    token = _normalize_company_token(company_name)
    if not token:
        return False
    blob_norm = _normalize_company_token(blob_text)
    if not blob_norm:
        return False
    return bool(re.search(rf"(?<!\w){re.escape(token)}(?!\w)", blob_norm))


# ── Step 1-pre: dedup scan (read-only, no writes) ────────────────────────────

def scan_people_duplicates() -> list[dict]:
    """
    Cluster People rows by Email (high confidence) then normalized Full Name
    (medium confidence, only for rows with no Email). Returns a list of
    {"confidence", "reason", "rows"} dicts. Never merges -- output only.
    """
    people = data_access.list_people()
    clusters = []

    by_email = defaultdict(list)
    for p in people:
        email = (p.get("Email") or "").strip().lower()
        if email:
            by_email[email].append(p)
    for email, rows in by_email.items():
        if len(rows) > 1:
            clusters.append({"confidence": "high", "reason": f"same Email: {email}", "rows": rows})

    already_flagged_ids = {row["Id"] for rows in by_email.values() if len(rows) > 1 for row in rows}
    by_name = defaultdict(list)
    for p in people:
        if p.get("Id") in already_flagged_ids:
            continue
        if (p.get("Email") or "").strip():
            continue  # has a unique email -- not a name-based dup candidate
        name = _normalize_name(p.get("Full Name"))
        if name:
            by_name[name].append(p)
    for name, rows in by_name.items():
        if len(rows) > 1:
            clusters.append({"confidence": "medium", "reason": f"same normalized name: {name}", "rows": rows})

    return clusters


def scan_people_company_gaps() -> list[dict]:
    """
    Cross-check Companies.Owner Name/Email/2nd Owner Name against People.
    Returns a list of {"company_id", "company_name", "owner_name",
    "owner_email", "role"} for owners with no confident matching People row.
    """
    companies = data_access.list_companies()
    people = data_access.list_people()

    people_by_email = {
        (p.get("Email") or "").strip().lower(): p for p in people if p.get("Email")
    }
    people_by_name = defaultdict(list)
    for p in people:
        name = _normalize_name(p.get("Full Name"))
        if name:
            people_by_name[name].append(p)

    missing = []
    for c in companies:
        for name_field, email_field, role in (
            ("Owner Name", "Owner Email", "primary_owner"),
            ("2nd Owner Name", None, "secondary_owner"),
        ):
            name = (c.get(name_field) or "").strip()
            if not name:
                continue
            email = (c.get(email_field) or "").strip().lower() if email_field else ""

            matched = people_by_email.get(email) if email else None
            if not matched:
                candidates = people_by_name.get(_normalize_name(name), [])
                matched = candidates[0] if len(candidates) == 1 else None

            if not matched:
                missing.append({
                    "company_id": c.get("Id"),
                    "company_name": c.get("Legal Name"),
                    "owner_name": name,
                    "owner_email": email,
                    "role": role,
                })

    return missing


def write_dedup_report(path: str) -> dict:
    """
    Write the Step 1-pre report to `path` (CSV). Caller is responsible for
    naming it duplicate_review_suggestions_<date>.csv per the round3
    anti-misuse convention. Read-only: this function does not write to
    NocoDB or Events.
    """
    dup_clusters = scan_people_duplicates()
    people_missing = scan_people_company_gaps()

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "本报告不构成合并指令 / This report is suggestions only.",
            "所有合并需 Riley/Alex 人工确认 / All merges require human confirmation.",
        ])
        writer.writerow([])
        writer.writerow(["-- People self-duplicates --"])
        writer.writerow(["confidence", "reason", "people_id", "full_name", "email", "phone",
                          "related_companies", "notes"])
        for cluster in dup_clusters:
            for row in cluster["rows"]:
                writer.writerow([
                    cluster["confidence"], cluster["reason"], row.get("Id"),
                    row.get("Full Name"), row.get("Email"), row.get("Phone"),
                    row.get("Related Companies"), row.get("Notes"),
                ])
        writer.writerow([])
        writer.writerow(["-- Company owners with no matching People row --"])
        writer.writerow(["company_id", "company_name", "owner_name", "owner_email", "role"])
        for row in people_missing:
            writer.writerow([row["company_id"], row["company_name"], row["owner_name"],
                              row["owner_email"], row["role"]])

    logger.info(
        "Wrote dedup report to %s: %d duplicate clusters, %d company owners missing People match",
        path, len(dup_clusters), len(people_missing),
    )
    return {"dup_clusters": len(dup_clusters), "people_missing": len(people_missing)}


# ── Step 1d: backfill matching (pure decision logic, no writes) ─────────────

def _primary_owner_candidates(company: dict, people: list[dict]) -> tuple[list[dict], str]:
    """
    Shared matching core for match_primary_owner (bulk backfill) and
    resolve_or_create_primary_owner_for_company (live webhook). Email exact
    match takes priority over normalized Name match. Returns
    (candidate_rows, basis) where basis is "email", "name", or "none".
    """
    email = (company.get("Owner Email") or "").strip().lower()
    if email:
        matches = [p for p in people if (p.get("Email") or "").strip().lower() == email]
        if matches:
            return matches, "email"

    name = (company.get("Owner Name") or "").strip()
    if name:
        norm = _normalize_name(name)
        matches = [p for p in people if _normalize_name(p.get("Full Name")) == norm]
        if matches:
            return matches, "name"

    return [], "none"


def match_primary_owner(company: dict, people: list[dict]) -> dict:
    """
    Email exact match (unique) > normalized Name match (unique) > needs_review.
    Returns {"action": "link"|"needs_review"|"skip", "person_id"?, "reason"}.
    """
    name = (company.get("Owner Name") or "").strip()
    email = (company.get("Owner Email") or "").strip()
    if not name and not email:
        return {"action": "skip", "reason": "no primary owner on this company"}

    candidates, basis = _primary_owner_candidates(company, people)

    if basis == "none":
        return {"action": "needs_review", "reason": "0 matches by email or name"}
    if len(candidates) == 1:
        reason = "email match" if basis == "email" else "name match (no/ambiguous email)"
        return {"action": "link", "person_id": candidates[0]["Id"], "reason": reason}
    return {"action": "needs_review", "reason": f"{basis} matched {len(candidates)} People rows"}


def match_secondary_owner(company: dict, people: list[dict]) -> dict:
    """
    Secondary owners have no Email field. Default: needs_review.
    Exception (round3 Q17): unique Name match + normalized full-word match of
    the company's Legal Name inside that Person's Related Companies blob ->
    auto-link (treated as historical confirmation from the 2026-06-05 import).
    This exception only ever narrows the auto-link set -- it cannot widen it,
    so it cannot make matching less safe than "all secondary owners manual".
    """
    name = (company.get("2nd Owner Name") or "").strip()
    if not name:
        return {"action": "skip", "reason": "no secondary owner on this company"}

    norm = _normalize_name(name)
    matches = [p for p in people if _normalize_name(p.get("Full Name")) == norm]
    if len(matches) != 1:
        return {
            "action": "needs_review",
            "reason": f"name matched {len(matches)} People rows (secondary owner has no Email)",
        }

    person = matches[0]
    legal_name = company.get("Legal Name") or ""
    if _full_word_company_match(legal_name, person.get("Related Companies") or ""):
        return {
            "action": "link",
            "person_id": person["Id"],
            "reason": "unique name match + Related Companies full-word match (historical confirmation)",
        }

    return {
        "action": "needs_review",
        "reason": "unique name match but no Related Companies confirmation -- secondary owners default to manual review",
    }


# ── Live webhook resolvers (Company/Person creation, primary owner only) ────
#
# Mode B (AGENTS.md): these write directly, no human gate, but only ever
# touch internal DB rows (no money/email/CRA/external comms), every action
# is Event-logged, and ambiguous matches always go to needs_review instead
# of guessing or creating under uncertainty. Secondary owners are out of
# scope here (round4: no Email field, higher misuse risk -- stay manual).

def resolve_or_create_primary_owner_for_company(
    company: dict, actor: str = "Webhook: Company Created"
) -> dict:
    """
    Live counterpart to match_primary_owner for a newly created Company:
    same Email > Name matching, but creates a new People row when there
    are truly zero candidates (instead of needs_review, which is what the
    bulk backfill does). Ambiguous matches (>1 candidate) still go to
    needs_review -- never guessed, never auto-created under uncertainty.
    """
    company_id = company.get("Id")
    name = (company.get("Owner Name") or "").strip()
    email = (company.get("Owner Email") or "").strip()
    if not name and not email:
        return {"action": "skip", "reason": "no primary owner on this company"}

    people = data_access.list_people()
    candidates, basis = _primary_owner_candidates(company, people)

    if not candidates:
        if not name:
            data_access.create_event(
                engagement_id=f"Companies:{company_id}",
                event_type="Needs Review",
                description=(
                    f"Company-created webhook: Owner Email '{email}' has no match and "
                    "no Owner Name to create a new Person from"
                ),
                actor=actor,
            )
            return {
                "action": "needs_review",
                "reason": "no existing match and no Owner Name to create a new Person from",
            }
        new_person = data_access.create_person(full_name=name, email=email, actor=actor)
        data_access.link_person_to_company(
            company_id, new_person["Id"], role="primary_owner", actor=actor
        )
        return {
            "action": "created_and_linked",
            "person_id": new_person["Id"],
            "reason": "no existing People match -- created new Person",
        }

    if len(candidates) == 1:
        data_access.link_person_to_company(
            company_id, candidates[0]["Id"], role="primary_owner", actor=actor
        )
        return {"action": "linked", "person_id": candidates[0]["Id"], "reason": f"{basis} match"}

    data_access.create_event(
        engagement_id=f"Companies:{company_id}",
        event_type="Needs Review",
        description=(
            f"Company-created webhook: Owner '{name}'/'{email}' matched "
            f"{len(candidates)} People rows by {basis} -- needs manual review"
        ),
        actor=actor,
    )
    return {"action": "needs_review", "reason": f"{basis} matched {len(candidates)} People rows"}


def resolve_or_create_company_for_person(
    person: dict, actor: str = "Webhook: Person Created"
) -> dict:
    """
    Live counterpart for a newly created Person: read the legacy
    People.Company text field, find-or-create the matching Companies row
    (exact normalized Legal Name match), then link via Owners. Does not
    parse Related Companies (free text, can list multiple companies --
    too ambiguous to auto-create from).
    """
    person_id = person.get("Id")
    company_name_raw = (person.get("Company") or "").strip()
    if not company_name_raw:
        return {"action": "skip", "reason": "no Company text field on this Person"}

    companies = data_access.list_companies()
    norm_target = _normalize_name(company_name_raw)
    matches = [c for c in companies if _normalize_name(c.get("Legal Name")) == norm_target]

    if not matches:
        new_company = data_access.create_company(
            legal_name=company_name_raw,
            owner_name=person.get("Full Name") or "",
            owner_email=person.get("Email") or "",
            actor=actor,
        )
        data_access.link_person_to_company(
            new_company["Id"], person_id, role="primary_owner", actor=actor
        )
        return {
            "action": "created_and_linked",
            "company_id": new_company["Id"],
            "reason": "no existing Company match -- created new stub Company",
        }

    if len(matches) == 1:
        data_access.link_person_to_company(
            matches[0]["Id"], person_id, role="primary_owner", actor=actor
        )
        return {"action": "linked", "company_id": matches[0]["Id"], "reason": "legal name match"}

    data_access.create_event(
        engagement_id=f"People:{person_id}",
        event_type="Needs Review",
        description=(
            f"Person-created webhook: Company text '{company_name_raw}' matched "
            f"{len(matches)} Companies rows -- needs manual review"
        ),
        actor=actor,
    )
    return {"action": "needs_review", "reason": f"name matched {len(matches)} Companies rows"}


def write_backfill_preview_report(path: str) -> dict:
    """
    Read-only preview of what a real backfill run would do: for every
    Company, run match_primary_owner / match_secondary_owner against the
    current People table and write the decision (link / needs_review /
    skip) to a CSV. Does not call data_access.link_person_to_company() and
    does not write anything to NocoDB or Events.
    """
    companies = data_access.list_companies()
    people = data_access.list_people()

    counts = {"primary_owner": defaultdict(int), "secondary_owner": defaultdict(int)}
    rows = []
    for c in companies:
        for role, matcher in (
            ("primary_owner", match_primary_owner),
            ("secondary_owner", match_secondary_owner),
        ):
            result = matcher(c, people)
            counts[role][result["action"]] += 1
            if result["action"] == "skip":
                continue
            rows.append({
                "company_id": c.get("Id"),
                "legal_name": c.get("Legal Name"),
                "role": role,
                "action": result["action"],
                "person_id": result.get("person_id", ""),
                "reason": result["reason"],
            })

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "本报告是只读预览,未写入任何数据 / Read-only preview, nothing written to NocoDB.",
        ])
        writer.writerow([])
        writer.writerow(["company_id", "legal_name", "role", "action", "person_id", "reason"])
        for row in rows:
            writer.writerow([row["company_id"], row["legal_name"], row["role"],
                              row["action"], row["person_id"], row["reason"]])

    summary = {
        "primary_owner": dict(counts["primary_owner"]),
        "secondary_owner": dict(counts["secondary_owner"]),
    }
    logger.info("Wrote backfill preview to %s: %s", path, summary)
    return summary
