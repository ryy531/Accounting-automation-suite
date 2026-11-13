# -*- coding: utf-8 -*-
"""
nocodb_source.py -- read client records from NocoDB for the Tax Reminder System.

Returns records in the EXACT same shape as tax_reminder.read_master_data(), so
it is a drop-in replacement for the legacy encrypted Master Excel reader. All
business data is read through _lib/data_access.py per the platform contract;
this module never calls the NocoDB REST API directly.

Field mapping (NocoDB Companies -> reminder record):
    Legal Name    <- Companies.Legal Name
    Owner Name    <- Companies.Owner Name
    Owner Email   <- Companies.Owner Email
    Language      <- Companies.Language        (SingleSelect)
    Client Type   <- Companies.Client Type     (SingleSelect: Annual / Monthly)
    GST Frequency <- Companies.GST Frequency   (SingleSelect)
    PST Frequency <- Companies.PST Frequency   (SingleSelect)
    T2 YE         <- Companies.T2 YE
    Assigned      <- Engagements.Assigned To   (aggregated per company; the
                     Assigned owner lives on Engagements, not Companies, per the
                     2026-06-29 decision)
"""
import sys
from pathlib import Path

# Business scripts reach the platform layer the same way WeeklyTodoReport does:
# add _lib to sys.path, then import the data_access contract.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "_lib"))

# The 9 keys every reminder record must carry, matching read_master_data().
RECORD_KEYS = (
    "Legal Name", "Owner Name", "Owner Email", "Language",
    "Assigned", "Client Type", "GST Frequency", "PST Frequency", "T2 YE",
)


def _flat(value) -> str:
    """Normalize a NocoDB cell (str / SingleSelect dict / None) to a stripped str."""
    if isinstance(value, dict):
        value = value.get("value") or value.get("title") or ""
    return " ".join(str(value or "").split())


def load_from_nocodb(active_only: bool = True) -> tuple[list[dict], frozenset[str]]:
    """
    Read Companies (+ Engagements for Assigned) from NocoDB and return
    (active_records, inactive_keys) where inactive_keys is a frozenset of
    upper-cased Legal Names whose `client status` is "inactive".

    active_only: exclude inactive Companies from active_records (default True).

    Raises NocoDBApiError (from the connector) if NocoDB is unreachable; the
    caller is expected to catch and fall back / report.
    """
    from data_access import list_companies, list_engagements

    # Assigned lives on Engagements (Assigned To); aggregate distinct names
    # per company so a company with multiple engagements shows all assignees.
    assigned_by_company: dict[str, set] = {}
    for e in list_engagements():
        company = _flat(e.get("Company")).upper()
        who = _flat(e.get("Assigned To"))
        if company and who:
            assigned_by_company.setdefault(company, set()).add(who)

    records: list[dict] = []
    inactive_keys: set[str] = set()
    seen: set[str] = set()

    for c in list_companies():
        legal = _flat(c.get("Legal Name"))
        if not legal:
            continue
        if _flat(c.get("client status")).lower() == "inactive":
            inactive_keys.add(legal.upper())
            if active_only:
                continue
        key = legal.upper()
        if key in seen:
            continue
        seen.add(key)

        records.append({
            "Legal Name":    legal,
            "Owner Name":    _flat(c.get("Owner Name")),
            "Owner Email":   _flat(c.get("Owner Email")),
            "Language":      _flat(c.get("Language")),
            "Assigned":      ", ".join(sorted(assigned_by_company.get(key, set()))),
            "Client Type":   _flat(c.get("Client Type")),
            "GST Frequency": _flat(c.get("GST Frequency")),
            "PST Frequency": _flat(c.get("PST Frequency")),
            "T2 YE":         _flat(c.get("T2 YE")),
        })

    # A key seen as active is not considered inactive (handles rare duplicates)
    inactive_keys -= seen
    return records, frozenset(inactive_keys)


if __name__ == "__main__":
    # Manual smoke test: python nocodb_source.py
    recs, inactive = load_from_nocodb()
    print(f"Loaded {len(recs)} company records from NocoDB.")
    if inactive:
        print(f"Excluded {len(inactive)} inactive company record(s).")
    for r in recs[:5]:
        print(" ", r["Legal Name"], "|", r["Client Type"],
              "| GST:", r["GST Frequency"], "| PST:", r["PST Frequency"],
              "| Assigned:", r["Assigned"])
