# =============================================================================
# client_lookup.py — Client Lookup (New / Returning)
#
# What it does:
#   1. Loads ClientMaster.xlsx (corporate 总表)
#   2. Searches Owner Name + 2nd Owner Name columns for the typed name
#   3. Reports: known client or possibly new
#   4. Shows company info (Legal Name, T2 YE, T2 Filed Until) for reference
#
# Note on T1 carry-forward year:
#   This table tracks corporate (T2/GST) history, not personal T1 history.
#   If the client is found here, confirm their T1 carry-forward year directly
#   in TaxCycle before proceeding.
# =============================================================================

import io
import os
import pandas as pd
from rapidfuzz import fuzz, process

import config


# ── Master list loading ───────────────────────────────────────────────────────

def load_master():
    """Load ClientMaster.xlsx (plain or password-protected). Returns DataFrame or None on error."""
    if not os.path.exists(config.CLIENT_MASTER_FILE):
        print(f"\n  [ERROR] Client master file not found:")
        print(f"  {config.CLIENT_MASTER_FILE}")
        return None

    password = getattr(config, "CLIENT_MASTER_PASSWORD", None)

    try:
        if password:
            import msoffcrypto
            with open(config.CLIENT_MASTER_FILE, "rb") as f:
                office_file = msoffcrypto.OfficeFile(f)
                office_file.load_key(password=password)
                buf = io.BytesIO()
                office_file.decrypt(buf)
                buf.seek(0)
            df = pd.read_excel(buf, sheet_name=0, engine="openpyxl")
        else:
            df = pd.read_excel(config.CLIENT_MASTER_FILE, sheet_name=0, engine="openpyxl")
    except Exception as e:
        print(f"\n  [ERROR] Could not open master file: {e}")
        return None

    missing = [c for c in config.CLIENT_MASTER_NAME_COLS if c not in df.columns]
    if missing:
        print(f"\n  [ERROR] Name column(s) not found in master file: {missing}")
        print(f"  Available columns: {list(df.columns)}")
        print(f"  Update CLIENT_MASTER_NAME_COLS in config.py to match.")
        return None

    return df


# ── Name index building ───────────────────────────────────────────────────────

def build_name_index(df):
    """
    Build a flat list of (name, row_index, source_col) from all name columns.
    Skips blank / NaN entries.
    """
    index = []
    for col in config.CLIENT_MASTER_NAME_COLS:
        if col not in df.columns:
            continue
        for row_idx, val in df[col].items():
            name = str(val).strip() if pd.notna(val) else ""
            if name and name.lower() != "nan":
                index.append((name, row_idx, col))
    return index


# ── Result display ────────────────────────────────────────────────────────────

def print_match(df, row_idx, matched_name, score, source_col):
    row = df.loc[row_idx]
    print()
    print(f"  Status        : KNOWN CLIENT  (match score: {score}%)")
    print(f"  Matched via   : {source_col}  →  {matched_name}")

    # Show configured display columns (skip if column missing or value empty)
    for col in config.CLIENT_MASTER_DISPLAY_COLS:
        if col not in df.columns:
            continue
        val = row[col]
        if pd.isna(val) or str(val).strip() in ("", "nan"):
            continue
        print(f"  {col:<20}: {val}")

    print()
    print("  T1 carry-forward year: check TaxCycle for this client's prior T1 return.")


# ── Main ──────────────────────────────────────────────────────────────────────

def lookup_client():
    print("\n" + "=" * 60)
    print("  Client Lookup — New / Returning")
    print("=" * 60)

    df = load_master()
    if df is None:
        return

    name_index = build_name_index(df)
    all_names  = [entry[0] for entry in name_index]

    print(f"\n  Loaded {len(df)} clients  |  {len(name_index)} names indexed")
    print(f"  (searching: {', '.join(config.CLIENT_MASTER_NAME_COLS)})")
    print("  Type a client name to look up, or Q to quit.\n")

    while True:
        query = input("  Client name: ").strip()
        if query.upper() == "Q":
            break
        if not query:
            continue

        # Fuzzy match — top 5
        results = process.extract(
            query, all_names, scorer=fuzz.token_sort_ratio, limit=5
        )

        if not results or results[0][1] < 55:
            print(f"\n  No match found for '{query}'.")
            print("  → Likely a NEW client — proceed with new intake.\n")

        elif results[0][1] >= 85:
            # High confidence
            best_name, best_score, best_pos = results[0]
            _, row_idx, source_col = name_index[best_pos]
            print_match(df, row_idx, best_name, best_score, source_col)

        else:
            # Low confidence — show options
            print(f"\n  Possible matches (please verify):\n")
            candidates = []
            for i, (name, score, pos) in enumerate(results, 1):
                _, row_idx, source_col = name_index[pos]
                legal = df.loc[row_idx].get("Legal Name", "")
                legal_str = f"  [{legal}]" if pd.notna(legal) and str(legal).strip() else ""
                print(f"  {i}. {name:<40}{legal_str}  ({score}%)")
                candidates.append((name, score, row_idx, source_col))

            pick = input(
                "\n  Enter number to confirm, or N for new client: "
            ).strip().upper()

            if pick == "N":
                print("  → NEW client — proceed with new intake.")
            elif pick.isdigit() and 1 <= int(pick) <= len(candidates):
                chosen_name, chosen_score, row_idx, source_col = candidates[int(pick) - 1]
                print_match(df, row_idx, chosen_name, chosen_score, source_col)
            else:
                print("  Invalid input — skipped.")

        print()
        again = input("  Look up another client? (Y / N): ").strip().upper()
        print()
        if again != "Y":
            break


if __name__ == "__main__":
    lookup_client()
    input("\nPress Enter to exit...")
