# =============================================================================
# fix_intake.py — Comprehensive Intake Folder Repair
#
# Does everything in one pass for every client folder in 2. Intake:
#
#   1. RENAME   — fix folder name (spaces after commas, "and"→"&")
#   2. SUBFOLDERS — ensure 4 standard subfolders exist, matched by NUMBER PREFIX
#                   so "3. Siged..." is recognised as satisfying "3. Signed..."
#                   and no duplicate is created
#   3. PERSON FOLDERS — inside the Client Intake subfolder, ensure
#                   "FirstName LastName" and "CRA FirstName LastName" exist,
#                   derived by parsing the client folder name
#   4. CLEANUP  — remove empty duplicate subfolders (same number prefix,
#                   different name) left over from previous runs
#
# Safety:
#   - DRY RUN by default; --apply to make real changes
#   - Non-empty folders are NEVER deleted
#   - All actions logged to data/logs/fix_intake_log.txt
#
# Usage:
#   python fix_intake.py            ← preview
#   python fix_intake.py --apply    ← apply
# =============================================================================

import os
import re
import sys
import shutil
from datetime import datetime

# ── Paths ─────────────────────────────────────────────────────────────────────
_script_dir  = os.path.dirname(os.path.abspath(__file__))
_project_dir = os.path.dirname(_script_dir)
_server_root = os.path.dirname(os.path.dirname(_project_dir))

BASE_PATH     = os.path.join(_server_root, "Quickbook Files", "6. 2025 Personal Tax")
INTAKE_PATH   = os.path.join(BASE_PATH, "2. Intake")
TEMPLATE_PATH = os.path.join(BASE_PATH, "1. Template")
LOGS_PATH     = os.path.join(_project_dir, "data", "logs")
LOG_FILE      = os.path.join(LOGS_PATH, "fix_intake_log.txt")

# ── Standard subfolder names (canonical) ──────────────────────────────────────
CLIENT_SUBFOLDERS = [
    "1. Client Intake",
    "2. Taxcycle",
    "3. Signed and efile confirmation",
    "4. Admin",
]

# ── Folders to skip entirely ───────────────────────────────────────────────────
SKIP_EXACT = {
    "Last name,First name & Leave blank if same,First name",
    "New folder",
    "Riley, Test",
}


# =============================================================================
# 1. FOLDER NAME NORMALISATION
# =============================================================================

def normalize_name(name: str) -> str:
    n = name
    n = re.sub(r'\s+[Aa][Nn][Dd]\s+', ' & ', n)   # "and" → "&"
    n = re.sub(r'\s*&\s*', ' & ', n)               # spacing around &
    n = re.sub(r',\s+', ',', n)                    # remove space after comma
    return n


# =============================================================================
# 2. SUBFOLDER MANAGEMENT — matched by leading number
# =============================================================================

def _leading_num(name: str) -> str:
    """Extract leading digits, e.g. '3. Siged...' → '3'."""
    m = re.match(r'^(\d+)', name.strip())
    return m.group(1) if m else ""


def _find_existing_by_num(client_path: str, num: str) -> list[str]:
    """Return all subfolder names in client_path that start with `num`."""
    return [
        e for e in os.listdir(client_path)
        if _leading_num(e) == num
        and os.path.isdir(os.path.join(client_path, e))
    ]


def audit_subfolders(client_path: str):
    """
    Returns:
      missing   — canonical names that have NO match by leading number
      duplicates — (num, [name, name, ...]) where >1 folder shares a prefix
                   and at least one is empty (safe to remove)
    """
    missing    = []
    duplicates = []

    for canonical in CLIENT_SUBFOLDERS:
        num     = _leading_num(canonical)
        matches = _find_existing_by_num(client_path, num)

        if not matches:
            missing.append(canonical)
        elif len(matches) > 1:
            # More than one folder with same number — flag empties for cleanup
            duplicates.append((num, matches))

    return missing, duplicates


def create_subfolder(client_path: str, canonical: str):
    """Create a subfolder, copying from Template if available."""
    dest = os.path.join(client_path, canonical)
    num  = _leading_num(canonical)

    # Try to find matching folder in template by leading number
    src = None
    if os.path.isdir(TEMPLATE_PATH):
        for t in os.listdir(TEMPLATE_PATH):
            if _leading_num(t) == num and os.path.isdir(os.path.join(TEMPLATE_PATH, t)):
                src = os.path.join(TEMPLATE_PATH, t)
                break

    if src:
        shutil.copytree(src, dest, dirs_exist_ok=True)
    else:
        os.makedirs(dest, exist_ok=True)


def get_client_intake_path(client_path: str) -> str | None:
    """Find the Client Intake subfolder by leading number '1'."""
    matches = _find_existing_by_num(client_path, "1")
    return os.path.join(client_path, matches[0]) if matches else None


