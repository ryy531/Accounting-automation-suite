# =============================================================================
# missing_client_reminder.py — Prior Year Client Reminder (Menu 4)
#
# What it does:
#   A. Scans prior LOOK_BACK_YEARS T1 client lists, compares with current year,
#      generates data/missing_clients_{TAX_YEAR}.xlsx for user review/edit.
#   B. Sends reminder emails to clients where Include = Y.
#
# Matching logic:
#   1. SIN exact match  (normalized: strip spaces / dashes)
#   2. Fuzzy name match (token_sort_ratio >= MISSING_CLIENT_FUZZY_THRESHOLD)
#
# Deduplication: if client appears in multiple prior years, keep most recent.
# =============================================================================

import json
import os
import re
import subprocess
from datetime import datetime

import pandas as pd
from rapidfuzz import fuzz, process

import config


# ── Normalisation helpers ─────────────────────────────────────────────────────

def _norm_sin(val) -> str:
    """Strip spaces/dashes from SIN/BN; return '' if blank."""
    if pd.isna(val) or str(val).strip() in ("", "nan"):
        return ""
    return re.sub(r"[\s\-]", "", str(val)).upper()


def _norm_name(val) -> str:
    """Lowercase-strip a name; return '' if blank."""
    if pd.isna(val) or str(val).strip() in ("", "nan"):
        return ""
    return str(val).strip().lower()


def _valid_email(val) -> bool:
    if pd.isna(val):
        return False
    return bool(re.match(r"[^@\s]+@[^@\s]+\.[^@\s]+", str(val).strip()))


# ── Folder discovery ──────────────────────────────────────────────────────────

def find_year_folder(year: int) -> str | None:
    """
    Scan QUICKBOOK_FILES_PATH for a folder matching
    '<digits>. <year> Personal Tax'.
    Returns full path or None.
    """
    pattern = re.compile(rf"^\d+\.\s*{year}\s+Personal\s+Tax\s*$", re.IGNORECASE)
    try:
        entries = os.listdir(config.QUICKBOOK_FILES_PATH)
    except OSError as e:
        print(f"  [WARN] Cannot list Quickbook Files: {e}")
        return None
    for entry in entries:
        if pattern.match(entry):
            return os.path.join(config.QUICKBOOK_FILES_PATH, entry)
    return None


def load_t1_list(folder: str) -> pd.DataFrame | None:
    """Load T1 client list.xlsx from folder. Returns DataFrame or None."""
    path = os.path.join(folder, config.T1_CLIENT_LIST_FILENAME)
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_excel(path, sheet_name=0, engine="openpyxl")
        df.columns = df.columns.str.strip()
        return df
    except Exception as e:
        print(f"  [WARN] Cannot read {path}: {e}")
        return None


# ── Matching ──────────────────────────────────────────────────────────────────

def build_current_index(df: pd.DataFrame):
    """Return (sin_set, normalised_name_list) from the current-year DataFrame."""
    sins, names = set(), []
    for _, row in df.iterrows():
        sin = _norm_sin(row.get("Reference (SIN/BN)", ""))
        name = _norm_name(row.get("Name", ""))
        if sin:
            sins.add(sin)
        if name:
            names.append(name)
    return sins, names


# Folders under these subdirs are treated as "client already active"
_WORKFLOW_SUBDIRS = [
    "2. Intake", "3. Preparing", "4. Review", "5. Waiting Info",
    "6. Sign & Pay", "6.1 Sent", "7. To efile", "8. Efiled",
]


def scan_folder_names(year_folder: str) -> list:
    """
    Scan workflow subdirectories for client folder names.
    Returns list of normalised name strings for fuzzy matching.
    """
    names = []
    for subdir in _WORKFLOW_SUBDIRS:
        path = os.path.join(year_folder, subdir)
        if not os.path.isdir(path):
            continue
        for entry in os.listdir(path):
            if not os.path.isdir(os.path.join(path, entry)):
                continue
            # Whole folder name (e.g. "Qian, Yu" → "qian, yu")
            norm = _norm_name(entry)
            if norm:
                names.append(norm)
            # Split on "&" to get individual names (e.g. "Brown, Morgan & Aaron")
            for part in entry.split("&"):
                part = part.strip()
                n = _norm_name(part)
                if n and n != norm:
                    names.append(n)
    return names


