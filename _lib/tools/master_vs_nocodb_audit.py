"""
tools/master_vs_nocodb_audit.py -- read-only field-level audit comparing the
legacy encrypted Master Client List Excel against live NocoDB Companies +
Engagements rows.

Purpose: produce evidence for whether NocoDB data diverges from the Master
Excel it was migrated from, or whether apparent gaps already existed in the
Master file itself. Never writes to NocoDB or to the Master file.

Companies/Engagements row reads go through data_access.py per _lib/AGENTS.md.
Report goes to _reports/ (gitignored) and contains only Legal Name + the
compared business fields already used by tax_reminder.py / ar_reminder.py /
generate_annual_return.py -- no SIN, no tokens, no webhook destinations.
"""
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import openpyxl
import msoffcrypto

import config.settings as settings
import data_access

ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = ROOT / "_reports"

MASTER_PATHS = [
    r"\\file-server\Shared Folder\4. General - Scan-Notes-Form-PPT\4. Accounting client list\#Business Master Client List.xlsx",
    r"Z:\4. General - Scan-Notes-Form-PPT\4. Accounting client list\#Business Master Client List.xlsx",
]

TARGET_SHEETS = ["master", "Alex's", "mk"]

# Default column headers (sheet "Master"); per-sheet overrides below mirror
# TaxAutomationReminderSystem/tax_reminder.py SHEET_COL_OVERRIDES -- same
# fields that reminder scripts already depend on, nothing new.
DEFAULT_COLS = {
    "Legal Name":  "Legal Name",
    "Owner Name":  "Owner Name",
    "Owner Email": "Owner Email",
    "Language":    "Language",
    "Assigned":    "Assigned",
    "Client Type": "Client Type",
    "GST Frequency": "GST Frequncy",  # typo present in the live Master sheet
    "PST Frequency": "PST Frequency",
    "T2 YE":       "T2 YE",
}

SHEET_COL_OVERRIDES = {
    "Alex's": {
        "Legal Name": "Company Name",
        "Owner Name":  None,
        "Owner Email": None,
        "Language":    None,
        "Assigned":    None,
        "Client Type": None,
        "GST Frequency": None,
        "PST Frequency": "PST Frequency",
        "T2 YE":       "T2 Done Until",
    },
    "mk": {
        "Legal Name": "Company Name",
        "Owner Name":  "Client Name",
        "Owner Email": "Owner Email",
        "Language":    None,
        "Assigned":    None,
        "Client Type": "Client Type",
        "GST Frequency": None,
        "PST Frequency": "PST Frequency",
        "T2 YE":       "T2 Done Until",
    },
}

# NocoDB field each Master column is compared against. Assigned has no
# Companies field (it lives on Engagements per the 2026-06-29 decision with
# Riley), so it is matched against any Engagement row for that company.
NOCODB_COMPANY_FIELD = {
    "Owner Name": "Owner Name",
    "Owner Email": "Owner Email",
    "Language": "Language",
    "Client Type": "Client Type",
    "GST Frequency": "GST Frequency",
    "PST Frequency": "PST Frequency",
    "T2 YE": "T2 YE",
}


def _normalize_name(name) -> str:
    if not name:
        return ""
    return " ".join(str(name).strip().split()).upper()


def _normalize_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        value = value.get("value") or value.get("title") or ""
    return " ".join(str(value).strip().split())


def _resolve_master_path() -> str | None:
    for p in MASTER_PATHS:
        if Path(p).exists():
            return p
    return None


def _decrypt_to_tempfile(path: str, password: str) -> str:
    with open(path, "rb") as f:
        of = msoffcrypto.OfficeFile(f)
        tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
        if of.is_encrypted():
            of.load_key(password=password)
            of.decrypt(tmp)
            tmp.close()
        else:
            tmp.close()
            import shutil
            shutil.copyfile(path, tmp.name)
    return tmp.name


def _find_columns(ws) -> dict[str, int]:
    norm_map = {}
    for cell in next(ws.iter_rows(min_row=1, max_row=1)):
        if cell.value:
            norm_map[" ".join(str(cell.value).strip().lower().split())] = cell.column
    return norm_map


