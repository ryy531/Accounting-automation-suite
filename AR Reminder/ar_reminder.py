#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AR Reminder System – Example CPA Firm P.C. Inc. / ExampleCo Group
Automated overdue invoice reminder emails.
"""

import os, sys, io, json, math, hashlib, base64, socket
import logging, getpass, tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd
from rapidfuzz import process as rfprocess, fuzz

# Force UTF-8 output on Windows
if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding.lower() != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── Paths ────────────────────────────────────────────────────────────────────
SCRIPT_DIR   = Path(__file__).parent
_LIB_ROOT    = SCRIPT_DIR.parent / "_lib"
if str(_LIB_ROOT) not in sys.path:
    sys.path.insert(0, str(_LIB_ROOT))

import config.settings as shared_settings
from connectors.email_client import EmailClient

AGING_DIR    = SCRIPT_DIR / "AR Aging Report"
LOG_DIR      = SCRIPT_DIR / "logs"
DATA_DIR     = SCRIPT_DIR / "data"
HISTORY_FILE = SCRIPT_DIR / "sent_history.json"

LOG_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)

# ── Config ───────────────────────────────────────────────────────────────────
SMTP_USER    = shared_settings.EMAIL_ADDRESS or "infosk@example-firm.com"
SENDER_NAME  = "Example CPA Firm P.C. Inc."

# Master Client List paths (UNC preferred, Z: fallback)
MASTER_PATHS = [
    r"\\file-server\Shared Folder\4. General - Scan-Notes-Form-PPT\4. Accounting client list\#Business Master Client List.xlsx",
    r"Z:\4. General - Scan-Notes-Form-PPT\4. Accounting client list\#Business Master Client List.xlsx",
]
MASTER_PASSWORD = "0000000000"

INVOICE_COUNTER_FILE = SCRIPT_DIR / "invoice_counter.json"
INVOICE_START        = 20000

# ── Dummy province tax rates (replace with official rates when provided) ──────
INVOICE_TAX_RULES = {
    "BC":      {"gst": 0.05, "pst": 0.07},   # GST 5% + PST 7%
    "AB":      {"gst": 0.05, "pst": 0.00},   # GST 5% only
    "SK":      {"gst": 0.05, "pst": 0.06},   # GST 5% + PST 6%
    "ON":      {"gst": 0.13, "pst": 0.00},   # HST 13%
    "MB":      {"gst": 0.05, "pst": 0.07},   # GST 5% + PST 7%  (dummy)
    "QC":      {"gst": 0.05, "pst": 0.09975},# GST 5% + QST 9.975%  (dummy)
    "Federal": {"gst": 0.05, "pst": 0.00},   # GST 5% only
}
INVOICE_TAX_DEFAULT = {"gst": 0.05, "pst": 0.00}  # fallback

JURISDICTION_MAP = {
    "british columbia": "BC",  "bc": "BC",
    "alberta":          "AB",  "ab": "AB",
    "saskatchewan":     "SK",  "sk": "SK",
    "ontario":          "ON",  "on": "ON",
    "manitoba":         "MB",  "mb": "MB",
    "quebec":           "QC",  "qc": "QC",
    "federal":          "Federal",
    "canada":           "Federal",
}

def _resolve_province(jurisdiction: str) -> str:
    """
    Map an Incorporation Jurisdiction string to a province key.
    If multiple provinces are separated by '/' (e.g. 'AB/SK'),
    pick the one with the highest combined tax rate.
    """
    parts = [p.strip() for p in jurisdiction.replace(",", "/").split("/") if p.strip()]
    candidates = [JURISDICTION_MAP.get(p.lower(), "") for p in parts]
    candidates = [c for c in candidates if c]
    if not candidates:
        return ""
    if len(candidates) == 1:
        return candidates[0]
    # Pick province with highest total tax rate
    def total_rate(key):
        r = INVOICE_TAX_RULES.get(key, INVOICE_TAX_DEFAULT)
        return r["gst"] + r["pst"]
    return max(candidates, key=total_rate)

EXTREME_THRESHOLD = 365
OVERDUE_MIN       = 30
INTEREST_RATE     = 0.01
FUZZY_AUTO        = 90    # >= auto-accept
FUZZY_WARN        = 70    # 70-89: flagged; <70: AI retry then skip if still low

GEMINI_MODEL  = "gemini-2.0-flash"
GEMINI_KEY    = DATA_DIR / "gemini_key.enc"

SENDER_SIG = """
<br>
<p style="font-size:13px; color:#555;">
  Sincerely,<br>
  <b>Example CPA Firm P.C. Inc.</b><br>
  info@example-firm.com &nbsp;|&nbsp; infosk@example-firm.com<br>
  555-000-0000<br>
  100 Example Street, Saskatoon, SK S7A 0A0