def find_missing_in_df(
    prior_df: pd.DataFrame,
    year: int,
    current_sins: set,
    current_names: list,
) -> list:
    """Return list of dicts for clients in prior_df not present in current year."""
    missing = []
    for _, row in prior_df.iterrows():
        name = str(row.get("Name", "")).strip()
        if not name or name.lower() == "nan":
            continue

        sin   = _norm_sin(row.get("Reference (SIN/BN)", ""))
        email_raw = row.get("Email", "")
        email = (
            str(email_raw).strip()
            if pd.notna(email_raw) and str(email_raw).strip() not in ("", "nan")
            else ""
        )

        # 1. SIN exact match
        if sin and sin in current_sins:
            continue

        # 2. Fuzzy name match
        nname = _norm_name(name)
        if current_names:
            result = process.extractOne(
                nname, current_names, scorer=fuzz.token_sort_ratio
            )
            if result and result[1] >= config.MISSING_CLIENT_FUZZY_THRESHOLD:
                continue

        missing.append({"Name": name, "Email": email, "SIN": sin, "Last_Seen_Year": year})

    return missing


def deduplicate(all_missing: list) -> list:
    """
    Remove duplicates across years.
    Primary key: SIN (if present); secondary: normalised name.
    Most recent year wins.
    """
    all_missing.sort(key=lambda x: x["Last_Seen_Year"], reverse=True)
    seen_sins, seen_names, result = set(), set(), []
    for entry in all_missing:
        sin   = entry["SIN"]
        nname = _norm_name(entry["Name"])
        if sin and sin in seen_sins:
            continue
        if nname in seen_names:
            continue
        if sin:
            seen_sins.add(sin)
        seen_names.add(nname)
        result.append(entry)
    result.sort(key=lambda x: (-x["Last_Seen_Year"], x["Name"].lower()))
    return result


# ── Excel output ──────────────────────────────────────────────────────────────

def _load_old_states(path: str) -> dict:
    """Load existing missing-client list and return {normalised_key: row_dict}."""
    if not os.path.exists(path):
        return {}
    try:
        df = pd.read_excel(path, sheet_name=0, engine="openpyxl")
        df.columns = df.columns.str.strip()
    except Exception:
        return {}
    preserve_cols = ["Include", "Sent At", "Reply Status", "Reply Summary", "Reply Date", "Notes"]
    lookup = {}
    for _, row in df.iterrows():
        sin = _norm_sin(row.get("SIN", ""))
        name = _norm_name(row.get("Name", ""))
        key = sin if sin else name
        if not key:
            continue
        lookup[key] = {c: row.get(c, "") for c in preserve_cols}
    return lookup


