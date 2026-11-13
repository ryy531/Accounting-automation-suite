# =============================================================================
# rename_cra_files.py — CRA File Renamer
#
# What it does:
#   1. Scans every client folder inside 2. Intake
#   2. Looks inside each client's "1. Client Intake" subfolder
#   3. Identifies the 5 CRA files by name pattern
#   4. Shows a preview table, asks for confirmation
#   5. Renames to: LastName_FirstName_DocType_TaxYear.pdf
#
# CRA files recognised:
#   Client data enquiry.pdf          → CRADataEnquiry
#   Client summary.pdf               → CRASummary
#   Overview.pdf                     → CRAOverview
#   20XX-AllSlips.pdf (any year)     → AllSlips
#   Notice_of_assessment_*.pdf       → NOA
# =============================================================================

import os
import config

# ── CRA file identification rules ─────────────────────────────────────────────
# Each rule: (match_function, doc_type_label)
CRA_FILE_RULES = [
    (lambda n: n.lower() == "client data enquiry.pdf",                              "CRADataEnquiry"),
    (lambda n: n.lower() == "client summary.pdf",                                   "CRASummary"),
    (lambda n: n.lower() == "overview.pdf",                                         "CRAOverview"),
    (lambda n: "allslips" in n.lower() and n.endswith(".pdf"),                      "AllSlips"),
    (lambda n: n.lower().startswith("notice_of_") and "assessment" in n.lower(),   "NOA"),
]


def identify_cra_file(filename):
    """Return doc-type label if filename matches a CRA pattern, else None."""
    for matcher, doc_type in CRA_FILE_RULES:
        if matcher(filename):
            return doc_type
    return None


def extract_client_prefix(folder_name):
    """
    Extract 'LastName_FirstName' from folder name.
    'Zhang,Wei & Li,Mei'  → 'Zhang_Wei'
    'Brown,Aaron & Mary'  → 'Brown_Aaron'
    'Smith,John'          → 'Smith_John'
    """
    primary = folder_name.split("&")[0].strip()
    if "," in primary:
        last, first = primary.split(",", 1)
        return f"{last.strip()}_{first.strip()}"
    return primary.replace(" ", "_")


def extract_prefix_from_cra_folder(cra_folder_name):
    """
    Extract 'LastName_FirstName' from a CRA_ subfolder name.
    'CRA_Jamie Reid'    → 'Reid_Jamie'
    'CRA_William Reid'  → 'Reid_William'
    """
    name = cra_folder_name[4:].strip()  # strip 'CRA_'
    parts = name.split()
    if len(parts) >= 2:
        return f"{parts[-1]}_{parts[0]}"
    return name.replace(" ", "_")


def build_rename_plan(client_folder_path, folder_name):
    """
    Scan a client's intake subfolder for unprocessed CRA files.

    Two structures are supported:
      - Single / direct:  PDFs sit directly in 1.Client Intake\
      - Couple subfolders: PDFs sit in 1.Client Intake\CRA_FirstName LastName\

    Returns list of (display_label, old_path, new_path).
    """
    intake_path = os.path.join(client_folder_path, config.CRA_INTAKE_SUBFOLDER)
    if not os.path.isdir(intake_path):
        return []

    plan = []

    # Detect CRA_ subfolders (couple / individual-per-person structure)
    cra_subfolders = [
        d for d in os.listdir(intake_path)
        if d.startswith("CRA_") and os.path.isdir(os.path.join(intake_path, d))
    ]

    if cra_subfolders:
        for cra_folder in sorted(cra_subfolders):
            cra_path = os.path.join(intake_path, cra_folder)
            prefix = extract_prefix_from_cra_folder(cra_folder)
            display = f"{folder_name} / {cra_folder}"
            for filename in os.listdir(cra_path):
                if not filename.endswith(".pdf"):
                    continue
                doc_type = identify_cra_file(filename)
                if doc_type is None:
                    continue
                old_path = os.path.join(cra_path, filename)
                new_name = f"{prefix}_{doc_type}_{config.TAX_YEAR}.pdf"
                new_path = os.path.join(cra_path, new_name)
                if old_path != new_path:
                    plan.append((display, old_path, new_path))
    else:
        # Single person: PDFs sit directly in intake subfolder
        prefix = extract_client_prefix(folder_name)
        for filename in os.listdir(intake_path):
            if not filename.endswith(".pdf"):
                continue
            doc_type = identify_cra_file(filename)
            if doc_type is None:
                continue
            old_path = os.path.join(intake_path, filename)
            new_name = f"{prefix}_{doc_type}_{config.TAX_YEAR}.pdf"
            new_path = os.path.join(intake_path, new_name)
            if old_path != new_path:
                plan.append((folder_name, old_path, new_path))

    return plan


# ── Main ──────────────────────────────────────────────────────────────────────

def rename_cra_files():
    print("\n" + "=" * 60)
    print("  CRA File Renamer")
    print("=" * 60)

    intake_folder = os.path.join(config.BASE_WORKFLOW_PATH, config.INTAKE_FOLDER_NAME)
    if not os.path.isdir(intake_folder):
        print(f"\n  [ERROR] Intake folder not found:")
        print(f"  {intake_folder}")
        return

    print(f"\n  Scanning: {intake_folder}\n")

    # Collect all renames across all client folders
    full_plan = []  # (folder_name, old_path, new_path)

    for folder_name in sorted(os.listdir(intake_folder)):
        folder_path = os.path.join(intake_folder, folder_name)
        if not os.path.isdir(folder_path) or folder_name.startswith("."):
            continue

        renames = build_rename_plan(folder_path, folder_name)
        full_plan.extend(renames)

    if not full_plan:
        print("  No CRA files found to rename.")
        print(f"  Files must be inside: {{client folder}}\\{config.CRA_INTAKE_SUBFOLDER}\\")
        print(f"  Expected filenames: Client data enquiry.pdf, Client summary.pdf,")
        print(f"                      Overview.pdf, 20XX-AllSlips.pdf, Notice_of_assessment_*.pdf")
        return

    # Preview table
    print(f"  Found {len(full_plan)} file(s) to rename:\n")
    COL1, COL2, COL3 = 32, 50, 42
    print(f"  {'Client':<{COL1}}  {'Current Name':<{COL2}}  New Name")
    print(f"  {'-'*COL1}  {'-'*COL2}  {'-'*COL3}")
    for folder_name, old, new in full_plan:
        client = folder_name[:COL1]
        old_name = os.path.basename(old)[:COL2]
        new_name = os.path.basename(new)
        print(f"  {client:<{COL1}}  {old_name:<{COL2}}  {new_name}")

    # Confirm
    print()
    confirm = input("  Rename all listed files? (Y / N): ").strip().upper()
    if confirm != "Y":
        print("\n  Cancelled — no files were changed.")
        return

    # Execute
    renamed = skipped = errors = 0
    print()
    for folder_name, old, new in full_plan:
        old_name = os.path.basename(old)
        new_name = os.path.basename(new)
        try:
            if os.path.exists(new):
                print(f"  [SKIP]  Already exists: {new_name}")
                skipped += 1
                continue
            os.rename(old, new)
            print(f"  [OK]    {old_name}  →  {new_name}")
            renamed += 1
        except Exception as e:
            print(f"  [ERROR] {old_name}: {e}")
            errors += 1

    print(f"\n  Done — Renamed: {renamed}  |  Skipped: {skipped}  |  Errors: {errors}")


if __name__ == "__main__":
    rename_cra_files()
    input("\nPress Enter to exit...")