</p>
"""

# ── Logging ──────────────────────────────────────────────────────────────────
today_str = datetime.now().strftime("%Y%m%d")
logging.basicConfig(
    filename=LOG_DIR / f"log_{today_str}.txt",
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    encoding="utf-8",
)
log = logging.getLogger()


def cprint(msg, color=None):
    codes = {"red": "\033[91m", "yellow": "\033[93m", "green": "\033[92m", "cyan": "\033[96m"}
    reset = "\033[0m"
    prefix = codes.get(color, "")
    print(f"{prefix}{msg}{reset}" if prefix else msg)


# ═══════════════════════════════════════════════════════════════════════════════
# GEMINI AI MATCHING
# ═══════════════════════════════════════════════════════════════════════════════

def _fernet():
    from cryptography.fernet import Fernet
    raw = hashlib.sha256(socket.gethostname().encode()).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


def load_gemini_key():
    if not GEMINI_KEY.exists():
        return None
    try:
        return _fernet().decrypt(GEMINI_KEY.read_bytes()).decode()
    except Exception:
        return None


def save_gemini_key(key):
    GEMINI_KEY.write_bytes(_fernet().encrypt(key.encode()))


def setup_gemini_key():
    key = load_gemini_key()
    if key:
        return key
    cprint("\n  Gemini API key not found (used for improved name matching).", "yellow")
    print("  Enter your Gemini API key, or press Enter to skip AI matching:")
    key = input("  API Key: ").strip()
    if not key:
        return None
    save_gemini_key(key)
    cprint("  Key saved (encrypted).", "green")
    return key


def ai_match_name(name, candidates, api_key):
    """Ask Gemini to pick the best company name match from a candidate list.
    Returns (matched_candidate_or_None, method_label)."""
    try:
        from google import genai
        client = genai.Client(api_key=api_key)
        cand_str = "\n".join(f"{i+1}. {c}" for i, c in enumerate(candidates))
        prompt = (
            f'You are matching company names in an accounts receivable system.\n\n'
            f'Source name: "{name}"\n\n'
            f'Candidates from customer database:\n{cand_str}\n\n'
            f'Reply with ONLY the exact candidate text that refers to the same company, '
            f'or "no match" if none clearly match. No explanation.'
        )
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        result = response.text.strip().strip('"')
        if result.lower() == "no match":
            return None, "AI: no match"
        for c in candidates:
            if result.lower() == c.lower():
                return c, "AI"
        return None, f"AI: unrecognized result"
    except Exception as e:
        log.warning(f"AI match error for '{name}': {e}")
        return None, f"AI error"


# ═══════════════════════════════════════════════════════════════════════════════
# DATA LAYER
# ═══════════════════════════════════════════════════════════════════════════════

def find_latest_xlsx():
    files = sorted(AGING_DIR.glob("*.xlsx"), key=os.path.getmtime, reverse=True)
    if not files:
        cprint("[ERROR] No .xlsx file found in 'AR Aging Report' folder.", "red")
        sys.exit(1)
    if len(files) > 1:
        cprint("\n  [WARNING] Multiple files found in AR Aging Report:", "yellow")
        for f in files:
            cprint(f"    - {f.name}", "yellow")
        cprint("  Please delete old files after this run.\n", "yellow")
    else:
        print(f"  Report: {files[0].name}")
    return files[0]


def load_and_process(gemini_key=None):
    """Returns list of client dicts, sorted by priority."""
    xlsx = find_latest_xlsx()
    print("  Reading data...", flush=True)

    s3 = pd.read_excel(xlsx, sheet_name="Sheet3", dtype=str)
    s2 = pd.read_excel(xlsx, sheet_name="Sheet2", dtype=str)

    inv = s3[s3["Type"] == "Invoice"].copy()
    inv = inv[inv["Name"].notna()]
    inv["Aging"]        = pd.to_numeric(inv["Aging"],        errors="coerce")
    inv["Open Balance"] = pd.to_numeric(inv["Open Balance"], errors="coerce").fillna(0)
    inv["Date"]         = pd.to_datetime(inv["Date"],         errors="coerce")
    inv["Due Date"]     = pd.to_datetime(inv["Due Date"],     errors="coerce")

    overdue = inv[inv["Aging"] >= OVERDUE_MIN]
    if overdue.empty:
        return []

    agg = overdue.groupby("Name").agg(
        max_aging       = ("Aging",        "max"),
        overdue_balance = ("Open Balance", "sum"),
    ).reset_index()
    total_bal = inv.groupby("Name")["Open Balance"].sum().rename("total_balance")
    agg = agg.join(total_bal, on="Name")
    agg = agg.sort_values(["max_aging", "overdue_balance"], ascending=[False, False]).reset_index(drop=True)

    s2_valid = s2[s2["Main Email"].notna()].copy()
    s2_names = s2_valid["Customer"].tolist()

    clients = []
    ai_calls = 0
    for _, row in agg.iterrows():
        name      = row["Name"]
        max_aging = int(row["max_aging"])
        over_bal  = float(row["overdue_balance"])
        tot_bal   = float(row.get("total_balance", over_bal))

        # Per-invoice interest
        client_inv = overdue[overdue["Name"] == name]
        interest = sum(
            float(ir["Open Balance"]) * math.ceil((float(ir["Aging"]) - OVERDUE_MIN) / 30) * INTEREST_RATE
            for _, ir in client_inv.iterrows()
            if math.ceil((float(ir["Aging"]) - OVERDUE_MIN) / 30) > 0
        )

        # Fuzzy match
        top_matches = rfprocess.extract(name, s2_names, scorer=fuzz.WRatio, limit=8)
        match_method = "Fuzzy"
        if top_matches:
            best_name, best_score, _ = top_matches[0]
        else:
            best_name, best_score = None, 0

        # AI fallback for low-confidence
        if best_score < FUZZY_AUTO and gemini_key:
            candidates = [m[0] for m in top_matches[:6]]
            ai_match, method = ai_match_name(name, candidates, gemini_key)
            if ai_match:
                best_name    = ai_match
                match_method = method
                best_score   = 95   # AI-confirmed – treat as high confidence
            ai_calls += 1

        if best_name:
            email_raw = s2_valid.loc[s2_valid["Customer"] == best_name, "Main Email"].iloc[0] \
                        if best_name in s2_valid["Customer"].values else None
            email = email_raw.split(";")[0].strip() if email_raw else None
        else:
            email = None

        # All open invoices for table
        client_all = inv[inv["Name"] == name]
        invoices = [
            {
                "num":      str(ir["Num"]) if pd.notna(ir["Num"]) else "-",
                "date":     ir["Date"].strftime("%Y-%m-%d") if pd.notna(ir["Date"]) else "-",
                "amount":   float(ir["Open Balance"]),
                "due_date": ir["Due Date"].strftime("%Y-%m-%d") if pd.notna(ir["Due Date"]) else "-",
                "aging":    int(ir["Aging"]) if pd.notna(ir["Aging"]) else 0,
            }
            for _, ir in client_all.iterrows()
        ]

        clients.append({
            "name":            name,
            "email":           email,
            "match_score":     round(best_score),
            "match_method":    match_method,
            "matched_name":    best_name,
            "max_aging":       max_aging,
            "overdue_balance": round(over_bal, 2),
            "total_balance":   round(tot_bal, 2),
            "interest":        round(interest, 2),
            "invoices":        invoices,
            "is_extreme":      max_aging > EXTREME_THRESHOLD,
        })

    if ai_calls:
        print(f"  AI matching: {ai_calls} call(s) made to Gemini.")
    return clients


# ═══════════════════════════════════════════════════════════════════════════════
# HISTORY
# ═══════════════════════════════════════════════════════════════════════════════

def load_history():
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_history(history):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def get_template_key(name, max_aging, history):
    if name not in history:
        return "30"
    if max_aging >= 90:
        return "90"
    elif max_aging >= 60:
        return "60"
    return "30"


# ═══════════════════════════════════════════════════════════════════════════════
# EXCEL REPORT
# ═══════════════════════════════════════════════════════════════════════════════

def _col_widths(ws):
    from openpyxl.cell.cell import MergedCell
    for col in ws.columns:
        col_letter = None
        max_width  = 10
        for cell in col:
            if isinstance(cell, MergedCell):
                continue
            if col_letter is None:
                col_letter = cell.column_letter
            val_len = len(str(cell.value or ""))
            if val_len > max_width:
                max_width = val_len
        if col_letter:
            ws.column_dimensions[col_letter].width = min(max_width + 3, 52)


def generate_review_excel(clients, history, path=None):
    """
    Creates AR_Review_YYYYMMDD.xlsx with three sheets:
      - Pending      : sendable clients, user may delete rows to skip
      - Failed       : no email / low confidence
      - Match Review : full fuzzy/AI match confidence table
    Returns the file path.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    if path is None:
        path = SCRIPT_DIR / f"AR_Review_{today_str}.xlsx"

    wb = Workbook()

    # ── Unified colour palette ──────────────────────────────────────────────
    # Each colour is ONE hex used for BOTH instruction rows and data rows.
    # Light backgrounds use dark text; dark backgrounds use white text.
    def fill(hex_):
        return PatternFill("solid", fgColor=hex_)
    def center():
        return Alignment(horizontal="center", vertical="center", wrap_text=True)

    # Colours (instruction row = data row = same hex)
    C_BLUE_DARK  = fill("2C3E50")  # column headers only (white text)
    C_ORANGE     = fill("FAD7A0")  # extreme aging — STEP 2 row + data rows
    C_YELLOW     = fill("FCF3CF")  # first-time / review — note rows + data rows
    C_RED        = fill("F5B7B1")  # failed / skipped — instruction row + data rows
    C_GRAY       = fill("F2F3F4")  # neutral STEP rows

    # Text colours paired to backgrounds above
    T_WHITE      = "FFFFFF"
    T_ORANGE     = "7E5109"   # dark brown on orange
    T_YELLOW     = "7D6608"   # dark amber on yellow
    T_RED        = "922B21"   # dark red on red
    T_GRAY       = "555555"   # dark gray on gray

    def instr_row(ws, text, bg_fill, txt_color, bold=False, ncols=9, height=20):
        """Append a full-width instruction row with consistent colour."""
        ws.append([text] + [""] * (ncols - 1))
        r = ws.max_row
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=ncols)
        cell = ws.cell(r, 1)
        cell.fill      = bg_fill
        cell.font      = Font(bold=bold, italic=not bold, color=txt_color, size=10 if not bold else 11)
        cell.alignment = Alignment(horizontal="left", vertical="center", wrap_text=True, indent=1)
        ws.row_dimensions[r].height = height

    # ── PENDING sheet ──────────────────────────────────────────────────────
    ws_p = wb.active
    ws_p.title = "Pending"
    ws_p.sheet_properties.tabColor = "2C3E50"

    instr_row(ws_p, "STEP 1  |  Check the 'Match Review' sheet first to verify email addresses for yellow rows.", C_GRAY,   T_GRAY,   bold=True, height=22)
    instr_row(ws_p, "STEP 2  |  Review orange rows below (overdue > 365 days). Delete rows you do NOT want to email.", C_ORANGE, T_ORANGE, bold=True, height=22)
    instr_row(ws_p, "STEP 3  |  Delete any other rows to skip. Then SAVE this file and press Enter in the console.", C_GRAY,   T_GRAY,   bold=True, height=22)
    ws_p.append([])

    p_headers = [
        "Client Name (Sheet3)", "Email", "Template",
        "Max Aging (days)", "Overdue Balance (CAD)", "Interest Accrued (CAD)",
        "Status", "Match Score", "Note (optional)",
    ]
    ws_p.append(p_headers)
    ws_p.row_dimensions[ws_p.max_row].height = 28
    for cell in ws_p[ws_p.max_row]:
        cell.font      = Font(bold=True, color=T_WHITE)
        cell.fill      = C_BLUE_DARK
        cell.alignment = center()

    sendable = [c for c in clients if c["email"] and c["match_score"] >= FUZZY_WARN]
    for c in sendable:
        tpl    = get_template_key(c["name"], c["max_aging"], history)
        status = "NEW" if c["name"] not in history else "Returning"
        ws_p.append([
            c["name"], c["email"], tpl + "d",
            c["max_aging"], c["overdue_balance"], c["interest"],
            status, c["match_score"], "",
        ])
        row_fill = C_ORANGE if c["is_extreme"] else (C_YELLOW if status == "NEW" else None)
        if row_fill:
            for cell in ws_p[ws_p.max_row]:
                cell.fill = row_fill

    ws_p.append([])
    instr_row(ws_p, "LEGEND:  Orange = Overdue > 365 days (verify before sending)   |   Yellow = First-time reminder (never emailed before)", C_GRAY, T_GRAY)
    _col_widths(ws_p)

    # ── FAILED sheet ───────────────────────────────────────────────────────
    ws_f = wb.create_sheet("Failed")
    ws_f.sheet_properties.tabColor = "F5B7B1"

    instr_row(ws_f, "These clients could NOT be matched to a reliable email address and were excluded from Pending.", C_RED, T_RED, bold=True, ncols=6, height=22)
    instr_row(ws_f, "If you know the correct email: manually add a row to the Pending sheet with their details.", C_RED, T_RED, ncols=6)
    ws_f.append([])

    f_headers = [
        "Client Name", "Max Aging (days)", "Overdue Balance (CAD)",
        "Match Score", "Best Candidate Found (Sheet2)", "Reason",
    ]
    ws_f.append(f_headers)
    ws_f.row_dimensions[ws_f.max_row].height = 28
    for cell in ws_f[ws_f.max_row]:
        cell.font      = Font(bold=True, color=T_WHITE)
        cell.fill      = C_BLUE_DARK
        cell.alignment = center()

    failed = [c for c in clients if not c["email"] or c["match_score"] < FUZZY_WARN]
    for c in failed:
        reason = "No email match found" if not c["email"] or c["match_score"] == 0 \
                 else f"Low confidence ({c['match_score']} < {FUZZY_WARN})"
        ws_f.append([
            c["name"], c["max_aging"], c["overdue_balance"],
            c["match_score"], c["matched_name"] or "—", reason,
        ])
        for cell in ws_f[ws_f.max_row]:
            cell.fill = C_RED
    _col_widths(ws_f)

    # ── MATCH REVIEW sheet ─────────────────────────────────────────────────
    ws_m = wb.create_sheet("Match Review")
    ws_m.sheet_properties.tabColor = "FCF3CF"

    instr_row(ws_m, "This sheet shows how the system matched client names (Sheet3) to email addresses (Sheet2).", C_GRAY,   T_GRAY,   bold=True, height=22)
    instr_row(ws_m, "White rows  (score >= 90): auto-matched — no action needed.", C_GRAY,   T_GRAY)
    instr_row(ws_m, "Yellow rows (score 70-89): review recommended — check that 'Client Name' and 'Best Match' refer to the same company.", C_YELLOW, T_YELLOW)
    instr_row(ws_m, "  > If the email looks WRONG:   go to the Pending sheet, find that client, and correct the Email cell manually.", C_YELLOW, T_YELLOW)
    instr_row(ws_m, "  > If the email looks CORRECT: no action needed.", C_YELLOW, T_YELLOW)
    instr_row(ws_m, "Red rows    (score < 70):  excluded from Pending. See the Failed sheet.", C_RED,    T_RED)
    ws_m.append([])

    m_headers = [
        "Client Name (Sheet3)", "Best Match (Sheet2)", "Email Found",
        "Score", "Method", "Status",
    ]
    ws_m.append(m_headers)
    ws_m.row_dimensions[ws_m.max_row].height = 28
    for cell in ws_m[ws_m.max_row]:
        cell.font      = Font(bold=True, color=T_WHITE)
        cell.fill      = C_BLUE_DARK
        cell.alignment = center()

    for c in clients:
        if c["match_score"] >= FUZZY_AUTO:
            status, row_fill = "Auto-matched — no action needed", None
        elif c["match_score"] >= FUZZY_WARN:
            status, row_fill = "Review recommended — verify email in Pending sheet", C_YELLOW
        else:
            status, row_fill = "Excluded — see Failed sheet", C_RED
        ws_m.append([
            c["name"], c["matched_name"] or "—", c["email"] or "—",
            c["match_score"], c["match_method"], status,
        ])
        if row_fill:
            for cell in ws_m[ws_m.max_row]:
                cell.fill = row_fill
    _col_widths(ws_m)

    wb.save(path)
    log.info(f"Review Excel saved: {path.name}  (Pending={len(sendable)}, Failed={len(failed)})")
    return path