def write_missing_list(missing: list, old_path: str = None) -> str:
    """Write missing clients to a styled Excel file. Merges old states if old_path given."""
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    old_lookup = _load_old_states(old_path) if old_path else {}

    rows = []
    for e in missing:
        sin = e.get("SIN", "")
        name = _norm_name(e.get("Name", ""))
        key = sin if sin else name
        old = old_lookup.get(key, {})
        rows.append({
            "Name":           e["Name"],
            "Email":          e["Email"],
            "SIN":            e["SIN"],
            "Last Seen Year": e["Last_Seen_Year"],
            "Include":        old.get("Include", "Y") if old else "Y",
            "Sent At":        old.get("Sent At", "") if old else "",
            "Reply Status":   old.get("Reply Status", "") if old else "",
            "Reply Summary":  old.get("Reply Summary", "") if old else "",
            "Reply Date":     old.get("Reply Date", "") if old else "",
            "Notes":          old.get("Notes", "") if old else "",
        })
    df_out = pd.DataFrame(
        rows,
        columns=[
            "Name", "Email", "SIN", "Last Seen Year", "Include", "Sent At",
            "Reply Status", "Reply Summary", "Reply Date", "Notes",
        ],
    )

    path = config.MISSING_CLIENT_LIST
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df_out.to_excel(writer, index=False, sheet_name="Missing Clients")
        ws = writer.sheets["Missing Clients"]

        for col, width in [
            ("A", 30), ("B", 38), ("C", 16), ("D", 16), ("E", 10), ("F", 22),
            ("G", 14), ("H", 50), ("I", 12), ("J", 35),
        ]:
            ws.column_dimensions[col].width = width

        hdr_fill = PatternFill("solid", fgColor="2C4770")
        hdr_font = Font(color="FFFFFF", bold=True)
        for cell in ws[1]:
            cell.fill   = hdr_fill
            cell.font   = hdr_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[1].height = 20
        ws.freeze_panes = "A2"

        # Dropdown: Include column (E) — Y / N
        dv_include = DataValidation(type="list", formula1='"Y,N"', allow_blank=True)
        dv_include.error = "Please enter Y or N"
        dv_include.errorTitle = "Invalid value"
        ws.add_data_validation(dv_include)
        dv_include.add(f"E2:E{ws.max_row + 200}")

        # Dropdown: Reply Status column (G)
        reply_options = "Positive,Negative,Question,Unclear,Handled"
        dv_reply = DataValidation(type="list", formula1=f'"{reply_options}"', allow_blank=True)
        dv_reply.error = "Please select a valid status"
        dv_reply.errorTitle = "Invalid value"
        ws.add_data_validation(dv_reply)
        dv_reply.add(f"G2:G{ws.max_row + 200}")

        # Highlight rows without email in orange
        orange_fill = PatternFill("solid", fgColor="FFE0B2")
        for row in ws.iter_rows(min_row=2):
            if not row[1].value or str(row[1].value).strip() == "":
                for cell in row:
                    cell.fill = orange_fill

    return path


def open_in_excel(path: str):
    """Open a file in its default application."""
    try:
        os.startfile(path)
    except Exception:
        try:
            subprocess.Popen(["cmd", "/c", "start", "", path])
        except Exception as e:
            print(f"  [WARN] Could not open file: {e}")
            print(f"  Please open manually: {path}")


# ── Phase A: Generate list ────────────────────────────────────────────────────