def read_master_records(decrypted_path: str) -> dict[str, dict]:
    """Read master/Alex's/mk sheets. Returns {normalized_legal_name: record}.

    First occurrence wins across sheets, matching the dedupe behavior already
    used by tax_reminder.py / generate_annual_return.py / ar_reminder.py, so
    this audit reflects what the reminder scripts actually see today.
    """
    wb = openpyxl.load_workbook(decrypted_path, read_only=True, data_only=True)
    records: dict[str, dict] = {}

    for sheet_name in wb.sheetnames:
        sn_norm = sheet_name.strip().lower()
        if sn_norm not in TARGET_SHEETS:
            continue

        ws = wb[sheet_name]
        norm_map = _find_columns(ws)
        overrides = SHEET_COL_OVERRIDES.get(sn_norm, {})

        col_idx = {}
        for field, default_header in DEFAULT_COLS.items():
            header = overrides[field] if field in overrides else default_header
            col_idx[field] = (
                None if header is None
                else norm_map.get(" ".join(header.strip().lower().split()))
            )

        legal_idx = col_idx.get("Legal Name")
        if legal_idx is None:
            continue

        for row in ws.iter_rows(min_row=2, values_only=False):
            cell = row[legal_idx - 1] if legal_idx <= len(row) else None
            legal_name = cell.value if cell else None
            key = _normalize_name(legal_name)
            if not key or key in records:
                continue

            rec = {"Legal Name": legal_name, "_sheet": sheet_name}
            for field, idx in col_idx.items():
                if field == "Legal Name" or idx is None:
                    continue
                c = row[idx - 1] if idx <= len(row) else None
                rec[field] = c.value if c else None
            records[key] = rec

    wb.close()
    return records


def build_nocodb_index() -> tuple[dict[str, dict], dict[str, list[str]]]:
    """Returns (companies_by_name, assigned_by_company_name)."""
    companies = data_access.list_companies()
    companies_by_name = {
        _normalize_name(c.get("Legal Name")): c for c in companies if c.get("Legal Name")
    }

    engagements = data_access.list_engagements()
    assigned_by_company: dict[str, set[str]] = {}
    for e in engagements:
        company = _normalize_name(e.get("Company"))
        if not company:
            continue
        assigned = _normalize_value(e.get("Assigned To"))
        if assigned:
            assigned_by_company.setdefault(company, set()).add(assigned)

    return companies_by_name, {k: sorted(v) for k, v in assigned_by_company.items()}


def compare(master_records, companies_by_name, assigned_by_company):
    """Returns (field_rows, master_only_names, nocodb_only_names)."""
    field_rows = []
    master_only = []

    for key, mrec in master_records.items():
        company = companies_by_name.get(key)
        if company is None:
            master_only.append(mrec["Legal Name"])
            continue

        for field, nocodb_field in NOCODB_COMPANY_FIELD.items():
            m_val = _normalize_value(mrec.get(field))
            n_val = _normalize_value(company.get(nocodb_field))
            field_rows.append(_categorize(mrec["Legal Name"], mrec["_sheet"], field, m_val, n_val))

        m_assigned = _normalize_value(mrec.get("Assigned"))
        n_assigned_list = assigned_by_company.get(key, [])
        n_assigned = ", ".join(n_assigned_list)
        field_rows.append(_categorize(
            mrec["Legal Name"], mrec["_sheet"], "Assigned (vs Engagements.Assigned To)",
            m_assigned, n_assigned,
        ))

    nocodb_only = [
        c.get("Legal Name") for key, c in companies_by_name.items()
        if key not in master_records
    ]

    return field_rows, master_only, nocodb_only


# Placeholder strings that appear in the Master file to mean "not applicable".
# NocoDB stores these as blank, which is the same information -- not a gap.
_JUNK_PLACEHOLDERS = {"na", "n/a", "n.a.", "-", "--", "none", "nil", "n.a", "x"}


def _is_blank_or_placeholder(val: str) -> bool:
    return val.strip().lower() in _JUNK_PLACEHOLDERS or not val.strip()


def _categorize(legal_name, sheet, field, m_val, n_val):
    if m_val == n_val:
        category = "match (incl. both blank)" if not m_val else "match"
    elif not m_val and n_val:
        category = "NocoDB has value, Master blank -> updated after migration"
    elif m_val and not n_val:
        if _is_blank_or_placeholder(m_val):
            # Master cell is a 'NA'-style placeholder; NocoDB blank is equivalent.
            category = "Master placeholder (NA) -> NocoDB blank, equivalent (not a gap)"
        else:
            category = "Master has value, NocoDB blank -> migration gap"
    else:
        category = "both have values but differ -> needs review"
    return {
        "Legal Name": legal_name,
        "Master Sheet": sheet,
        "Field": field,
        "Master Value": m_val,
        "NocoDB Value": n_val,
        "Category": category,
    }