def append_sent_sheet(path, sent_clients, smtp_failed):
    """Add/replace 'Sent' and update 'Failed' sheet in an existing report."""
    from openpyxl import load_workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    wb = load_workbook(path)

    def fill(hex_):
        return PatternFill("solid", fgColor=hex_)
    def hdr_font():
        return Font(bold=True, color="FFFFFF")
    def center():
        return Alignment(horizontal="center", vertical="center", wrap_text=True)

    # Remove old Sent sheet if exists
    if "Sent" in wb.sheetnames:
        del wb["Sent"]

    ws_s = wb.create_sheet("Sent")
    ws_s.row_dimensions[1].height = 30
    s_headers = [
        "Client Name", "Email", "Template",
        "Max Aging (days)", "Overdue Balance (CAD)", "Interest (CAD)", "Sent At",
    ]
    ws_s.append(s_headers)
    for cell in ws_s[1]:
        cell.font, cell.fill, cell.alignment = hdr_font(), fill("1E8449"), center()
    for c in sent_clients:
        ws_s.append([
            c["name"], c["email"], c["template"] + "d",
            c["max_aging"], c["overdue_balance"], c["interest"], c["sent_at"],
        ])
        for cell in ws_s[ws_s.max_row]:
            cell.fill = fill("D5F5E3")
    for col in ws_s.columns:
        ws_s.column_dimensions[col[0].column_letter].width = min(
            max(len(str(cell.value or "")) for cell in col) + 3, 52)

    # Append SMTP failures to Failed sheet
    if smtp_failed and "Failed" in wb.sheetnames:
        ws_f = wb["Failed"]
        for c in smtp_failed:
            ws_f.append([
                c["name"], c["max_aging"], c["overdue_balance"],
                c["match_score"], c.get("matched_name") or "—",
                c.get("fail_reason", "SMTP send error"),
            ])
            for cell in ws_f[ws_f.max_row]:
                cell.fill = fill("FADBD8")

    wb.save(path)
    log.info(f"Sent sheet added to {path.name}  ({len(sent_clients)} sent, {len(smtp_failed)} SMTP failed)")