def generate_missing_list():
    print("\n" + "=" * 60)
    print("  Phase A — Generate Missing Client List")
    print("=" * 60)

    tax_year  = int(config.TAX_YEAR)
    out_path  = config.MISSING_CLIENT_LIST

    # Offer to reuse existing file
    if os.path.exists(out_path):
        mtime = datetime.fromtimestamp(os.path.getmtime(out_path)).strftime("%Y-%m-%d %H:%M")
        print(f"\n  List already exists: {os.path.basename(out_path)}")
        print(f"  Generated : {mtime}")
        ans = input("  Regenerate? (Y / N): ").strip().upper()
        if ans != "Y":
            print("  Opening existing list...")
            open_in_excel(out_path)
            return

    # ── Find current-year folder ──────────────────────────────────────────────
    print(f"\n  Searching for {tax_year} Personal Tax folder...")
    cur_folder = find_year_folder(tax_year)
    if not cur_folder:
        print(f"  [ERROR] No folder found for year {tax_year} in:")
        print(f"  {config.QUICKBOOK_FILES_PATH}")
        return

    print(f"  Found : {os.path.basename(cur_folder)}")
    cur_df = load_t1_list(cur_folder)
    if cur_df is None:
        print("  [ERROR] T1 client list not found in current-year folder.")
        return

    print(f"  Current year ({tax_year}): {len(cur_df)} clients")
    current_sins, current_names = build_current_index(cur_df)

    # Also scan existing client folders as extra exclusion source
    folder_names = scan_folder_names(cur_folder)
    folder_added = len(folder_names)
    current_names.extend(folder_names)
    if folder_added:
        print(f"  Existing client folders  : {folder_added} names scanned")

    # ── Load prior years ──────────────────────────────────────────────────────
    all_missing = []
    for offset in range(1, config.LOOK_BACK_YEARS + 1):
        yr     = tax_year - offset
        folder = find_year_folder(yr)
        if not folder:
            print(f"  [WARN] Folder for {yr} not found — skipping.")
            continue
        df = load_t1_list(folder)
        if df is None:
            print(f"  [WARN] T1 client list for {yr} not found — skipping.")
            continue
        print(f"  Year {yr}: {len(df)} clients loaded")
        missing = find_missing_in_df(df, yr, current_sins, current_names)
        print(f"          → {len(missing)} not found in {tax_year}")
        all_missing.extend(missing)

    if not all_missing:
        print("\n  All prior-year clients are already in this year's list. Nothing to send.")
        return

    # ── Deduplicate & report ──────────────────────────────────────────────────
    deduped   = deduplicate(all_missing)
    no_email  = sum(1 for e in deduped if not e["Email"])

    print(f"\n  Unique missing clients : {len(deduped)}")
    if no_email:
        print(f"  No email address       : {no_email}  (highlighted in orange — set Include=N or add email)")

    # ── Write & open ──────────────────────────────────────────────────────────
    old_states = _load_old_states(out_path)
    merged = sum(1 for e in deduped
                 if (_norm_sin(e.get("SIN", "")) or _norm_name(e.get("Name", ""))) in old_states)
    out_path = write_missing_list(deduped, old_path=out_path)
    if merged:
        print(f"  Preserved states from previous list: {merged} client(s)")
    print(f"\n  List written to: {out_path}")
    print("  Opening in Excel — review, set Include (Y/N), then run Phase B to send.")
    open_in_excel(out_path)


# ── Template ──────────────────────────────────────────────────────────────────

_FALLBACK_HTML = """\
<p>Dear {{CLIENT_NAME}},</p>

<p>This is a friendly reminder that tax season is already halfway through. If you would like us to prepare your <strong>{{TAX_YEAR}} tax return</strong> this year, please send us your documents as soon as possible and avoid waiting until the last minute.</p>

<p>If your information has changed, please complete our update form here:<br>
https://example-firm.com/information-update-form/</p>

<p>Please also use the following checklist when preparing your documents:<br>
https://example-firm.com/form/checklist/</p>

<p>Our process remains the same as last year.</p>

<p>Thank you, and we look forward to assisting you again this tax season.</p>

<p>Best regards,<br>
<strong>ExampleCo LLP</strong><br>
Chartered Professional Accountants</p>
"""


def _load_template():
    """
    Load docx template → (html_str, cid_images).
    Falls back to built-in HTML if file not found.
    """
    tpl_path = config.MISSING_CLIENT_TEMPLATE
    if os.path.exists(tpl_path):
        try:
            import mammoth
            from send_package_email import extract_cid_images
            with open(tpl_path, "rb") as f:
                result = mammoth.convert_to_html(f)
            html, images = extract_cid_images(result.value)
            return html, images
        except Exception as e:
            print(f"  [WARN] Could not read docx template: {e}")
            print("  Using built-in fallback HTML.")

    # Built-in fallback
    return _FALLBACK_HTML, []


def _fill_template(html: str, client_name: str) -> str:
    """Replace per-client placeholders in the rendered HTML."""
    html = html.replace("{{CLIENT_NAME}}", client_name)
    html = html.replace("{{TAX_YEAR}}", config.TAX_YEAR)
    return html


# ── Email building ────────────────────────────────────────────────────────────

