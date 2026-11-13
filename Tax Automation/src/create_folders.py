# =============================================================================
# create_folders.py — Automatic Client Folder Creation
#
# What it does:
#   1. Reads NewClient.xlsx (new clients to onboard)
#   2. Generates correct folder name from naming rules
#   3. Copies template folder structure into 2. Intake
#   4. Creates Children subfolders if needed
#   5. Logs results (success / already exists / error)
# =============================================================================

import os
import shutil
import re
import pandas as pd
from datetime import datetime

import config

LOG_FILE = os.path.join(config.SCRIPTS_LOGS_PATH, "create_folders_log.txt")


# ── Folder name generation ────────────────────────────────────────────────────

def _clean(val) -> str:
    """Return stripped string, converting NaN / None / 'nan' to empty string."""
    if pd.isna(val):
        return ""
    s = str(val).strip()
    return "" if s.lower() == "nan" else s


def build_folder_name(row: pd.Series) -> str:
    """
    Build client folder name from Excel row.

    Couple, same last name:  HusbandLast,HusbandFirst & WifeFirst
    Couple, diff last names: HusbandLast,HusbandFirst & WifeLast,WifeFirst
    Single (husband only):   HusbandLast,HusbandFirst
    Single (wife only):      WifeLast,WifeFirst
    """
    h_last  = _clean(row.get("HusbandLast"))
    h_first = _clean(row.get("HusbandFirst"))
    w_last  = _clean(row.get("WifeLast"))
    w_first = _clean(row.get("WifeFirst"))
    same    = _clean(row.get("SameLastName")).upper()

    has_husband = bool(h_last and h_first)
    has_wife    = bool(w_first)          # last name optional when same as husband

    if not has_husband and not has_wife:
        raise ValueError("At least one full name (Last + First) is required")

    # Single client — only wife fields filled
    if not has_husband:
        if not w_last:
            raise ValueError("WifeLast is required when HusbandLast is empty")
        return f"{w_last},{w_first}"

    base = f"{h_last},{h_first}"

    # Add spouse if present
    if has_wife:
        if same == "Y":
            return f"{base} & {w_first}"
        else:
            if w_last:
                return f"{base} & {w_last},{w_first}"
            else:
                return f"{base} & {w_first}"

    return base


def parse_kids(kids_cell) -> list[str]:
    """
    Parse Kids column — semicolon-separated names.
    e.g. "Tom;Lily" → ["Tom", "Lily"]
    Returns empty list if blank.
    """
    if pd.isna(kids_cell) or str(kids_cell).strip() in ("", "nan"):
        return []
    return [k.strip() for k in str(kids_cell).split(";") if k.strip()]


# ── Template copying ──────────────────────────────────────────────────────────

def copy_template(dest_path: str):
    """
    Copy the Template folder structure to dest_path.
    If Template folder exists on server, copy from there.
    Otherwise create the standard subfolders directly.
    """
    template_path = os.path.join(config.BASE_WORKFLOW_PATH, config.TEMPLATE_FOLDER_NAME)

    if os.path.isdir(template_path):
        # Copy the full template (preserves any files inside)
        # We copy subfolder by subfolder so we don't copy hidden system files
        for item in os.listdir(template_path):
            src = os.path.join(template_path, item)
            dst = os.path.join(dest_path, item)
            if os.path.isdir(src):
                shutil.copytree(src, dst, dirs_exist_ok=True)
            # Skip files in template root (e.g. placeholder files)
    else:
        # Fallback: create standard subfolders manually
        for subfolder in config.CLIENT_SUBFOLDERS:
            os.makedirs(os.path.join(dest_path, subfolder), exist_ok=True)


def get_person_names(row: pd.Series) -> list[str]:
    """
    Return individual person names as 'First Last' strings from an Excel row.
    Couple  → [husband_name, wife_name]
    Single  → [person_name]
    """
    h_last  = _clean(row.get("HusbandLast"))
    h_first = _clean(row.get("HusbandFirst"))
    w_last  = _clean(row.get("WifeLast"))
    w_first = _clean(row.get("WifeFirst"))
    same    = _clean(row.get("SameLastName")).upper()

    names = []
    if h_first and h_last:
        names.append(f"{h_first} {h_last}")
    if w_first:
        effective_last = h_last if (same == "Y" and not w_last) else w_last
        if effective_last:
            names.append(f"{w_first} {effective_last}")
    return names