def read_pending_from_excel(path):
    """Read remaining client names from the Pending sheet (after user edits).
    Skips instruction rows at the top and legend rows at the bottom."""
    df = pd.read_excel(path, sheet_name="Pending", dtype=str, header=None)
    # Find the real header row: the row containing "Client Name (Sheet3)"
    header_row = None
    for i, row in df.iterrows():
        if any("Client Name (Sheet3)" in str(v) for v in row.values):
            header_row = i
            break
    if header_row is None:
        return set()
    df.columns = df.iloc[header_row]
    df = df.iloc[header_row + 1:].reset_index(drop=True)
    # Keep only rows with a valid email
    df = df[df["Email"].notna() & df["Email"].astype(str).str.contains("@", na=False)]
    return set(df["Client Name (Sheet3)"].dropna().astype(str).str.strip().tolist())


# ═══════════════════════════════════════════════════════════════════════════════
# EMAIL BUILDING
# ═══════════════════════════════════════════════════════════════════════════════

def _invoice_table(invoices):
    rows = ""
    for iv in invoices:
        hl = ' style="background-color:#fff3cd;"' if iv["aging"] >= OVERDUE_MIN else ""
        aging_lbl = f"{iv['aging']}d" if iv["aging"] > 0 else "Current"
        rows += (
            f"<tr{hl}>"
            f"<td>{iv['num']}</td>"
            f"<td>{iv['date']}</td>"
            f"<td style='text-align:right;'>CAD {iv['amount']:,.2f}</td>"
            f"<td>{iv['due_date']}</td>"
            f"<td style='text-align:center;'>{aging_lbl}</td>"
            "</tr>"
        )
    return (
        "<table border='1' cellpadding='6' cellspacing='0' "
        "style='border-collapse:collapse;font-size:13px;width:100%;margin-top:12px;'>"
        "<thead style='background-color:#2c3e50;color:white;'>"
        "<tr><th>Invoice #</th><th>Invoice Date</th><th>Amount</th>"
        "<th>Due Date</th><th>Days Past Due</th></tr>"
        "</thead>"
        f"<tbody>{rows}</tbody></table>"
    )


def _interest_block(interest, max_aging):
    if interest <= 0:
        return ""
    months = math.ceil((max_aging - OVERDUE_MIN) / 30)
    return (
        "<p style='color:#c0392b;font-weight:bold;font-size:14px;"
        "border:2px solid #c0392b;padding:10px;border-radius:4px;margin-top:14px;'>"
        f"&#9888; Interest Accrued: CAD {interest:,.2f}<br>"
        f"<span style='font-size:12px;font-weight:normal;'>"
        f"({months} month(s) overdue &times; 1% per month, from date of overdue)"
        "</span></p>"
    )


def build_email(client, template_key, history):
    name      = client["name"]
    tot_bal   = client["total_balance"]
    over_bal  = client["overdue_balance"]
    interest  = client["interest"]
    max_aging = client["max_aging"]
    inv_count = sum(1 for i in client["invoices"] if i["aging"] >= OVERDUE_MIN)
    today_fmt = datetime.now().strftime("%B %d, %Y")
    tbl       = _invoice_table(client["invoices"])
    iblk      = _interest_block(interest, max_aging)
    prev_date = history.get(name, {}).get("last_sent", "our previous notice")

    if template_key == "30":
        subject = f"Friendly Reminder: Outstanding Balance – {name}"
        body = f"""
        <p>Date: {today_fmt}</p>
        <p>Dear {name},</p>
        <p>This is a friendly reminder that you currently have
        <b>{inv_count} overdue invoice(s)</b> with an outstanding balance of
        <b>CAD {tot_bal:,.2f}</b>.
        We have provided a statement below summarizing all unpaid invoices.</p>
        <p>Please review your records. If you have already made these payments or have
        any questions, please contact us at <b>555-000-0000</b>.</p>
        {iblk}{tbl}
        <p>We value our continued cooperation and look forward to hearing from you soon.</p>
        {SENDER_SIG}"""

    elif template_key == "60":
        subject = f"Second Notice: Outstanding Balance – {name}"
        body = f"""
        <p>Date: {today_fmt}</p>
        <p>Dear {name},</p>
        <p>Although we contacted you on <b>{prev_date}</b> regarding the outstanding balance,
        we have not yet received a response.</p>
        <p>As of today, your total outstanding balance is <b>CAD {tot_bal:,.2f}</b>,
        with an overdue portion of <b>CAD {over_bal:,.2f}</b>.</p>
        <p>As outlined in our contract, overdue payments are subject to
        <b>interest at 1% per month</b> from the date the payment became overdue.</p>
        {iblk}{tbl}
        <p>Please make payment as soon as possible or contact us at
        <b>555-000-0000</b> to discuss arrangements.</p>
        {SENDER_SIG}"""

    else:  # 90
        subject = f"Final Notice: Overdue Account – {name}"
        body = f"""
        <p>Date: {today_fmt}</p>
        <p>Dear {name},</p>
        <p>Despite our previous correspondence, the following invoices remain outstanding.
        Your total outstanding balance is <b>CAD {tot_bal:,.2f}</b>.</p>
        {iblk}{tbl}
        <p>Please contact us <b>immediately</b>. As outlined in our contract, we reserve
        the right to <b>suspend our services</b> to your company. Failure to resolve
        this balance may result in government penalties and affect your credit record.</p>
        <p>Please remit full payment or contact us at <b>555-000-0000</b> immediately.</p>
        {SENDER_SIG}"""

    html = (
        "<!DOCTYPE html><html><body "
        "style='font-family:Arial,sans-serif;font-size:14px;color:#333;"
        "max-width:720px;margin:auto;padding:20px;'>"
        f"{body}</body></html>"
    )
    return subject, html