# =============================================================================
# 3. PERSON FOLDER MANAGEMENT
# =============================================================================

def parse_persons_from_folder_name(folder_name: str) -> list[str]:
    """
    Derive 'First Last' person name strings from a client folder name.

    Gao,Yifan               → ['Yifan Gao']
    Ma,Lei & Li,Na          → ['Lei Ma', 'Na Li']
    Walker,James & Lisa   → ['James Walker', 'Lisa Walker']
    tran,minh & Lan pham → ['minh tran', 'Lan pham']   (preserves original case)
    """
    parts = [p.strip() for p in folder_name.split('&')]
    persons = []

    first_last = None   # carry forward for same-last-name spouse

    for i, part in enumerate(parts):
        if ',' in part:
            last, first = part.split(',', 1)
            last  = last.strip()
            first = first.strip()
            persons.append(f"{first} {last}")
            if i == 0:
                first_last = last
        else:
            # No comma → spouse shares husband's last name
            first = part.strip()
            last  = first_last or ""
            if last:
                persons.append(f"{first} {last}")
            else:
                persons.append(first)   # fallback: just the first name

    return persons


def audit_person_folders(intake_subfolder_path: str, persons: list[str]):
    """
    Return person names whose 'FirstName LastName' or 'CRA FirstName LastName'
    folders are missing inside the Client Intake subfolder.
    """
    existing = set(os.listdir(intake_subfolder_path))
    missing  = []
    for p in persons:
        if p not in existing or f"CRA {p}" not in existing:
            missing.append(p)
    return missing


def create_person_folders(intake_subfolder_path: str, persons: list[str]):
    for p in persons:
        os.makedirs(os.path.join(intake_subfolder_path, p),          exist_ok=True)
        os.makedirs(os.path.join(intake_subfolder_path, f"CRA {p}"), exist_ok=True)


# =============================================================================
# 4. DUPLICATE SUBFOLDER CLEANUP
# =============================================================================

def find_empty_duplicates(client_path: str, num: str, names: list[str]) -> list[str]:
    """
    Given multiple folders sharing a number prefix, return the names of empty
    ones that are NOT the canonical name (those are safe to delete).
    Keep the canonical name (or whichever is non-empty) and remove the rest.
    """
    canonical = next((s for s in CLIENT_SUBFOLDERS if _leading_num(s) == num), None)
    to_delete = []
    for name in names:
        if name == canonical:
            continue   # never delete the canonical one
        path = os.path.join(client_path, name)
        if not os.listdir(path):   # only delete if empty
            to_delete.append(name)
    return to_delete


# =============================================================================
# SCAN + PLAN
# =============================================================================

def scan_intake():
    """
    Scan all client folders in Intake and build a plan of actions.
    Returns list of dicts, one per client.
    """
    if not os.path.isdir(INTAKE_PATH):
        print(f"\n  [ERROR] Intake path not found:\n  {INTAKE_PATH}\n")
        return []

    plan = []

    for entry in sorted(os.listdir(INTAKE_PATH)):
        entry_path = os.path.join(INTAKE_PATH, entry)
        if not os.path.isdir(entry_path):
            continue
        if entry in SKIP_EXACT or (entry and entry[0].isdigit()):
            continue

        corrected_name = normalize_name(entry)
        # Use corrected name for subfolder analysis (path still points to old name pre-apply)
        effective_path = entry_path

        missing_subs, dup_groups = audit_subfolders(effective_path)

        # Person folders
        intake_sub = get_client_intake_path(effective_path)
        persons    = parse_persons_from_folder_name(corrected_name)
        missing_persons = []
        if intake_sub and os.path.isdir(intake_sub):
            missing_persons = audit_person_folders(intake_sub, persons)

        # Duplicates to clean
        cleanup = []
        for num, names in dup_groups:
            cleanup.extend(find_empty_duplicates(effective_path, num, names))

        plan.append({
            "old_name":       entry,
            "new_name":       corrected_name,
            "needs_rename":   corrected_name != entry,
            "missing_subs":   missing_subs,
            "missing_persons": missing_persons,
            "cleanup":        cleanup,
            "path":           entry_path,
            "persons":        persons,
        })

    return plan


# =============================================================================
# PREVIEW
# =============================================================================