def create_intake_person_folders(client_path: str, persons: list[str]):
    """
    Inside the 'Client Intake' subfolder, create per-person subfolders:
      PersonName
      CRA_PersonName
    One pair per person in the couple (or just one pair for a single client).
    Searches for the intake folder by partial name match (handles spacing variants).
    """
    # Find the actual intake subfolder regardless of spacing / dot style
    intake_path = None
    for entry in os.listdir(client_path):
        if "client intake" in entry.lower() and os.path.isdir(os.path.join(client_path, entry)):
            intake_path = os.path.join(client_path, entry)
            break

    if intake_path is None:
        return   # intake subfolder not found — skip silently

    for person in persons:
        os.makedirs(os.path.join(intake_path, person),            exist_ok=True)
        os.makedirs(os.path.join(intake_path, f"CRA {person}"),    exist_ok=True)


def create_child_folders(parent_path: str, children: list[str]):
    """Create Children/ChildName/subfolders structure."""
    children_root = os.path.join(parent_path, "Children")
    os.makedirs(children_root, exist_ok=True)

    for child in children:
        child_path = os.path.join(children_root, child)
        os.makedirs(child_path, exist_ok=True)
        for subfolder in config.CHILD_SUBFOLDERS:
            os.makedirs(os.path.join(child_path, subfolder), exist_ok=True)


# ── Main ──────────────────────────────────────────────────────────────────────

def create_folders():
    print("\n" + "="*60)
    print("  Client Folder Creation")
    print("="*60)

    # Validate paths
    if not os.path.exists(config.NEW_CLIENT_FILE):
        print(f"\n  [ERROR] NewClient.xlsx not found:")
        print(f"  {config.NEW_CLIENT_FILE}")
        return

    intake_path = os.path.join(config.BASE_WORKFLOW_PATH, config.INTAKE_FOLDER_NAME)
    if not os.path.isdir(intake_path):
        print(f"\n  [ERROR] Intake folder not found:")
        print(f"  {intake_path}")
        return

    # Read input
    print(f"\n  Reading: {config.NEW_CLIENT_FILE}")
    df = pd.read_excel(config.NEW_CLIENT_FILE)
    print(f"  Found {len(df)} rows")

    results = []
    created = skipped = errors = 0

    print()
    for idx, row in df.iterrows():
        row_num = idx + 2  # Excel row number

        try:
            folder_name = build_folder_name(row)
        except ValueError as e:
            msg = f"Row {row_num}: [ERROR] {e}"
            print(f"  {msg}")
            results.append(msg)
            errors += 1
            continue

        dest = os.path.join(intake_path, folder_name)

        if os.path.exists(dest):
            msg = f"Row {row_num}: [SKIP]  Already exists — {folder_name}"
            print(f"  {msg}")
            results.append(msg)
            skipped += 1
            continue

        try:
            os.makedirs(dest, exist_ok=True)
            copy_template(dest)

            persons = get_person_names(row)
            create_intake_person_folders(dest, persons)

            children = parse_kids(row.get("Kids"))
            if children:
                create_child_folders(dest, children)
                msg = f"Row {row_num}: [OK]    {folder_name}  (children: {', '.join(children)})"
            else:
                msg = f"Row {row_num}: [OK]    {folder_name}"

            print(f"  {msg}")
            results.append(msg)
            created += 1

        except Exception as e:
            msg = f"Row {row_num}: [ERROR] {folder_name} — {e}"
            print(f"  {msg}")
            results.append(msg)
            errors += 1

    # Summary
    summary = (
        f"\n  ─────────────────────────────\n"
        f"  Created : {created}\n"
        f"  Skipped : {skipped}  (already existed)\n"
        f"  Errors  : {errors}\n"
    )
    print(summary)

    # Write log
    os.makedirs(config.SCRIPTS_DATA_PATH, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n{'='*60}\n")
        f.write(f"Run: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("\n".join(results))
        f.write(summary)

    print(f"  Log saved to: {LOG_FILE}")


if __name__ == "__main__":
    create_folders()
    input("\nPress Enter to exit...")