# ═══════════════════════════════════════════════════════════════════════════════
# SMTP
# ═══════════════════════════════════════════════════════════════════════════════

def connect_smtp(password=None):
    client = EmailClient.from_settings(SENDER_NAME, password=password)
    client.connect_imap()
    log.info("Email client connected (%s)", shared_settings.EMAIL_AUTH_MODE)
    return client


def request_email_password():
    """Prompt only while the legacy password transport is active."""
    if shared_settings.EMAIL_AUTH_MODE == "gmail_api":
        print("  Using Gmail API OAuth authorization.")
        return None
    print("  (Password will not be shown as you type)")
    return getpass.getpass(f"  SMTP Password ({SMTP_USER}): ").strip()


def send_email(conn, to_addr, subject, html_body,
               attachment_bytes: bytes = None, attachment_name: str = None):
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.mime.base import MIMEBase
    from email import encoders

    outer = MIMEMultipart("mixed")
    outer["From"]    = f"{SENDER_NAME} <{SMTP_USER}>"
    outer["To"]      = to_addr
    outer["Subject"] = subject

    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(html_body, "html", "utf-8"))
    outer.attach(alt)

    if attachment_bytes and attachment_name:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(attachment_bytes)
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f'attachment; filename="{attachment_name}"')
        outer.attach(part)

    raw = outer.as_bytes()
    conn.send_raw(raw, to_addr)


# ═══════════════════════════════════════════════════════════════════════════════
# MENU FLOWS
# ═══════════════════════════════════════════════════════════════════════════════

def flow_preview():
    print()
    gemini_key = setup_gemini_key()
    clients    = load_and_process(gemini_key)
    if not clients:
        cprint("  No overdue clients (>=30 days) found.", "green")
        return

    history = load_history()
    path    = generate_review_excel(clients, history)

    sendable = [c for c in clients if c["email"] and c["match_score"] >= FUZZY_WARN]
    failed   = [c for c in clients if not c["email"] or c["match_score"] < FUZZY_WARN]
    extreme  = [c for c in sendable if c["is_extreme"]]
    total_over = sum(c["overdue_balance"] for c in sendable)
    total_int  = sum(c["interest"]        for c in sendable)

    cprint(f"\n  Review file saved: {path.name}", "cyan")
    print(f"\n  Summary:")
    print(f"    Ready to send   : {len(sendable)} client(s)")
    print(f"    Extreme aging   : {len(extreme)} client(s)  (>365d, highlighted orange in Pending sheet)")
    cprint(f"    Cannot send     : {len(failed)} client(s)  (see Failed sheet)", "red")
    print(f"    Total overdue   : CAD {total_over:,.2f}")
    cprint(f"    Total interest  : CAD {total_int:,.2f}", "red" if total_int > 0 else None)
    print(f"\n  Open {path.name} to review details.")
    print()


def flow_send():
    print()
    gemini_key = setup_gemini_key()
    clients    = load_and_process(gemini_key)
    if not clients:
        cprint("  No overdue clients (>=30 days) found.", "green")
        return

    history = load_history()
    path    = generate_review_excel(clients, history)

    sendable = [c for c in clients if c["email"] and c["match_score"] >= FUZZY_WARN]
    failed   = [c for c in clients if not c["email"] or c["match_score"] < FUZZY_WARN]

    if not sendable:
        cprint("  No clients to send to.", "yellow")
        return

    extreme   = [c for c in sendable if c["is_extreme"]]
    total_over = sum(c["overdue_balance"] for c in sendable)
    total_int  = sum(c["interest"]        for c in sendable)

    cprint(f"\n  Review file saved: {path.name}", "cyan")
    print(f"\n  Pending:")
    print(f"    Ready to send   : {len(sendable)} client(s)")
    print(f"    Extreme aging   : {len(extreme)} client(s)  (orange rows)")
    cprint(f"    Cannot send     : {len(failed)} client(s)  (Failed sheet)", "red")
    print(f"    Total overdue   : CAD {total_over:,.2f}")
    cprint(f"    Total interest  : CAD {total_int:,.2f}", "red" if total_int > 0 else None)
    print()
    cprint("  Open the file, review the Pending sheet.", "yellow")
    cprint("  Delete any rows you do NOT want to send, then save and close the file.", "yellow")
    input("  Press Enter when ready to continue... ")

    # Re-read which clients remain in Pending after user edits
    try:
        keep_names = read_pending_from_excel(path)
    except Exception as e:
        cprint(f"  [ERROR] Could not read back the review file: {e}", "red")
        return

    final = [c for c in sendable if c["name"] in keep_names]
    removed = len(sendable) - len(final)

    print(f"\n  Final send list: {len(final)} client(s)"
          + (f"  ({removed} removed by review)" if removed else ""))
    if not final:
        cprint("  All clients removed. Nothing to send.", "yellow")
        return

    print(f"\n  Proceed to send {len(final)} email(s)? (Y/N): ", end="")
    if input().strip().upper() != "Y":
        print("  Cancelled.")
        return

    password = request_email_password()
    if shared_settings.EMAIL_AUTH_MODE == "password" and not password:
        cprint("  Empty password – cancelled.", "red")
        return

    sent_clients = []
    smtp_failed  = []
    sent = fail = 0

    try:
        srv = connect_smtp(password)
        for c in final:
            tpl  = get_template_key(c["name"], c["max_aging"], history)
            subj, html = build_email(c, tpl, history)
            try:
                send_email(srv, c["email"], subj, html)
                now     = datetime.now().strftime("%Y-%m-%d")
                sent_at = datetime.now().strftime("%Y-%m-%d %H:%M")
                entry = history.setdefault(c["name"], {"first_sent": now})
                entry.update({
                    "last_sent":       now,
                    "last_template":   tpl + "d",
                    "sent_count":      entry.get("sent_count", 0) + 1,
                    "email":           c["email"],
                    "max_aging":       c["max_aging"],
                    "overdue_balance": c["overdue_balance"],
                })
                sent_clients.append({**c, "template": tpl, "sent_at": sent_at})
                cprint(f"  OK  [{tpl}d] -> {c['email']}  ({c['name']})", "green")
                log.info(f"SENT [{tpl}d] {c['email']} ({c['name']}) bal={c['overdue_balance']:.2f}")
                sent += 1
            except Exception as e:
                smtp_failed.append({**c, "fail_reason": f"SMTP error: {e}"})
                cprint(f"  FAIL -> {c['email']}  ({c['name']}): {e}", "red")
                log.error(f"FAILED {c['email']} ({c['name']}): {e}")
                fail += 1
        srv.quit()
    except Exception as e:
        cprint(f"\n  [SMTP ERROR] Cannot connect: {e}", "red")
        log.error(f"SMTP connection error: {e}")
        return

    save_history(history)
    append_sent_sheet(path, sent_clients, smtp_failed)

    cprint(f"\n  Done.  Sent: {sent}   Failed: {fail}", "green")
    log.info(f"Run complete. Sent={sent} Failed={fail}")
    cprint(f"  Report updated: {path.name}  (Sent sheet added)", "cyan")
    print(f"  Log: logs/log_{today_str}.txt\n")