def _build_message(client_name: str, recipient: str, body_html: str, inline_images: list, creds: dict):
    from email.mime.image import MIMEImage
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    subject = config.MISSING_CLIENT_SUBJECT.format(client_name=client_name)
    outer   = MIMEMultipart("mixed")
    outer["Subject"] = subject
    outer["From"]    = f"{creds['sender_name']} <{creds['sender_email']}>"
    outer["To"]      = recipient

    if inline_images:
        related = MIMEMultipart("related")
        related.attach(MIMEText(body_html, "html", "utf-8"))
        for cid, subtype, data in inline_images:
            img = MIMEImage(data, _subtype=subtype)
            img.add_header("Content-ID", f"<{cid}>")
            img.add_header("Content-Disposition", "inline")
            related.attach(img)
        outer.attach(related)
    else:
        outer.attach(MIMEText(body_html, "html", "utf-8"))

    return outer


# ── Log helpers ───────────────────────────────────────────────────────────────

def _load_log() -> dict:
    if os.path.exists(config.MISSING_CLIENT_LOG):
        try:
            with open(config.MISSING_CLIENT_LOG, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_log(log: dict):
    with open(config.MISSING_CLIENT_LOG, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)


def _add_log_entry(log: dict, name: str, email: str):
    entry = {"email": email, "sent_at": datetime.now().isoformat(timespec="seconds")}
    log.setdefault(name, []).append(entry)


def _update_excel_sent_at(path: str, sent_names: list):
    """Write current timestamp into Sent At column for the given client names."""
    import openpyxl
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        wb = openpyxl.load_workbook(path)
        ws = wb.active
        headers    = {cell.value: cell.column for cell in ws[1]}
        name_col   = headers.get("Name")
        sent_col   = headers.get("Sent At")
        if name_col and sent_col:
            sent_set = {n.lower() for n in sent_names}
            for row in ws.iter_rows(min_row=2):
                val = row[name_col - 1].value
                if val and str(val).strip().lower() in sent_set:
                    row[sent_col - 1].value = ts
        wb.save(path)
        wb.close()
    except Exception as e:
        print(f"  [WARN] Could not update Sent At in Excel: {e}")


# ── Phase B: Send reminders ───────────────────────────────────────────────────

def send_missing_reminders():
    print("\n" + "=" * 60)
    print("  Phase B — Send Reminder Emails")
    print("=" * 60)

    path = config.MISSING_CLIENT_LIST
    if not os.path.exists(path):
        print(f"\n  [ERROR] Missing client list not found.")
        print(f"  Run Phase A first to generate: {path}")
        return

    try:
        df = pd.read_excel(path, sheet_name=0, engine="openpyxl")
        df.columns = df.columns.str.strip()
    except Exception as e:
        print(f"\n  [ERROR] Cannot read list: {e}")
        return

    # Include = Y  +  valid email
    mask     = df["Include"].astype(str).str.strip().str.upper() == "Y"
    all_incl = mask.sum()
    to_send  = df[mask & df["Email"].apply(_valid_email)].copy()
    skipped_no_email = int(all_incl) - len(to_send)

    # Exclude clients with Reply Status = Negative or Handled
    _SKIP_REPLY_STATUSES = {"negative", "handled"}
    if "Reply Status" in to_send.columns:
        skip_mask = to_send["Reply Status"].astype(str).str.strip().str.lower().isin(_SKIP_REPLY_STATUSES)
        skipped_reply = to_send[skip_mask]
        to_send = to_send[~skip_mask].copy()
        if not skipped_reply.empty:
            print(f"\n  Skipped {len(skipped_reply)} client(s) with Reply Status = Negative/Handled:")
            for _, r in skipped_reply.iterrows():
                print(f"    - {r['Name']} ({r.get('Reply Status', '')})")

    # Exclude clients who already reached max sends
    log = _load_log()
    max_sends = config.MISSING_CLIENT_MAX_SENDS
    skipped_max = []
    filtered = []
    for _, row in to_send.iterrows():
        name = str(row["Name"])
        prior = len(log.get(name, []))
        if prior >= max_sends:
            skipped_max.append((name, prior))
        else:
            filtered.append(row)
    to_send = pd.DataFrame(filtered) if filtered else pd.DataFrame(columns=to_send.columns)

    if skipped_max:
        print(f"\n  Skipped {len(skipped_max)} client(s) already sent {max_sends}+ times:")
        for name, cnt in skipped_max:
            print(f"    - {name} ({cnt}x sent)")
        print()

    if to_send.empty:
        print("\n  No clients to send to (Include=Y with valid email).")
        if skipped_no_email:
            print(f"  Note: {skipped_no_email} row(s) marked Y but have no valid email.")
        return

    # Load credentials
    from send_package_email import load_credentials, send_one
    creds = load_credentials()
    if not creds:
        return

    # Load template
    html_template, images = _load_template()
    tpl_source = "docx" if os.path.exists(config.MISSING_CLIENT_TEMPLATE) else "built-in fallback"
    print(f"\n  Template  : {tpl_source}")

    # Summary table
    year_col = "Last Seen Year" if "Last Seen Year" in df.columns else "Last_Seen_Year"
    print(f"\n  Clients to remind ({len(to_send)}):")
    print(f"  {'#':<4} {'Name':<32} {'Email':<36} {'Last Seen':<12} {'Sent Before'}")
    print("  " + "─" * 94)
    for i, (_, row) in enumerate(to_send.iterrows(), 1):
        name   = str(row["Name"])
        email  = str(row["Email"]).strip()
        yr     = row.get(year_col, "?")
        prior  = len(log.get(name, []))
        prior_str = f"{prior}x" if prior else "—"
        print(f"  {i:<4} {name:<32} {email:<36} {str(yr):<12} {prior_str}")

    if skipped_no_email:
        print(f"\n  Note: {skipped_no_email} row(s) marked Y but skipped (no valid email).")

    ans = input(f"\n  Send to all {len(to_send)} clients? (Y / N): ").strip().upper()
    if ans != "Y":
        print("  Cancelled.")
        return

    # ── Send ─────────────────────────────────────────────────────────────────
    sent_names, sent_count, fail_count = [], 0, 0
    print()
    for _, row in to_send.iterrows():
        name  = str(row["Name"])
        email = str(row["Email"]).strip()
        body  = _fill_template(html_template, name)
        msg   = _build_message(name, email, body, images, creds)
        ok, err = send_one(msg, email, creds)
        if ok:
            sent_count += 1
            sent_names.append(name)
            _add_log_entry(log, name, email)
            print(f"  ✓  {name} <{email}>")
        else:
            fail_count += 1
            print(f"  ✗  {name} — {err}")

    # Persist
    _save_log(log)
    if sent_names:
        _update_excel_sent_at(path, sent_names)

    print(f"\n  Done: {sent_count} sent, {fail_count} failed.")
    print(f"  Log : {config.MISSING_CLIENT_LOG}")


# ── Entry point ───────────────────────────────────────────────────────────────

def missing_client_reminder():
    print("\n" + "=" * 60)
    print("  Menu 4 — Prior Year Client Reminder")
    print("=" * 60)
    print()
    print("  A.  Generate missing client list")
    print(f"      Scan {config.LOOK_BACK_YEARS} prior years → Excel for review/edit")
    print()
    print("  B.  Send reminder emails")
    print("      Reads reviewed list, sends to Include=Y rows")
    print()
    print("  C.  Scan email replies")
    print("      Check inbox for client replies → AI classify → update Excel")
    print()
    print("  D.  Create folders from positive replies")
    print("      Read Positive rows → NewClient.xlsx → create folders")
    print()
    print("  Q.  Back to main menu")
    print()

    choice = input("  Enter choice (A / B / C / D / Q): ").strip().upper()
    if choice == "A":
        generate_missing_list()
    elif choice == "B":
        send_missing_reminders()
    elif choice == "C":
        from scan_email_replies import scan_replies
        scan_replies()
    elif choice == "D":
        from scan_email_replies import create_folders_from_replies
        create_folders_from_replies()
    elif choice == "Q":
        return
    else:
        print("  Invalid choice.")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    missing_client_reminder()
    input("\nPress Enter to exit...")
