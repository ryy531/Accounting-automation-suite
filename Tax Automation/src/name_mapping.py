# =============================================================================
# name_mapping.py — Build TaxCycle ↔ Folder Name Mapping Table
#
# What it does:
#   1. Scans all workflow folders for client folder names
#   2. Reads TaxCycle export Excel for client names + emails
#   3. Fuzzy-matches TaxCycle names to folder names
#   4. Outputs client_mapping.xlsx:
#        - Green  = auto-confirmed (score >= 90)
#        - Yellow = please review  (score 70–89)
#        - Red    = no match found (score < 70)
# =============================================================================

import os
import re
import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from rapidfuzz import fuzz, process

import config

# ── Colour fills ──────────────────────────────────────────────────────────────
FILL_GREEN  = PatternFill("solid", fgColor="C6EFCE")
FILL_YELLOW = PatternFill("solid", fgColor="FFEB9C")
FILL_RED    = PatternFill("solid", fgColor="FFC7CE")
FILL_HEADER = PatternFill("solid", fgColor="4472C4")
FONT_HEADER = Font(bold=True, color="FFFFFF")
FONT_RED    = Font(color="9C0006")
FONT_GREEN  = Font(color="276221")
FONT_YELLOW = Font(color="7D6608")


# ── Folder scanning ───────────────────────────────────────────────────────────

def scan_client_folders() -> list[str]:
    """Return all unique top-level client folder names across workflow folders."""
    folders = set()
    for wf in config.WORKFLOW_FOLDERS_TO_SCAN:
        wf_path = os.path.join(config.BASE_WORKFLOW_PATH, wf)
        if not os.path.isdir(wf_path):
            print(f"  [SKIP] Not found: {wf_path}")
            continue
        for name in os.listdir(wf_path):
            full = os.path.join(wf_path, name)
            if os.path.isdir(full) and not name.startswith("."):
                folders.add(name)
    return sorted(folders)


# ── Name parsing ──────────────────────────────────────────────────────────────

def parse_folder_name(folder: str) -> dict:
    """
    Parse folder name into searchable parts.

    Examples:
      "Brown,Aaron & Mary"     → primary="Aaron Brown", spouse="Mary Brown"
      "Zhang,Wei & Li,Mei"     → primary="Wei Zhang",   spouse="Mei Li"
      "Smith,John"             → primary="John Smith",  spouse=None
    """
    result = {"folder": folder, "primary": "", "spouse": ""}
    folder = folder.strip()

    if "&" in folder:
        left, right = folder.split("&", 1)
        left, right = left.strip(), right.strip()

        # Parse left side: "Last,First"
        if "," in left:
            last, first = left.split(",", 1)
            last, first = last.strip(), first.strip()
            result["primary"] = f"{first} {last}"

            # Right side: either "WifeFirst" (same last) or "WifeLast,WifeFirst"
            if "," in right:
                w_last, w_first = right.split(",", 1)
                result["spouse"] = f"{w_first.strip()} {w_last.strip()}"
            else:
                result["spouse"] = f"{right} {last}"
    else:
        # Single person
        if "," in folder:
            last, first = folder.split(",", 1)
            result["primary"] = f"{first.strip()} {last.strip()}"

    return result


def folder_search_names(folder: str) -> list[str]:
    """Return all name variants for a folder (used as fuzzy match targets)."""
    parsed = parse_folder_name(folder)
    names = []
    if parsed["primary"]:
        names.append(parsed["primary"])
    if parsed["spouse"]:
        names.append(parsed["spouse"])
    return names


# ── TaxCycle export loading ───────────────────────────────────────────────────