def flow_history():
    history = load_history()
    if not history:
        cprint("  No history found. No emails sent yet.", "yellow")
        return
    cprint(f"\n  Sent History ({len(history)} client(s))", "cyan")
    print(f"  {'Client':<44} {'First Sent':<12} {'Last Sent':<12} {'#':>3}  Template")
    print(f"  {'-'*44} {'-'*12} {'-'*12} {'-'*3}  {'-'*10}")
    for name, info in sorted(history.items(), key=lambda x: x[1].get("last_sent", ""), reverse=True):
        print(
            f"  {name[:43]:<44} "
            f"{info.get('first_sent',''):<12} "
            f"{info.get('last_sent',''):<12} "
            f"{info.get('sent_count',0):>3}  "
            f"{info.get('last_template','')}"
        )
    print()


# ═══════════════════════════════════════════════════════════════════════════════
# INVOICE – Master Reader
# ═══════════════════════════════════════════════════════════════════════════════

def _resolve_master_path():
    for p in MASTER_PATHS:
        if os.path.exists(p):
            return p
    return None

def _normalize_header(val) -> str:
    """Collapse newlines/extra spaces in cell header values."""
    if val is None:
        return ""
    return " ".join(str(val).split())

def read_invoice_clients(master_password: str) -> list[dict]:
    """
    Decrypt and read Master Client List.
    Returns list of dicts for Client Type = Monthly clients.
    Handles 'B/B+ Charge' header that may span two lines in Excel.
    """
    try:
        import msoffcrypto
        import openpyxl
    except ImportError as e:
        cprint(f"  [ERROR] Missing package: {e}", "red")
        return []

    path = _resolve_master_path()
    if not path:
        cprint("  [ERROR] Master Client List not found on any known path.", "red")
        return []

    # Decrypt to temp file
    try:
        with open(path, "rb") as f:
            of = msoffcrypto.OfficeFile(f)
            tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
            if of.is_encrypted():
                of.load_key(password=master_password)
                of.decrypt(tmp)
                tmp.close()
            else:
                import shutil
                tmp.close()
                shutil.copyfile(path, tmp.name)
    except Exception as e:
        cprint(f"  [ERROR] Decrypt failed: {e}", "red")
        return []

    TARGET_SHEETS = ["master", "Alex's", "mk"]
    clients = []
    seen = set()

    try:
        wb = openpyxl.load_workbook(tmp.name, read_only=True, data_only=True)
        for sheet_name in wb.sheetnames:
            if sheet_name.strip().lower() not in TARGET_SHEETS:
                continue
            ws = wb[sheet_name]
            rows = list(ws.iter_rows(values_only=True))
            if not rows:
                continue

            # Build header map (normalize to handle newline in B/B+ Charge)
            raw_headers = [_normalize_header(h) for h in rows[0]]
            hmap = {h: i for i, h in enumerate(raw_headers) if h}

            def get(row, field):
                idx = hmap.get(field)
                return row[idx] if idx is not None and idx < len(row) else None

            for row in rows[1:]:
                legal = get(row, "Legal Name")
                if not legal or str(legal).strip() == "":
                    continue
                client_type = str(get(row, "Client Type") or "").strip()
                if client_type.lower() != "monthly":
                    continue
                key = str(legal).strip().upper()
                if key in seen:
                    continue
                seen.add(key)

                # B/B+ Charge header may be normalized from "B/B+\nCharge"
                bb_key = next((k for k in hmap if "b/b" in k.lower() and "charge" in k.lower()), None)
                bb_raw = row[hmap[bb_key]] if bb_key and hmap[bb_key] < len(row) else None

                try:
                    bb_charge = float(bb_raw or 0)
                except (ValueError, TypeError):
                    bb_charge = 0.0
                try:
                    monthly_fee = float(get(row, "Monthly Fee") or 0)
                except (ValueError, TypeError):
                    monthly_fee = 0.0

                jurisdiction = str(get(row, "Incorporation Jurisdiction") or "").strip()
                province_key = _resolve_province(jurisdiction)

                raw_email = str(get(row, "Owner Email") or "").strip()
                first_email = raw_email.split(";")[0].split(",")[0].strip()

                clients.append({
                    "legal_name":    str(legal).strip(),
                    "owner_name":    str(get(row, "Owner Name") or "").strip(),
                    "owner_email":   first_email,
                    "language":      str(get(row, "Language") or "").strip().upper(),
                    "monthly_fee":   monthly_fee,
                    "bb_charge":     bb_charge,
                    "jurisdiction":  jurisdiction,
                    "province_key":  province_key,
                })
        wb.close()
    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass

    return clients


# ═══════════════════════════════════════════════════════════════════════════════
# INVOICE – Number Counter
# ═══════════════════════════════════════════════════════════════════════════════

def _load_invoice_counter() -> int:
    if INVOICE_COUNTER_FILE.exists():
        try:
            return int(json.loads(INVOICE_COUNTER_FILE.read_text())["next"])
        except Exception:
            pass
    return INVOICE_START

def _save_invoice_counter(next_num: int) -> None:
    INVOICE_COUNTER_FILE.write_text(json.dumps({"next": next_num}, indent=2))


# ═══════════════════════════════════════════════════════════════════════════════
# INVOICE – Tax Calculation
# ═══════════════════════════════════════════════════════════════════════════════