def write_report(field_rows, master_only, nocodb_only) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    REPORT_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out_path = REPORT_DIR / f"master_vs_nocodb_audit_{timestamp}.xlsx"

    wb = Workbook()
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="2F5496")

    def write_sheet(ws, headers, rows):
        for ci, h in enumerate(headers, 1):
            c = ws.cell(row=1, column=ci, value=h)
            c.font = header_font
            c.fill = header_fill
        for ri, row in enumerate(rows, 2):
            for ci, h in enumerate(headers, 1):
                ws.cell(row=ri, column=ci, value=row.get(h, ""))
        ws.auto_filter.ref = f"A1:{openpyxl.utils.get_column_letter(len(headers))}{max(len(rows) + 1, 1)}"
        ws.freeze_panes = "A2"

    # Summary
    ws_summary = wb.active
    ws_summary.title = "Summary"
    from collections import Counter
    counts = Counter(r["Category"] for r in field_rows)
    summary_rows = [{"Category": k, "Count": v} for k, v in counts.most_common()]
    summary_rows.append({"Category": "Companies in Master but not in NocoDB", "Count": len(master_only)})
    summary_rows.append({"Category": "Companies in NocoDB but not in Master", "Count": len(nocodb_only)})
    write_sheet(ws_summary, ["Category", "Count"], summary_rows)

    # Field-level discrepancies: real issues only. Excludes clean matches AND
    # the Master 'NA'-placeholder rows (those are equivalent to NocoDB blank,
    # not a migration problem) so this sheet shows only items worth acting on.
    ws_detail = wb.create_sheet("Field Discrepancies")
    detail_rows = [
        r for r in field_rows
        if not r["Category"].startswith("match")
        and "equivalent (not a gap)" not in r["Category"]
    ]
    write_sheet(ws_detail, ["Legal Name", "Master Sheet", "Field", "Master Value", "NocoDB Value", "Category"], detail_rows)

    # Full comparison (including matches), for completeness
    ws_all = wb.create_sheet("All Comparisons")
    write_sheet(ws_all, ["Legal Name", "Master Sheet", "Field", "Master Value", "NocoDB Value", "Category"], field_rows)

    ws_master_only = wb.create_sheet("Master-only companies")
    write_sheet(ws_master_only, ["Legal Name"], [{"Legal Name": n} for n in master_only])

    ws_nocodb_only = wb.create_sheet("NocoDB-only companies")
    write_sheet(ws_nocodb_only, ["Legal Name"], [{"Legal Name": n} for n in nocodb_only])

    wb.save(out_path)
    return out_path


def main():
    password = settings.CLIENT_MASTER_PASSWORD
    if not password:
        print("[ERROR] CLIENT_MASTER_PASSWORD not set in _lib/config/.env")
        sys.exit(1)

    # Optional CLI arg: explicit Master file path (e.g. an UPDATED candidate).
    # Falls back to the known default paths when no arg is given.
    if len(sys.argv) > 1:
        master_path = sys.argv[1]
        if not Path(master_path).exists():
            print(f"[ERROR] Master file not found: {master_path}")
            sys.exit(1)
    else:
        master_path = _resolve_master_path()
        if not master_path:
            print("[ERROR] Master Client List not found on any known path.")
            sys.exit(1)

    print(f"[1/4] Decrypting {master_path} ...")
    tmp_path = _decrypt_to_tempfile(master_path, password)

    print("[2/4] Reading Master/Alex's/MK sheets ...")
    master_records = read_master_records(tmp_path)
    print(f"      {len(master_records)} unique companies in Master")

    print("[3/4] Reading NocoDB Companies + Engagements ...")
    companies_by_name, assigned_by_company = build_nocodb_index()
    print(f"      {len(companies_by_name)} companies in NocoDB")

    print("[4/4] Comparing and writing report ...")
    field_rows, master_only, nocodb_only = compare(master_records, companies_by_name, assigned_by_company)
    out_path = write_report(field_rows, master_only, nocodb_only)

    print(f"\nDone. Report: {out_path}")
    print(f"  Master-only companies (not yet in NocoDB): {len(master_only)}")
    print(f"  NocoDB-only companies (not in Master): {len(nocodb_only)}")


if __name__ == "__main__":
    main()