def preview(plan: list):
    rename_count  = sum(1 for c in plan if c["needs_rename"])
    sub_count     = sum(len(c["missing_subs"]) for c in plan)
    person_count  = sum(len(c["missing_persons"]) for c in plan)
    cleanup_count = sum(len(c["cleanup"]) for c in plan)

    print(f"\n  {'─'*60}")
    print(f"  Clients scanned : {len(plan)}")
    print(f"  Renames needed  : {rename_count}")
    print(f"  Missing subfolders to create : {sub_count}")
    print(f"  Missing person folders       : {person_count}")
    print(f"  Empty duplicates to delete   : {cleanup_count}")
    print(f"  {'─'*60}\n")

    for c in plan:
        has_work = c["needs_rename"] or c["missing_subs"] or c["missing_persons"] or c["cleanup"]
        if not has_work:
            continue

        print(f"  ▸ {c['old_name']}")

        if c["needs_rename"]:
            print(f"      RENAME  → {c['new_name']}")

        for s in c["missing_subs"]:
            print(f"      CREATE  {s}")

        for p in c["missing_persons"]:
            print(f"      CREATE  1.Client Intake / {p}")
            print(f"      CREATE  1.Client Intake / CRA {p}")

        for d in c["cleanup"]:
            print(f"      DELETE  {d}  (empty duplicate)")

        print()


# =============================================================================
# APPLY
# =============================================================================

def apply(plan: list) -> list[str]:
    results = []
    ok = errors = 0

    for c in plan:
        client_label = c["new_name"]
        current_path = c["path"]

        # ── 1. Rename ──
        if c["needs_rename"]:
            new_path = os.path.join(INTAKE_PATH, c["new_name"])
            if os.path.exists(new_path):
                msg = f"[SKIP RENAME] {c['old_name']} → already exists"
                print(f"  {msg}")
                results.append(msg)
                current_path = new_path
            else:
                try:
                    os.rename(current_path, new_path)
                    current_path = new_path
                    msg = f"[RENAMED] {c['old_name']} → {c['new_name']}"
                    print(f"  {msg}")
                    results.append(msg)
                    ok += 1
                except Exception as e:
                    msg = f"[ERROR RENAME] {c['old_name']} — {e}"
                    print(f"  {msg}")
                    results.append(msg)
                    errors += 1
                    continue

        # ── 2. Delete empty duplicates BEFORE creating new subfolders ──
        for dup in c["cleanup"]:
            dup_path = os.path.join(current_path, dup)
            if not os.path.exists(dup_path):
                continue
            if os.listdir(dup_path):
                msg = f"[SKIP DELETE] {client_label}\\{dup}  (not empty)"
                print(f"  ⚠  {msg}")
                results.append(msg)
                continue
            try:
                os.rmdir(dup_path)
                msg = f"[DELETED] {client_label}\\{dup}"
                print(f"  {msg}")
                results.append(msg)
                ok += 1
            except Exception as e:
                msg = f"[ERROR DELETE] {client_label}\\{dup} — {e}"
                print(f"  {msg}")
                results.append(msg)
                errors += 1

        # ── 3. Create missing subfolders ──
        for sub in c["missing_subs"]:
            try:
                create_subfolder(current_path, sub)
                msg = f"[CREATED SUB] {client_label}\\{sub}"
                print(f"  {msg}")
                results.append(msg)
                ok += 1
            except Exception as e:
                msg = f"[ERROR SUB] {client_label}\\{sub} — {e}"
                print(f"  {msg}")
                results.append(msg)
                errors += 1

        # ── 4. Create missing person folders ──
        intake_sub = get_client_intake_path(current_path)
        if intake_sub and c["missing_persons"]:
            try:
                create_person_folders(intake_sub, c["missing_persons"])
                for p in c["missing_persons"]:
                    msg = f"[CREATED PERSON] {client_label} / {p} + CRA {p}"
                    print(f"  {msg}")
                    results.append(msg)
                ok += len(c["missing_persons"])
            except Exception as e:
                msg = f"[ERROR PERSON] {client_label} — {e}"
                print(f"  {msg}")
                results.append(msg)
                errors += 1

    print(f"\n  {'─'*40}")
    print(f"  Done — OK: {ok}  Errors: {errors}\n")
    return results


# =============================================================================
# MAIN
# =============================================================================

def main():
    apply_mode = "--apply" in sys.argv

    print("\n" + "=" * 65)
    print("  fix_intake.py — Intake Folder Repair")
    print(f"  {INTAKE_PATH}")
    print("=" * 65)

    plan = scan_intake()
    if not plan:
        return

    preview(plan)

    has_work = any(
        c["needs_rename"] or c["missing_subs"] or c["missing_persons"] or c["cleanup"]
        for c in plan
    )
    if not has_work:
        print("  ✓ Everything looks good. Nothing to do.\n")
        return

    if not apply_mode:
        print("  ── DRY RUN. Run with --apply to make changes. ──\n")
        return

    answer = input("  Apply all changes? (yes / no): ").strip().lower()
    if answer != "yes":
        print("\n  Aborted.\n")
        return

    results = apply(plan)

    os.makedirs(LOGS_PATH, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n{'='*65}\n")
        f.write(f"Run: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("\n".join(results) + "\n")
    print(f"  Log: {LOG_FILE}\n")


if __name__ == "__main__":
    main()
    if "--apply" not in sys.argv:
        input("\nPress Enter to exit...")