def calc_invoice_taxes(monthly_fee: float, bb_charge: float, province_key: str) -> dict:
    rules = INVOICE_TAX_RULES.get(province_key, INVOICE_TAX_DEFAULT)
    subtotal  = monthly_fee + bb_charge
    gst       = round(subtotal * rules["gst"], 2)
    pst       = round(subtotal * rules["pst"], 2)
    tax_total = round(gst + pst, 2)
    total     = round(subtotal + tax_total, 2)
    return {
        "subtotal":  subtotal,
        "gst":       gst,
        "pst":       pst,
        "tax_total": tax_total,
        "total":     total,
        "gst_rate":  rules["gst"],
        "pst_rate":  rules["pst"],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# INVOICE – PDF Generator (fpdf2)
# ═══════════════════════════════════════════════════════════════════════════════

def generate_invoice_pdf(client: dict, invoice_num: int, taxes: dict) -> bytes:
    """Generate invoice PDF in memory and return as bytes."""
    try:
        from fpdf import FPDF
    except ImportError:
        raise RuntimeError("fpdf2 not installed. Run: pip install fpdf2")

    now       = datetime.now()
    inv_date  = now.strftime("%B %d, %Y")
    period    = f"{now.year}.{now.month:02d}"

    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    # ── Header ──────────────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 10, "Example CPA Firm P.C. Inc.", ln=True, align="C")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 5, "100 Example Street, Saskatoon, SK S7A 0A0", ln=True, align="C")
    pdf.cell(0, 5, "info@example-firm.com  |  infosk@example-firm.com  |  555-000-0000", ln=True, align="C")
    pdf.ln(6)

    # ── Divider ─────────────────────────────────────────────────────────────
    pdf.set_draw_color(47, 84, 150)
    pdf.set_line_width(0.8)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(4)

    # ── Invoice title + meta ─────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, "INVOICE", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(95, 6, f"Invoice #:  {invoice_num}", ln=False)
    pdf.cell(95, 6, f"Date:  {inv_date}", ln=True)
    pdf.ln(4)

    # ── Bill to ─────────────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Invoice To:", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 6, client["legal_name"], ln=True)
    if client["owner_name"]:
        pdf.cell(0, 6, f"Attn: {client['owner_name']}", ln=True)
    pdf.ln(4)

    # ── Line items table header ──────────────────────────────────────────────
    pdf.set_fill_color(47, 84, 150)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(110, 7, "Description", border=1, fill=True)
    pdf.cell(40,  7, "Amount (CAD)", border=1, fill=True, align="R")
    pdf.ln()
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 10)

    # B/B+ fee row (only if > 0)
    if client["bb_charge"] > 0:
        pdf.cell(110, 7, "B/B+ Service Fee", border=1)
        pdf.cell(40,  7, f"${client['bb_charge']:,.2f}", border=1, align="R")
        pdf.ln()

    # Monthly accounting service
    pdf.cell(110, 7, f"Accounting Service {period}", border=1)
    pdf.cell(40,  7, f"${client['monthly_fee']:,.2f}", border=1, align="R")
    pdf.ln()

    pdf.ln(2)

    # ── Totals ───────────────────────────────────────────────────────────────
    def total_row(label, amount, bold=False):
        pdf.set_font("Helvetica", "B" if bold else "", 10)
        pdf.cell(110, 6, "")
        pdf.cell(20,  6, label, align="R")
        pdf.cell(20,  6, f"${amount:,.2f}", align="R", border=1)
        pdf.ln()

    total_row("Subtotal:", taxes["subtotal"])
    if taxes["gst"] > 0:
        label = "HST:" if taxes["pst_rate"] == 0.0 and taxes["gst_rate"] >= 0.12 else f"GST ({taxes['gst_rate']*100:.0f}%):"
        total_row(label, taxes["gst"])
    if taxes["pst"] > 0:
        total_row(f"PST ({taxes['pst_rate']*100:.0f}%):", taxes["pst"])
    total_row("TOTAL:", taxes["total"], bold=True)

    pdf.ln(6)

    # ── Payment info ─────────────────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, "Payment Methods:", ln=True)
    pdf.set_font("Helvetica", "", 10)
    for line in [
        "E-Transfer: info@example-firm.com",
        "Cheque payable to: Example CPA Firm P.C. Inc.",
    ]:
        pdf.cell(0, 5, line, ln=True)

    pdf.ln(4)
    pdf.set_font("Helvetica", "I", 9)
    pdf.set_text_color(120, 120, 120)
    pdf.cell(0, 5, "Thank you for your continued business.", ln=True)

    return bytes(pdf.output())


# ═══════════════════════════════════════════════════════════════════════════════
# INVOICE – Email Builder
# ═══════════════════════════════════════════════════════════════════════════════

INVOICE_TPL_DIR = SCRIPT_DIR / "Template"

def _load_invoice_email_template(lang: str) -> tuple[str | None, str | None]:
    """
    Load invoice email body from .docx template if available.
    Returns (subject, html_body) or (None, None) if not found.
    Template files: Template/Monthly Invoice - CN.docx / Monthly Invoice - EN.docx
    Placeholders: {NAME}, {COMPANY}, {INVOICE_NO}, {PERIOD}
    """
    suffix = "CN" if lang == "CN" else "EN"
    tpl_path = INVOICE_TPL_DIR / f"Monthly Invoice - {suffix}.docx"
    if not tpl_path.exists():
        return None, None
    try:
        from docx import Document as DocxDocument
        doc = DocxDocument(str(tpl_path))
        subject = None
        parts   = []
        for para in doc.paragraphs:
            t = para.text.strip()
            if not t:
                continue
            if subject is None and t.lower().startswith("subject:"):
                subject = t[8:].strip()
            else:
                parts.append(f"<p>{para.text}</p>")
        html = '<div style="font-family:Arial;font-size:14px;color:#333;">' + "".join(parts) + "</div>"
        return subject, html
    except Exception as e:
        log.warning(f"Invoice template load failed: {e}")
        return None, None


def build_invoice_email(client: dict, invoice_num: int, taxes: dict) -> tuple[str, str]:
    """Return (subject, html_body) for the invoice email."""
    lang    = "CN" if client.get("language", "EN").upper() in ("CN", "CHINESE", "ZH") else "EN"
    name    = client["owner_name"] or client["legal_name"]
    company = client["legal_name"]
    now     = datetime.now()
    period  = f"{now.year}.{now.month:02d}"

    # Try .docx template first
    tpl_subj, tpl_body = _load_invoice_email_template(lang)
    if tpl_body:
        for k, v in {"{NAME}": name, "{COMPANY}": company,
                     "{INVOICE_NO}": str(invoice_num), "{PERIOD}": period}.items():
            tpl_body = tpl_body.replace(k, v)
            if tpl_subj:
                tpl_subj = tpl_subj.replace(k, v)
        subject = tpl_subj or (f"发票 – {company}" if lang == "CN" else f"Invoice – {company}")
        return subject, tpl_body

    # Fallback built-in HTML
    if lang == "CN":
        subject = f"发票 – {company} – {period}"
        body = f"""<div style="font-family:Arial;font-size:14px;color:#333;">
<p>尊敬的 {name}，</p>
<p>请查收 <b>{company}</b> {period} 月度服务发票（发票编号：{invoice_num}），已作为附件随邮件发送。</p>
<p>如有任何问题，请随时联系我们。</p>
<br><p>谢谢！<br><b>Example CPA Firm P.C. Inc.</b><br>infosk@example-firm.com<br>555-000-0000</p>
</div>"""
    else:
        subject = f"Invoice – {company} – {period}"
        body = f"""<div style="font-family:Arial;font-size:14px;color:#333;">
<p>Dear {name},</p>
<p>Please find attached the invoice for <b>{company}</b> for the period {period} (Invoice #{invoice_num}).</p>
<p>Please do not hesitate to contact us if you have any questions.</p>
<br><p>Best regards,<br><b>Example CPA Firm P.C. Inc.</b><br>infosk@example-firm.com<br>555-000-0000</p>
</div>"""
    return subject, body