def load_taxcycle(path: str) -> pd.DataFrame:
    """Load TaxCycle export, use Sheet 1 (index 0), return relevant columns."""
    df = pd.read_excel(path, sheet_name=0)

    required = ["Name", "Email", "Invoice amount", "Refund (Balance due)"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        print(f"  [WARN] TaxCycle export missing columns: {missing}")

    keep = [c for c in required if c in df.columns]
    return df[keep].copy()


# ── Fuzzy matching ────────────────────────────────────────────────────────────

def best_folder_match(tc_name: str, folder_parsed: list[dict]) -> tuple[str, int]:
    """
    Find the best matching folder for a TaxCycle name.
    Returns (folder_name, score).
    """
    best_folder = ""
    best_score  = 0

    for fp in folder_parsed:
        for variant in [fp["primary"], fp["spouse"]]:
            if not variant:
                continue
            score = fuzz.token_sort_ratio(tc_name.lower(), variant.lower())
            if score > best_score:
                best_score  = score
                best_folder = fp["folder"]

    return best_folder, best_score


# ── Main ──────────────────────────────────────────────────────────────────────

def build_mapping():
    print("\n" + "="*60)
    print("  Name Mapping Builder")
    print("="*60)

    # 1. Scan folders
    print("\n[1/4] Scanning workflow folders...")
    folder_names = scan_client_folders()
    print(f"  Found {len(folder_names)} client folders")

    folder_parsed = [parse_folder_name(f) for f in folder_names]

    # 2. Load TaxCycle
    print("\n[2/4] Loading TaxCycle export...")
    if not os.path.exists(config.TAXCYCLE_EXPORT):
        print(f"  [ERROR] File not found: {config.TAXCYCLE_EXPORT}")
        print(f"  Please copy the TaxCycle export to that path and retry.")
        return
    tc_df = load_taxcycle(config.TAXCYCLE_EXPORT)
    print(f"  Loaded {len(tc_df)} clients from TaxCycle")

    # 3. Match
    print("\n[3/4] Fuzzy matching names...")
    rows = []
    for _, row in tc_df.iterrows():
        tc_name = str(row.get("Name", "")).strip()
        if not tc_name:
            continue

        folder, score = best_folder_match(tc_name, folder_parsed)

        if score >= config.AUTO_CONFIRM_THRESHOLD:
            status = "Auto-confirmed"
        elif score >= config.REVIEW_THRESHOLD:
            status = "Please review"
        else:
            status = "No match — manual entry required"
            folder = ""

        rows.append({
            "TaxCycle Name":     tc_name,
            "Matched Folder":    folder,
            "Match Score":       score,
            "Status":            status,
            "Email":             row.get("Email", ""),
            "Invoice Amount":    row.get("Invoice amount", ""),
            "Refund/Balance":    row.get("Refund (Balance due)", ""),
            "Override Folder":   "",   # staff fills this in for red rows
        })

    # 4. Write Excel
    print("\n[4/4] Writing output file...")
    os.makedirs(config.SCRIPTS_DATA_PATH, exist_ok=True)

    result_df = pd.DataFrame(rows)
    result_df.to_excel(config.MAPPING_FILE, index=False, engine="openpyxl")

    # Apply formatting
    _format_mapping_excel(config.MAPPING_FILE, rows)

    auto  = sum(1 for r in rows if r["Status"] == "Auto-confirmed")
    review= sum(1 for r in rows if r["Status"] == "Please review")
    none_ = sum(1 for r in rows if r["Status"].startswith("No match"))

    print(f"\n  Results:")
    print(f"    Auto-confirmed  : {auto:>4}  (green)")
    print(f"    Please review   : {review:>4}  (yellow)")
    print(f"    No match found  : {none_:>4}  (red — fill 'Override Folder' column)")
    print(f"\n  Saved to: {config.MAPPING_FILE}")
    print("\n  Next step: Open the file, fix red rows, then save.")


def _format_mapping_excel(path: str, rows: list[dict]):
    wb = load_workbook(path)
    ws = wb.active
    ws.title = "Name Mapping"

    thin = Side(style="thin", color="CCCCCC")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    col_widths = [30, 38, 12, 28, 34, 16, 16, 30]
    for i, w in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    # Header row
    for cell in ws[1]:
        cell.fill      = FILL_HEADER
        cell.font      = FONT_HEADER
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border    = border
    ws.row_dimensions[1].height = 20
    ws.freeze_panes = "A2"

    # Data rows
    for i, row_data in enumerate(rows, 2):
        status = row_data["Status"]
        if status == "Auto-confirmed":
            fill = FILL_GREEN;  font = FONT_GREEN
        elif status == "Please review":
            fill = FILL_YELLOW; font = FONT_YELLOW
        else:
            fill = FILL_RED;    font = FONT_RED

        for cell in ws[i]:
            cell.border    = border
            cell.alignment = Alignment(vertical="center")

        # Colour only the status + score columns (C and D)
        ws.cell(i, 3).fill = fill
        ws.cell(i, 3).font = font
        ws.cell(i, 4).fill = fill
        ws.cell(i, 4).font = font

    # Add a note in the header for the Override column
    ws["H1"].comment = None  # clear any existing
    ws["H1"] = "Override Folder (fill for red rows)"

    wb.save(path)


if __name__ == "__main__":
    build_mapping()
    input("\nPress Enter to exit...")