# ═══════════════════════════════════════════════════════════════════════════════
# INVOICE – Main Flow
# ═══════════════════════════════════════════════════════════════════════════════

def flow_invoices():
    """[5] Send Monthly Invoices – read Master, generate PDF per client, email."""
    cprint("\n  Monthly Invoice Sender", "cyan")
    print("  " + "─" * 50)

    # Check fpdf2 available
    try:
        import fpdf  # noqa
    except ImportError:
        cprint("  [ERROR] fpdf2 not installed. Run: pip install fpdf2", "red")
        return

    # Read master
    print("  (Password will not be shown as you type)")
    master_pass = getpass.getpass(f"  Master File Password: ").strip()
    if not master_pass:
        master_pass = MASTER_PASSWORD

    print("\n  Reading Master Client List (Monthly clients)...")
    clients = read_invoice_clients(master_pass)
    if not clients:
        cprint("  No Monthly clients found or read failed.", "yellow")
        return

    # Filter clients with valid email and fee > 0
    valid = [c for c in clients if c["owner_email"] and "@" in c["owner_email"]
             and (c["monthly_fee"] > 0 or c["bb_charge"] > 0)]
    no_email = [c for c in clients if not c["owner_email"] or "@" not in c["owner_email"]]

    print(f"\n  Clients found:       {len(clients)}")
    print(f"  Valid (has email):   {len(valid)}")
    if no_email:
        cprint(f"  No email (skipped):  {len(no_email)}", "yellow")

    if not valid:
        cprint("  Nothing to send.", "yellow")
        return

    # Preview table
    print(f"\n  {'#':<4} {'Legal Name':<35} {'Email':<30} {'Fee':>7} {'B/B+':>6} {'Tax':>7} {'Total':>8} {'Prov'}")
    print("  " + "─" * 105)
    invoice_start = _load_invoice_counter()
    for i, c in enumerate(valid):
        t = calc_invoice_taxes(c["monthly_fee"], c["bb_charge"], c["province_key"])
        prov = c["province_key"] or f"({c['jurisdiction'][:6]})"
        print(f"  {i+1:<4} {c['legal_name'][:34]:<35} {c['owner_email'][:29]:<30} "
              f"${c['monthly_fee']:>5.0f} ${c['bb_charge']:>4.0f} ${t['tax_total']:>5.2f} ${t['total']:>6.2f} {prov}")

    now = datetime.now()
    period = f"{now.year}.{now.month:02d}"
    print(f"\n  Period: {period}  |  Invoice numbers: {invoice_start} – {invoice_start + len(valid) - 1}")

    ch = input(f"\n  Send {len(valid)} invoice(s)? (Y/N): ").strip().upper()
    if ch != "Y":
        print("  Cancelled.")
        return

    smtp_pass = request_email_password()
    if shared_settings.EMAIL_AUTH_MODE == "password" and not smtp_pass:
        cprint("  Empty password – cancelled.", "red")
        return

    sent = fail = 0
    failed_list = []
    next_num = invoice_start

    try:
        srv = connect_smtp(smtp_pass)
        for c in valid:
            taxes    = calc_invoice_taxes(c["monthly_fee"], c["bb_charge"], c["province_key"])
            inv_num  = next_num
            inv_name = f"Invoice_{inv_num}_{c['legal_name'][:20].replace(' ','_')}.pdf"
            try:
                pdf_bytes = generate_invoice_pdf(c, inv_num, taxes)
                subj, html = build_invoice_email(c, inv_num, taxes)
                send_email(srv, c["owner_email"], subj, html,
                           attachment_bytes=pdf_bytes, attachment_name=inv_name)
                cprint(f"  OK  #{inv_num} → {c['owner_email']}  ({c['legal_name']})", "green")
                log.info(f"INVOICE SENT #{inv_num} {c['owner_email']} ({c['legal_name']})")
                next_num += 1
                sent += 1
            except Exception as e:
                failed_list.append((c["legal_name"], c["owner_email"], str(e)))
                cprint(f"  FAIL #{inv_num} {c['legal_name']}: {e}", "red")
                log.error(f"INVOICE FAIL #{inv_num} {c['legal_name']}: {e}")
                next_num += 1   # still advance counter to avoid reuse on retry
                fail += 1
        srv.quit()
    except Exception as e:
        cprint(f"\n  [SMTP ERROR] {e}", "red")
        log.error(f"SMTP error (invoices): {e}")
        return

    # Persist counter only if at least one was attempted
    _save_invoice_counter(next_num)

    # Summary
    print(f"\n  {'='*50}")
    print(f"  Invoices sent:   {sent}")
    print(f"  Failed:          {fail}")
    if failed_list:
        print(f"\n  [!] FAILED ({len(failed_list)}):")
        print(f"  {'#':<4} {'Client':<35} {'Email':<28} Error")
        print("  " + "─" * 88)
        for i, (nm, em, err) in enumerate(failed_list, 1):
            err_s = (err[:40] + "...") if len(err) > 40 else err
            print(f"  {i:<4} {nm[:34]:<35} {em[:27]:<28} {err_s}")

    if no_email:
        print(f"\n  Skipped (no email): {len(no_email)}")
        for c in no_email:
            print(f"    - {c['legal_name']}")

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    os.system("")   # Enable ANSI colours on Windows
    log.info("AR Reminder started")
    while True:
        print("\n" + "=" * 56)
        cprint("   AR Reminder System  -  Example CPA Firm P.C. Inc.", "cyan")
        print("=" * 56)
        print("  [1]  Preview  - Generate review Excel (no emails sent)")
        print("  [2]  Send     - Review then send reminder emails")
        print("  [3]  History  - View sent history")
        print("  [5]  Send Monthly Invoices")
        print("  [6]  Exit")
        print("-" * 56)
        choice = input("  Select: ").strip()

        if   choice == "1": flow_preview()
        elif choice == "2": flow_send()
        elif choice == "3": flow_history()
        elif choice == "5": flow_invoices()
        elif choice == "6": print("  Goodbye.\n"); break
        else: cprint("  Please enter 1, 2, 3, 5, or 6.", "yellow")

    log.info("AR Reminder exited")


if __name__ == "__main__":
    main()
