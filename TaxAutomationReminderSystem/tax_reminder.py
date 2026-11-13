# -*- coding: utf-8 -*-
"""
Tax Reminder System
ExampleCo Group / Example CPA Firm P.C. Inc.

Reminders:
  1. Bookkeeping  – Client Type = "Monthly"
  2. PST          – PST Frequency = "Monthly" or "Quarterly"
  3. GST          – GST Frequency = "Monthly" or "Quarterly"
"""

import os, sys, shutil, logging, tempfile, base64

# Force UTF-8 stdout so fullwidth CJK characters in menu labels don't crash on
# Windows consoles that default to cp1252.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

try:
    import msoffcrypto
except ImportError:
    print("[ERROR] Missing msoffcrypto-tool.  Run: pip install msoffcrypto-tool"); sys.exit(1)
try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.worksheet.datavalidation import DataValidation
except ImportError:
    print("[ERROR] Missing openpyxl.  Run: pip install openpyxl"); sys.exit(1)
try:
    from docx import Document
except ImportError:
    print("[ERROR] Missing python-docx.  Run: pip install python-docx"); sys.exit(1)

# ============================================================
# CONFIG
# ============================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_LIB_ROOT = os.path.join(os.path.dirname(SCRIPT_DIR), "_lib")
if _LIB_ROOT not in sys.path:
    sys.path.insert(0, _LIB_ROOT)

import config.settings as shared_settings
from connectors.email_client import EmailClient

MASTER_RELATIVE_DIR = r"4. General - Scan-Notes-Form-PPT\4. Accounting client list"
MASTER_FILENAMES = (
    "#Business Master Client List.xlsx",
    "#Business Master Client List_UPDATED.xlsx",
)
MASTER_ROOTS = (
    r"\\file-server\Shared Folder",  # Preferred: available without a mapped drive.
    "Z:\\",                              # Fallback for existing workstation mappings.
)
PATH_OPTIONS = [
    os.path.join(root, MASTER_RELATIVE_DIR, filename)
    for root in MASTER_ROOTS
    for filename in MASTER_FILENAMES
]

CONFIG = {
    "master_path":    "",          # resolved at runtime
    "output_dir":     SCRIPT_DIR,
    "tracker_file":   "TaxReminder_Tracker.xlsx",
    "smtp_user":      shared_settings.EMAIL_ADDRESS or "infosk@example-firm.com",
    "sender_name":    "ExampleCo Group",
    "sheets_to_read": ["master", "Alex's", "mk"],
    # Column header names in the master Excel file
    "col_legal_name":  "Legal Name",
    "col_owner_name":  "Owner Name",
    "col_owner_email": "Owner Email",
    "col_language":    "Language",
    "col_assigned":    "Assigned",
    "col_client_type": "Client Type",
    "col_gst_freq":    "GST Frequncy",
    "col_pst_freq":    "PST Frequency",
    "col_t2_ye":       "T2 YE",
    # Template .docx paths  (R1=1st, R2=15th, R3=25th)
    "tpl_bookkeeping_r1": os.path.join(SCRIPT_DIR, "ReminderTemplet", "Monthly Bookkeeping Reminder - 1st - ENG.docx"),
    "tpl_bookkeeping_r2": os.path.join(SCRIPT_DIR, "ReminderTemplet", "Monthly Bookkeeping Reminder - 15th - ENG.docx"),
    "tpl_bookkeeping_r3": os.path.join(SCRIPT_DIR, "ReminderTemplet", "Monthly Bookkeeping Reminder - 25th - ENG.docx"),
    "tpl_pst_r1":         os.path.join(SCRIPT_DIR, "ReminderTemplet", "Monthly PST Reminder - 1st - ENG.docx"),
    "tpl_pst_r2":         os.path.join(SCRIPT_DIR, "ReminderTemplet", "Monthly PST Reminder - 15th - ENG.docx"),
    "tpl_pst_r3":         os.path.join(SCRIPT_DIR, "ReminderTemplet", "Monthly PST Reminder - 25th - ENG.docx"),
    "tpl_pst_q":          os.path.join(SCRIPT_DIR, "ReminderTemplet", "Quarterly PST Reminder - ENG.docx"),
    "tpl_gst_r1":         os.path.join(SCRIPT_DIR, "ReminderTemplet", "Monthly GST Reminder - 1st - ENG.docx"),
    "tpl_gst_r2":         os.path.join(SCRIPT_DIR, "ReminderTemplet", "Monthly GST Reminder - 15th - ENG.docx"),
    "tpl_gst_r3":         os.path.join(SCRIPT_DIR, "ReminderTemplet", "Monthly GST Reminder - 25th - ENG.docx"),
    "tpl_t2_r1":          os.path.join(SCRIPT_DIR, "ReminderTemplet", "T2 Reminder - R1 - ENG.docx"),
    "tpl_t2_r2":          os.path.join(SCRIPT_DIR, "ReminderTemplet", "T2 Reminder - R2 - ENG.docx"),
    "tpl_t2_r3":          os.path.join(SCRIPT_DIR, "ReminderTemplet", "T2 Reminder - R3 - ENG.docx"),
}

REMINDER_TYPES = {
    "1": {
        "key":           "bookkeeping",
        "label":         "Bookkeeping Reminder",
        "filter_col":    "col_client_type",
        "filter_vals":   ["Monthly"],
        "freq_field":    "Client Type",
        "tracker_sheet": "Bookkeeping",
        "tracker_cols":  ["Legal Name", "Owner Name", "Owner Email", "Language",
                          "Assigned", "Client Type", "Status",
                          "R1SentDate", "R2SentDate", "R3SentDate", "Notes"],
        "tpl_keys":      {1: "tpl_bookkeeping_r1", 2: "tpl_bookkeeping_r2", 3: "tpl_bookkeeping_r3"},
        "subj_en":       "Bookkeeping Reminder \u2013 {month} \u2013 {co}",
        "subj_cn":       "\u6708\u5ea6\u8bb0\u8d26\u63d0\u9192 \u2013 {month} \u2013 {co}",
        "subj_vie":      "Nh\u1eafc nh\u1edf K\u1ebf to\u00e1n \u2013 {month} \u2013 {co}",
    },
    "2": {
        "key":           "pst",
        "label":         "PST Monthly Reminder",
        "filter_col":    "col_pst_freq",
        "filter_vals":   ["Monthly", "Quarterly"],  # both kept in tracker
        "send_freq":     "Monthly",                 # regular send: Monthly only
        "freq_field":    "PST Frequency",
        "tracker_sheet": "PST",
        "tracker_cols":  ["Legal Name", "Owner Name", "Owner Email", "Language",
                          "Assigned", "PST Frequency", "Status",
                          "R1SentDate", "R2SentDate", "R3SentDate", "Notes"],
        "tpl_keys":      {1: "tpl_pst_r1", 2: "tpl_pst_r2", 3: "tpl_pst_r3"},
        "subj_en":       "PST Filing Reminder \u2013 {month} \u2013 {co}",
        "subj_cn":       "PST \u7533\u62a5\u63d0\u9192 \u2013 {month} \u2013 {co}",
        "subj_vie":      "Nh\u1eafc nh\u1edf n\u1ed9p PST \u2013 {month} \u2013 {co}",
    },
    "3": {
        "key":           "gst",
        "label":         "GST/HST Reminder",
        "filter_col":    "col_gst_freq",
        "filter_vals":   ["Monthly", "Quarterly"],
        "freq_field":    "GST Frequency",
        "tracker_sheet": "GST",
        "tracker_cols":  ["Legal Name", "Owner Name", "Owner Email", "Language",
                          "Assigned", "GST Frequency", "Status",
                          "R1SentDate", "R2SentDate", "R3SentDate", "Notes"],
        "tpl_keys":      {1: "tpl_gst_r1", 2: "tpl_gst_r2", 3: "tpl_gst_r3"},
        "subj_en":       "GST/HST Filing Reminder \u2013 {month} \u2013 {co}",
        "subj_cn":       "GST/HST \u7533\u62a5\u63d0\u9192 \u2013 {month} \u2013 {co}",
        "subj_vie":      "Nh\u1eafc nh\u1edf n\u1ed9p GST/HST \u2013 {month} \u2013 {co}",
    },
}

# Status dropdown options per tracker sheet
STATUS_DROPDOWN_OPTS = {
    "Bookkeeping": '"Active,R1 Sent,R2 Sent,R3 Sent,Inactive"',
    "PST":         '"Active,R1 Sent,R2 Sent,R3 Sent,Quarterly Sent,Inactive"',
    "GST":         '"Active,R1 Sent,R2 Sent,R3 Sent,Inactive"',
    "T2":          '"Active,R1 Sent,R2 Sent,R3 Sent,Inactive"',
}

# ============================================================
# T2 MODULE  (Corporate Tax — Annual clients, 1st of each month only)
# ============================================================
T2_MODULE = {
    "label":         "T2 Corporate Tax Reminder",
    "tracker_sheet": "T2",
    "tracker_cols":  ["Legal Name", "Owner Name", "Owner Email", "Language",
                      "Assigned", "T2 YE", "Status",
                      "R1SentDate", "R2SentDate", "R3SentDate", "Notes"],
    "tpl_keys":      {1: "tpl_t2_r1", 2: "tpl_t2_r2", 3: "tpl_t2_r3"},
    "subj_en":       "T2 Filing Reminder \u2013 {month} \u2013 {co}",
    "subj_cn":       "T2\u62a5\u7a0e\u63d0\u9192 \u2013 {month} \u2013 {co}",
    "subj_vie":      "Nh\u1eafc nh\u1edf n\u1ed9p T2 \u2013 {month} \u2013 {co}",
}

# ============================================================
# QUARTERLY PST  (separate from the monthly round system)
# ============================================================
QUARTERLY_MONTHS = (1, 4, 7, 10)   # months in which quarterly PST is due

QUARTERLY_PST = {
    "label":         "PST Quarterly Reminder",
    "tracker_sheet": "PST",
    "freq_field":    "PST Frequency",
    "filter_val":    "Quarterly",
    "tpl_key":       "tpl_pst_q",   # CONFIG key for the .docx template
    "subj_en":       "PST Quarterly Filing Reminder \u2013 {month} \u2013 {co}",
    "subj_cn":       "PST \u5b63\u5ea6\u7533\u62a5\u63d0\u9192 \u2013 {month} \u2013 {co}",
    "subj_vie":      "Nh\u1eafc nh\u1edf n\u1ed9p PST H\u00e0ng Qu\u00fd \u2013 {month} \u2013 {co}",
}

# ============================================================
# ROUND DETECTION  (1st=R1, 15th=R2, 25th=R3)
# ============================================================
ROUND_DAYS = {1: 1, 2: 15, 3: 25}   # round_num → day-of-month it starts

# Language suffix used in template filenames
LANG_SUFFIX = {"CN": "CHN", "VIE": "VIE", "EN": "ENG"}

# Vietnamese month names
VIE_MONTHS = [
    "Tháng 1", "Tháng 2", "Tháng 3", "Tháng 4",
    "Tháng 5", "Tháng 6", "Tháng 7", "Tháng 8",
    "Tháng 9", "Tháng 10", "Tháng 11", "Tháng 12",
]

def normalize_lang(raw):
    """Normalize Excel language field to internal code: CN, VIE, or EN."""
    v = str(raw or "").strip().upper()
    if v in ("CN", "CHN", "CHINESE", "ZH") or v.startswith("CHINESE"):
        return "CN"
    if v in ("VIE", "VI", "VIETNAMESE", "VN") or v.startswith("VIETNAMESE") or v.startswith("VIE"):
        return "VIE"
    return "EN"

def get_template_path(rt, round_num, lang):
    """Return the .docx template path for this reminder type / round / language.
    Falls back to the ENG template if a language-specific file is not found."""
    base_path = CONFIG[rt["tpl_keys"][round_num]]
    suffix = LANG_SUFFIX.get(lang, "ENG")
    if suffix != "ENG":
        lang_path = base_path.replace("- ENG.docx", f"- {suffix}.docx")
        if os.path.exists(lang_path):
            return lang_path
    return base_path

def get_current_round():
    day = datetime.now().day
    if day < 15: return 1
    if day < 25: return 2
    return 3

def round_label(round_num):
    return {1: "Round 1 (1st)", 2: "Round 2 (15th)", 3: "Round 3 (25th)"}[round_num]

def r_sent_key(round_num):
    return f"R{round_num}SentDate"

def is_quarterly_month():
    """True if the current month is a PST quarterly filing month (1/4/7/10)."""
    return datetime.now().month in QUARTERLY_MONTHS

def _quarter_group(dt):
    """Return (year, quarter_start_month) — quarter_start_month ∈ {1,4,7,10}."""
    m = dt.month
    qs = ((m - 1) // 3) * 3 + 1   # 1→1, 2→1, 3→1, 4→4, …, 10→10, 11→10, 12→10
    return (dt.year, qs)

def already_sent_this_quarter(rec):
    """True if the quarterly PST email was already sent in the current quarter.
    Uses R1SentDate as the quarterly sent-date marker."""
    sent = rec.get("R1SentDate")
    if not sent:
        return False
    try:
        sent_dt = sent if isinstance(sent, datetime) \
                  else datetime.strptime(str(sent).strip()[:10], "%Y-%m-%d")
        return _quarter_group(sent_dt) == _quarter_group(datetime.now())
    except Exception:
        return False

def already_sent_this_round(rec, round_num):
    """True if this round's email was already sent in the current month."""
    sent = rec.get(r_sent_key(round_num))
    if not sent:
        return False
    try:
        sent_dt = sent if isinstance(sent, datetime) \
                  else datetime.strptime(str(sent).strip()[:10], "%Y-%m-%d")
        now = datetime.now()
        return (sent_dt.year, sent_dt.month) == (now.year, now.month)
    except Exception:
        return False

# ============================================================
# PERIOD HELPERS  (for {MONTH} placeholder)
# ============================================================
_EN_MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
_CN_MONTHS = [f"{i}\u6708" for i in range(1, 13)]   # "1月" … "12月"

def _month_minus(year, month, n):
    """Return (year, month) after subtracting n months."""
    m = month - n
    while m < 1:
        m += 12
        year -= 1
    return year, m

def monthly_period(lang):
    """Label for the previous calendar month (what monthly reminders ask for)."""
    now = datetime.now()
    y, m = _month_minus(now.year, now.month, 1)
    if lang == "CN":
        return f"{y}\u5e74{_CN_MONTHS[m - 1]}"   # e.g. "2026年2月"
    if lang == "VIE":
        return f"{VIE_MONTHS[m - 1]}/{y}"         # e.g. "Tháng 2/2026"
    return f"{_EN_MONTHS[m - 1]} {y}"              # e.g. "February 2026"

def quarterly_period(lang):
    """Label for the 3-month range ending last month (what quarterly reminders ask for)."""
    now = datetime.now()
    ey, em = _month_minus(now.year, now.month, 1)   # end   = last month
    sy, sm = _month_minus(now.year, now.month, 3)   # start = 3 months ago

    if lang == "CN":
        start_str = f"{sy}\u5e74{_CN_MONTHS[sm - 1]}"
        end_str   = f"{ey}\u5e74{_CN_MONTHS[em - 1]}"
        return f"{start_str} \u2013 {end_str}"
    if lang == "VIE":
        start_str = f"{VIE_MONTHS[sm - 1]}/{sy}"
        end_str   = f"{VIE_MONTHS[em - 1]}/{ey}"
        return f"{start_str} \u2013 {end_str}"
    # English: omit start year if same as end year
    if sy == ey:
        return f"{_EN_MONTHS[sm - 1]} \u2013 {_EN_MONTHS[em - 1]} {ey}"
    return f"{_EN_MONTHS[sm - 1]} {sy} \u2013 {_EN_MONTHS[em - 1]} {ey}"

# ============================================================
# T2 HELPERS
# ============================================================
_T2_YE_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,  "may": 5,  "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

def parse_t2_ye(val):
    """Return month number 1-12 from a T2 YE value like 'Jun', or None if invalid/NA."""
    if not val:
        return None
    v = str(val).strip().lower()
    if v in ("na", "n/a", "-", ""):
        return None
    return _T2_YE_MAP.get(v[:3])

def t2_round_for_client(t2_ye_month):
    """Return round number 1/2/3 based on months elapsed since T2 YE, or None if out of window."""
    if t2_ye_month is None:
        return None
    elapsed = (datetime.now().month - t2_ye_month) % 12
    return elapsed if elapsed in (1, 2, 3) else None

def t2_ye_month_label(t2_ye_month, lang):
    """Return the localized month label for a T2 YE month (used in {MONTH} placeholder)."""
    elapsed = (datetime.now().month - t2_ye_month) % 12
    y, m = _month_minus(datetime.now().year, datetime.now().month, elapsed)
    if lang == "CN":
        return f"{y}\u5e74{_CN_MONTHS[m - 1]}"
    if lang == "VIE":
        return f"{VIE_MONTHS[m - 1]}/{y}"
    return f"{_EN_MONTHS[m - 1]} {y}"

def get_t2_template_path(round_num, lang):
    """Return the T2 .docx template path for this round / language, falling back to ENG."""
    base_path = CONFIG[T2_MODULE["tpl_keys"][round_num]]
    suffix = LANG_SUFFIX.get(lang, "ENG")
    if suffix != "ENG":
        lang_path = base_path.replace("- ENG.docx", f"- {suffix}.docx")
        if os.path.exists(lang_path):
            return lang_path
    return base_path

# ============================================================
# STYLES
# ============================================================
HF  = Font(name="Arial", bold=True, size=11, color="FFFFFF")
HFL = PatternFill("solid", fgColor="2F5496")
HA  = Alignment(horizontal="center", vertical="center", wrap_text=True)
CF  = Font(name="Arial", size=10)
CA  = Alignment(vertical="center", wrap_text=True)
BD  = Border(left=Side(style="thin"), right=Side(style="thin"),
             top=Side(style="thin"),  bottom=Side(style="thin"))
COL_WIDTHS = {
    "Legal Name": 32, "Owner Name": 20, "Owner Email": 32,
    "Language": 10, "Assigned": 12,
    "Client Type": 15, "GST Frequency": 16, "PST Frequency": 16,
    "Status": 14, "R1SentDate": 16, "R2SentDate": 16, "R3SentDate": 16, "Notes": 28,
}

# ============================================================
# LOGGING
# ============================================================
def setup_logging():
    log_dir = os.path.join(CONFIG["output_dir"], "Logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"taxreminder_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ]
    )

# ============================================================
# PATH RESOLUTION
# ============================================================
def resolve_master_path():
    """Return the first existing, readable Master workbook in priority order."""
    for path in PATH_OPTIONS:
        try:
            if not os.path.isfile(path):
                continue
            with open(path, "rb"):
                return path
        except OSError as exc:
            logging.warning("Master path is not readable: %s (%s)", path, exc)
    return None

# ============================================================
# DECRYPT
# ============================================================
def decrypt_to_tempfile(filepath, password):
    import shutil
    logging.info(f"Decrypting: {filepath}")
    print("  [Decrypt] Please wait (large file)...")
    with open(filepath, "rb") as f:
        of = msoffcrypto.OfficeFile(f)
        tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
        if of.is_encrypted():
            of.load_key(password=password)
            of.decrypt(tmp)
            tmp.close()
        else:
            logging.info("File is not password-protected; copying as-is.")
            print("  [Decrypt] File is not password-protected; skipping decryption.")
            tmp.close()
            shutil.copyfile(filepath, tmp.name)
    print("  [Decrypt] Done")
    return tmp.name

# ============================================================
# READ MASTER  (per-sheet column mapping)
# ============================================================

def _norm_header(s):
    """Normalize a column header: lowercase + collapse whitespace."""
    return " ".join(str(s).strip().lower().split())

def find_columns(ws):
    """Scan row 1, return {normalized_header: col_index_1based}."""
    norm_map = {}
    for cell in next(ws.iter_rows(min_row=1, max_row=1)):
        if cell.value:
            norm_map[_norm_header(cell.value)] = cell.column
    return norm_map

# Per-sheet column overrides.
# Keys must match sheet names after strip().lower().
# Only specify columns that differ from CONFIG defaults.
# Use None to mark a column as unavailable in that sheet.
SHEET_COL_OVERRIDES = {
    "Alex's": {
        "col_legal_name":  "Company Name",
        "col_owner_name":  None,
        "col_owner_email": None,
        "col_language":    None,
        "col_assigned":    None,
        "col_client_type": None,
        "col_gst_freq":    None,
        "col_pst_freq":    "PST Frequency",
        "col_t2_ye":       "T2 Done Until",
        "default_email":   "staff.example@gmail.com",
    },
    "mk": {
        "col_legal_name":  "Company Name",
        "col_owner_name":  "Client Name",
        "col_owner_email": "Owner Email",
        "col_language":    None,
        "col_assigned":    None,
        "col_client_type": "Client Type",
        "col_gst_freq":    None,
        "col_pst_freq":    "PST Frequency",
        "col_t2_ye":       "T2 Done Until",
        "default_email":   "staff.example@gmail.com",
    },
}

REQUIRED_COLS = [
    "col_legal_name", "col_owner_name", "col_owner_email",
    "col_language", "col_assigned",
    "col_client_type", "col_gst_freq", "col_pst_freq", "col_t2_ye",
]

def read_master_data(decrypted_path):
    """Read master / Alex's / mk sheets, deduplicate by Legal Name."""
    TARGET = [s.lower() for s in CONFIG["sheets_to_read"]]
    print(f"  [Read] Loading sheets: {', '.join(s.title() for s in TARGET)}...")
    wb = openpyxl.load_workbook(decrypted_path, read_only=True, data_only=True)
    logging.info(f"Available sheets: {wb.sheetnames}")

    seen    = set()
    records = []

    for sheet_name in wb.sheetnames:
        sn_norm = sheet_name.strip().lower()
        if sn_norm not in TARGET:
            continue

        ws       = wb[sheet_name]
        norm_map = find_columns(ws)

        # Build effective column header map: CONFIG defaults + per-sheet overrides
        overrides = SHEET_COL_OVERRIDES.get(sn_norm, {})
        col_headers = {}
        for cfg_key in REQUIRED_COLS:
            if cfg_key in overrides:
                col_headers[cfg_key] = overrides[cfg_key]   # None means not available
            else:
                col_headers[cfg_key] = CONFIG[cfg_key]

        # Resolve each header → column index (None stays None)
        idx = {}
        for cfg_key, header in col_headers.items():
            if header is None:
                idx[cfg_key] = None
            else:
                idx[cfg_key] = norm_map.get(_norm_header(header))
                if idx[cfg_key] is None:
                    logging.warning(f"Sheet '{sheet_name}': column '{header}' not found")

        legal_idx = idx.get("col_legal_name")
        if legal_idx is None:
            logging.warning(f"Sheet '{sheet_name}': no Legal Name column — skipped")
            continue

        sheet_new = 0
        for row in ws.iter_rows(min_row=2, values_only=True):
            legal_val = row[legal_idx - 1] if legal_idx <= len(row) else None
            if not legal_val or str(legal_val).strip() == "":
                continue

            def _get(cfg_key, _row=row, _idx=idx):
                i = _idx.get(cfg_key)
                return _row[i - 1] if i and i <= len(_row) else None

            email_val = str(_get("col_owner_email") or "").strip()
            if not email_val:
                email_val = overrides.get("default_email", "")
            rec = {
                "Legal Name":    str(legal_val).strip(),
                "Owner Name":    str(_get("col_owner_name")  or "").strip(),
                "Owner Email":   email_val,
                "Language":      str(_get("col_language")    or "").strip(),
                "Assigned":      str(_get("col_assigned")    or "").strip(),
                "Client Type":   str(_get("col_client_type") or "").strip(),
                "GST Frequency": str(_get("col_gst_freq")    or "").strip(),
                "PST Frequency": str(_get("col_pst_freq")    or "").strip(),
                "T2 YE":         str(_get("col_t2_ye")       or "").strip(),
            }
            key = rec["Legal Name"].upper()
            if key and key not in seen:
                seen.add(key)
                records.append(rec)
                sheet_new += 1

        print(f"  [Read] Sheet '{sheet_name}': {sheet_new} new records")
        logging.info(f"Sheet '{sheet_name}': {sheet_new} records")

    wb.close()
    print(f"  [Read] Total: {len(records)} records")
    return records

# ============================================================
# FILTER
# ============================================================
def filter_clients(records, rt):
    """Return records that match this reminder type's filter, with a valid email."""
    field_name  = rt["freq_field"]          # key as stored in the record dict
    filter_vals = {v.upper() for v in rt["filter_vals"]}
    out = []
    for r in records:
        val   = str(r.get(field_name, "") or "").strip().upper()
        email = str(r.get("Owner Email", "") or "").strip()
        if val in filter_vals and email and "@" in email:
            out.append(r)
    return out

# ============================================================
# DOCX → HTML  (with Subject extraction)
# ============================================================

# XML namespaces used in docx drawing elements
_NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

# WD_COLOR_INDEX value → CSS background-color
_HL_COLORS = {
    1: "#000000", 2: "#0000FF", 3: "#00FFFF",  4: "#00FF00",
    5: "#FF00FF", 6: "#FF0000", 7: "#FFFF00",  8: "#FFFFFF",
    9: "#00008B", 10: "#008B8B", 11: "#006400", 12: "#8B008B",
    13: "#8B0000", 14: "#808000", 15: "#A9A9A9", 16: "#D3D3D3",
}

def _doc_images(doc):
    """Return {rId: (mime_type, bytes)} for every image in the document."""
    imgs = {}
    for rel in doc.part.rels.values():
        if "image" in rel.reltype:
            part = rel.target_part
            imgs[rel.rId] = (part.content_type, part.blob)
    return imgs

def _render_run(run, imgs, replacements):
    """Convert one docx Run to an HTML fragment, preserving colour/highlight/size/images."""
    # ── Inline image (drawing) — use CID reference ──────────
    blips = run._r.findall(f".//{{{_NS_A}}}blip")
    if blips:
        out = []
        for blip in blips:
            rid = blip.get(f"{{{_NS_R}}}embed")
            if rid and rid in imgs:
                out.append(
                    f'<img src="cid:{rid}" '
                    f'style="max-width:100%;vertical-align:middle;">'
                )
        if out:
            return "".join(out)

    text = run.text
    if not text:
        return ""
    if replacements:
        for k, v in replacements.items():
            text = text.replace(k, v)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    # ── Inline CSS ──────────────────────────────────────────
    css = []
    try:
        c = run.font.color.rgb
        if c:
            css.append(f"color:#{c};")
    except Exception:
        pass
    try:
        hl = run.font.highlight_color
        if hl is not None:
            bg = _HL_COLORS.get(int(hl))
            if bg:
                css.append(f"background-color:{bg};")
    except Exception:
        pass
    try:
        sz = run.font.size
        if sz:
            css.append(f"font-size:{sz / 12700:.0f}pt;")
    except Exception:
        pass

    if css:
        text = f'<span style="{"".join(css)}">{text}</span>'
    if run.bold:      text = f"<b>{text}</b>"
    if run.italic:    text = f"<i>{text}</i>"
    if run.underline: text = f"<u>{text}</u>"
    return text

def _para_style(para):
    """Return inline CSS string for paragraph-level alignment."""
    try:
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        _map = {
            WD_ALIGN_PARAGRAPH.CENTER:  "center",
            WD_ALIGN_PARAGRAPH.RIGHT:   "right",
            WD_ALIGN_PARAGRAPH.JUSTIFY: "justify",
        }
        a = _map.get(para.alignment)
        return f"text-align:{a};" if a else ""
    except Exception:
        return ""

def _render_para(para, imgs, replacements):
    """Convert one docx Paragraph to an HTML fragment."""
    inner = "".join(_render_run(r, imgs, replacements) for r in para.runs)
    if not inner.strip():
        return "<br>"
    style = _para_style(para)
    attr  = f' style="{style}"' if style else ""
    return f"<p{attr}>{inner}</p>"

def _render_table(table, imgs, replacements):
    """Convert a docx Table to a basic HTML table."""
    rows = []
    for row in table.rows:
        cells = []
        for cell in row.cells:
            inner = "".join(_render_para(p, imgs, replacements) for p in cell.paragraphs)
            cells.append(
                f'<td style="border:1px solid #ccc;padding:4px 8px;vertical-align:top;">'
                f'{inner}</td>'
            )
        rows.append(f"<tr>{''.join(cells)}</tr>")
    return (
        '<table style="border-collapse:collapse;width:100%;margin:8px 0;">'
        + "".join(rows) + "</table>"
    )

def _signature(lang):
    base = "<br><p><b>ExampleCo Group</b><br>Example CPA Firm P.C. Inc.<br>infosk@example-firm.com</p>"
    if lang == "CN":
        return "<br><p>\u8c22\u8c22\uff01" + base
    if lang == "VIE":
        return "<br><p>Tr\u00e2n tr\u1ecdng," + base
    return "<br><p>Best regards," + base

def parse_template(docx_path, replacements=None):
    """
    Parse a .docx template file into HTML, preserving:
      - Font colour, highlight colour, font size
      - Bold / italic / underline
      - Paragraph alignment
      - Inline images (embedded as base64 data URIs)
      - Tables
    If the first non-empty paragraph starts with "Subject:", that line is
    extracted as the email subject and excluded from the body.
    Returns (subject_or_None, html_body).
    """
    from docx.oxml.ns import qn
    from docx.text.paragraph import Paragraph as DocxParagraph
    from docx.table import Table as DocxTable

    doc  = Document(docx_path)
    imgs = _doc_images(doc)

    # ── Subject extraction ───────────────────────────────────
    subject      = None
    subject_elem = None
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        if text.lower().startswith("subject:"):
            subject = text[8:].strip()
            if replacements and subject:
                for k, v in replacements.items():
                    subject = subject.replace(k, v)
            subject_elem = para._element
        break

    # ── Render body in document order (paragraphs + tables) ──
    parts = ['<div style="font-family:Arial;font-size:14px;color:#333;line-height:1.6;">']
    past_subject = (subject_elem is None)

    for child in doc.element.body:
        if child is subject_elem:
            past_subject = True
            continue
        if not past_subject:
            continue

        tag = child.tag
        if tag == qn("w:p"):
            parts.append(_render_para(DocxParagraph(child, doc), imgs, replacements))
        elif tag == qn("w:tbl"):
            parts.append(_render_table(DocxTable(child, doc), imgs, replacements))

    parts.append("</div>")
    return subject, "\n".join(parts), imgs

def build_placeholder_html(rec, rt):
    """Fallback body when the template .docx has not yet been provided."""
    lang  = normalize_lang(rec.get("Language", ""))
    name  = rec.get("Owner Name", "")
    co    = rec.get("Legal Name", "")
    label = rt["label"]
    sig   = _signature(lang)
    if lang == "CN":
        return (f'<div style="font-family:Arial;font-size:14px;color:#333;">'
                f'<p>\u5c0a\u656c\u7684 {name}\uff0c</p>'
                f'<p>\u8fd9\u662f\u5173\u4e8e <b>{co}</b> \u7684 {label} \u63d0\u9192\u3002</p>'
                f'<p>\u3010\u6a21\u677f\u5360\u4f4d\u7b26 \u2013 \u5f85\u6b63\u5f0f .docx \u6a21\u677f\u6587\u4ef6\u5c31\u7eea\u540e\u81ea\u52a8\u66ff\u6362\u3011</p>'
                f'{sig}</div>')
    if lang == "VIE":
        return (f'<div style="font-family:Arial;font-size:14px;color:#333;">'
                f'<p>K\u00ednh g\u1eedi {name},</p>'
                f'<p>\u0110\u00e2y l\u00e0 th\u00f4ng b\u00e1o nh\u1eafc nh\u1edf {label} cho <b>{co}</b>.</p>'
                f'<p>[M\u1eabu t\u1ea1m th\u1eddi \u2013 s\u1ebd \u0111\u01b0\u1ee3c thay th\u1ebf khi c\u00f3 t\u1ec7p .docx ch\u00ednh th\u1ee9c]</p>'
                f'{sig}</div>')
    return (f'<div style="font-family:Arial;font-size:14px;color:#333;">'
            f'<p>Dear {name},</p>'
            f'<p>This is a {label} for <b>{co}</b>.</p>'
            f'<p>[Template placeholder &ndash; will be replaced once the .docx template is provided]</p>'
            f'{sig}</div>')

def build_quarterly_email_content(rec):
    """Return (subject, html_body) for one quarterly PST record."""
    lang = normalize_lang(rec.get("Language", ""))
    co   = rec.get("Legal Name", "")
    name = rec.get("Owner Name", "")

    # Quarterly reminders always show the 3-month range
    month = quarterly_period(lang)

    def _default_subj():
        if lang == "CN":  return QUARTERLY_PST["subj_cn"].format(month=month, co=co)
        if lang == "VIE": return QUARTERLY_PST["subj_vie"].format(month=month, co=co)
        return QUARTERLY_PST["subj_en"].format(month=month, co=co)

    # Template: try language variant first, fall back to ENG
    base_path = CONFIG[QUARTERLY_PST["tpl_key"]]
    suffix = LANG_SUFFIX.get(lang, "ENG")
    tpl_path = base_path
    if suffix != "ENG":
        lang_path = base_path.replace("- ENG.docx", f"- {suffix}.docx")
        if os.path.exists(lang_path):
            tpl_path = lang_path

    if os.path.exists(tpl_path):
        replacements = {"{NAME}": name, "{COMPANY}": co, "{MONTH}": month}
        tpl_subj, body, images = parse_template(tpl_path, replacements)
        logging.info(f"Quarterly template loaded: {os.path.basename(tpl_path)}")
        subj = tpl_subj if tpl_subj else _default_subj()
    else:
        logging.warning(f"Quarterly template not found: {tpl_path} — using placeholder")
        images = {}
        sig  = _signature(lang)
        if lang == "CN":
            body = (f'<div style="font-family:Arial;font-size:14px;color:#333;">'
                    f'<p>\u5c0a\u656c\u7684 {name}\uff0c</p>'
                    f'<p>\u8fd9\u662f\u5173\u4e8e <b>{co}</b> \u7684 PST \u5b63\u5ea6\u7533\u62a5\u63d0\u9192\u3002</p>'
                    f'{sig}</div>')
        elif lang == "VIE":
            body = (f'<div style="font-family:Arial;font-size:14px;color:#333;">'
                    f'<p>K\u00ednh g\u1eedi {name},</p>'
                    f'<p>\u0110\u00e2y l\u00e0 nh\u1eafc nh\u1edf n\u1ed9p PST h\u00e0ng qu\u00fd cho <b>{co}</b>.</p>'
                    f'{sig}</div>')
        else:
            body = (f'<div style="font-family:Arial;font-size:14px;color:#333;">'
                    f'<p>Dear {name},</p>'
                    f'<p>This is a PST Quarterly Filing Reminder for <b>{co}</b>.</p>'
                    f'{sig}</div>')
        subj = _default_subj()

    return subj, body, images

def build_t2_email_content(rec, round_num):
    """Return (subject, html_body, images) for one T2 record."""
    lang    = normalize_lang(rec.get("Language", ""))
    co      = rec.get("Legal Name", "")
    name    = rec.get("Owner Name", "")
    ye_mon  = parse_t2_ye(rec.get("T2 YE", ""))
    month   = t2_ye_month_label(ye_mon, lang) if ye_mon else "N/A"

    def _default_subj():
        if lang == "CN":  return T2_MODULE["subj_cn"].format(month=month, co=co)
        if lang == "VIE": return T2_MODULE["subj_vie"].format(month=month, co=co)
        return T2_MODULE["subj_en"].format(month=month, co=co)

    tpl_path = get_t2_template_path(round_num, lang)
    if os.path.exists(tpl_path):
        replacements = {"{NAME}": name, "{COMPANY}": co, "{MONTH}": month}
        tpl_subj, body, images = parse_template(tpl_path, replacements)
        logging.info(f"T2 template loaded: {os.path.basename(tpl_path)}")
        subj = tpl_subj if tpl_subj else _default_subj()
    else:
        logging.warning(f"T2 template not found: {tpl_path} — using placeholder")
        images = {}
        sig = _signature(lang)
        urgency = {1: ("Please file", "\u8bf7\u5c3d\u5feb\u63d0\u4ea4", "Vui l\u00f2ng n\u1ed9p"),
                   2: ("Reminder: still pending", "\u8ddf\u8fdb\u63d0\u9192\uff1a\u5c1a\u672a\u63d0\u4ea4", "Nh\u1eafc nh\u1edf: v\u1eabn c\u00f2n ch\u1edd"),
                   3: ("URGENT: overdue", "\u7d27\u6025\uff1a\u5df2\u9010\u671f", "KH\u1ea8N C\u1ea4P: qu\u00e1 h\u1ea1n")}
        urg = urgency.get(round_num, urgency[1])
        if lang == "CN":
            body = (f'<div style="font-family:Arial;font-size:14px;color:#333;">'
                    f'<p>\u5c0a\u656c\u7684 {name}\uff0c</p>'
                    f'<p>[{urg[1]}] \u8bf7\u5c3d\u5feb\u63d0\u4ea4 <b>{co}</b> \u7684 T2 \u7533\u62a5\u6587\u4ef6\uff08{month}\uff09\u3002</p>'
                    f'<p>[\u6a21\u677f\u5360\u4f4d\u7b26 \u2013 \u5f85 T2 Reminder - R{round_num} - CHN.docx \u6a21\u677f\u6dfb\u52a0\u540e\u81ea\u52a8\u66ff\u6362]</p>'
                    f'{sig}</div>')
        elif lang == "VIE":
            body = (f'<div style="font-family:Arial;font-size:14px;color:#333;">'
                    f'<p>K\u00ednh g\u1eedi {name},</p>'
                    f'<p>[{urg[2]}] Vui l\u00f2ng n\u1ed9p h\u1ed3 s\u01a1 T2 c\u1ee7a <b>{co}</b> ({month}).</p>'
                    f'<p>[M\u1eabu t\u1ea1m th\u1eddi \u2013 ch\u1edd t\u1ec7p T2 Reminder - R{round_num} - VIE.docx]</p>'
                    f'{sig}</div>')
        else:
            body = (f'<div style="font-family:Arial;font-size:14px;color:#333;">'
                    f'<p>Dear {name},</p>'
                    f'<p>[{urg[0]}] Please submit your T2 filing documents for <b>{co}</b> ({month}).</p>'
                    f'<p>[Template placeholder &ndash; waiting for T2 Reminder - R{round_num} - ENG.docx]</p>'
                    f'{sig}</div>')
        subj = _default_subj()

    return subj, body, images

def build_email_content(rec, rt, round_num=None):
    """Return (subject, html_body) for one record."""
    if round_num is None:
        round_num = get_current_round()
    lang = normalize_lang(rec.get("Language", ""))
    co   = rec.get("Legal Name", "")
    name = rec.get("Owner Name", "")

    # {MONTH}: quarterly clients get a 3-month range, monthly clients get prev month
    freq_val = str(rec.get(rt.get("freq_field", ""), "") or "").strip().upper()
    if freq_val == "QUARTERLY":
        month = quarterly_period(lang)
    else:
        month = monthly_period(lang)

    def _default_subj():
        if lang == "CN":  return rt["subj_cn"].format(month=month, co=co)
        if lang == "VIE": return rt["subj_vie"].format(month=month, co=co)
        return rt["subj_en"].format(month=month, co=co)

    tpl_path = get_template_path(rt, round_num, lang)

    if os.path.exists(tpl_path):
        replacements = {"{NAME}": name, "{COMPANY}": co, "{MONTH}": month}
        tpl_subj, body, images = parse_template(tpl_path, replacements)
        logging.info(f"Template loaded: {os.path.basename(tpl_path)}")
        subj = tpl_subj if tpl_subj else _default_subj()
    else:
        body   = build_placeholder_html(rec, rt)
        images = {}
        logging.warning(f"Template not found: {tpl_path} — using placeholder")
        subj = _default_subj()

    return subj, body, images

# ============================================================
# TRACKER  I/O
# ============================================================
def load_existing_tracker(filepath):
    """
    Returns {sheet_name: {legal_name_upper: record_dict}}
    """
    result = {}
    if not os.path.exists(filepath):
        return result
    wb     = openpyxl.load_workbook(filepath)
    valid  = {rt["tracker_sheet"] for rt in REMINDER_TYPES.values()} | {T2_MODULE["tracker_sheet"]}
    for sheet_name in wb.sheetnames:
        if sheet_name not in valid:
            continue
        ws      = wb[sheet_name]
        headers = [ws.cell(row=1, column=c).value for c in range(1, ws.max_column + 1)]
        recs    = {}
        for ri in range(2, ws.max_row + 1):
            rd = {}
            for ci, h in enumerate(headers, 1):
                if h:
                    rd[h] = ws.cell(row=ri, column=ci).value
            key = str(rd.get("Legal Name", "") or "").strip().upper()
            if key:
                recs[key] = rd
        result[sheet_name] = recs
        logging.info(f"Tracker '{sheet_name}': {len(recs)} records loaded")
    return result

def write_sheet(ws, cols, recs):
    for ci, cn in enumerate(cols, 1):
        c = ws.cell(row=1, column=ci, value=cn)
        c.font = HF; c.fill = HFL; c.alignment = HA; c.border = BD
        ws.column_dimensions[openpyxl.utils.get_column_letter(ci)].width = COL_WIDTHS.get(cn, 15)
    ws.auto_filter.ref = f"A1:{openpyxl.utils.get_column_letter(len(cols))}1"
    ws.freeze_panes   = "A2"
    for ri, rec in enumerate(recs, 2):
        for ci, cn in enumerate(cols, 1):
            c = ws.cell(row=ri, column=ci, value=rec.get(cn) or "")
            c.font = CF; c.alignment = CA; c.border = BD

def save_tracker(tracker_working, output_path):
    """
    tracker_working: {sheet_name: {key: record_dict}}
    Creates a backup before overwriting.
    """
    if os.path.exists(output_path):
        bd = os.path.join(os.path.dirname(output_path), "Backup")
        os.makedirs(bd, exist_ok=True)
        shutil.copy2(output_path,
                     os.path.join(bd, f"TaxReminder_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"))

    wb = openpyxl.Workbook()
    wb.remove(wb.active)   # remove default empty sheet

    seen_sheets = set()
    for rt in REMINDER_TYPES.values():
        sn   = rt["tracker_sheet"]
        if sn in seen_sheets:
            continue
        seen_sheets.add(sn)
        cols = rt["tracker_cols"]
        ws   = wb.create_sheet(sn)
        recs = list(tracker_working.get(sn, {}).values())
        write_sheet(ws, cols, recs)
        # Re-add Status dropdown so it survives each save
        opts = STATUS_DROPDOWN_OPTS.get(sn)
        if opts and "Status" in cols:
            status_col = openpyxl.utils.get_column_letter(cols.index("Status") + 1)
            dv = DataValidation(
                type="list", formula1=opts, allow_blank=True,
                showDropDown=False, showErrorMessage=True,
                errorTitle="Invalid value",
                error="Please select a value from the dropdown list.",
            )
            dv.sqref = f"{status_col}2:{status_col}1048576"
            ws.add_data_validation(dv)
        logging.info(f"Saved '{sn}': {len(recs)} records")

    # T2 sheet (not in REMINDER_TYPES)
    sn   = T2_MODULE["tracker_sheet"]
    cols = T2_MODULE["tracker_cols"]
    ws   = wb.create_sheet(sn)
    recs = list(tracker_working.get(sn, {}).values())
    write_sheet(ws, cols, recs)
    opts = STATUS_DROPDOWN_OPTS.get(sn)
    if opts and "Status" in cols:
        status_col = openpyxl.utils.get_column_letter(cols.index("Status") + 1)
        dv = DataValidation(
            type="list", formula1=opts, allow_blank=True,
            showDropDown=False, showErrorMessage=True,
            errorTitle="Invalid value",
            error="Please select a value from the dropdown list.",
        )
        dv.sqref = f"{status_col}2:{status_col}1048576"
        ws.add_data_validation(dv)
    logging.info(f"Saved '{sn}': {len(recs)} records")

    while True:
        try:
            wb.save(output_path)
            logging.info(f"Tracker saved: {output_path}")
            break
        except PermissionError:
            print(f"\n  [ERROR] Cannot save tracker — the file is open in another program.")
            print(f"  Please close  {output_path}")
            input("  Then press Enter to retry...")


# ============================================================
# SMTP
# ============================================================
def connect_smtp(password=None):
    client = EmailClient.from_settings(CONFIG["sender_name"], password=password)
    client.connect_imap()
    logging.info("Email client connected (%s)", shared_settings.EMAIL_AUTH_MODE)
    return client


def request_email_password(existing=None):
    """Prompt only while the legacy password transport is active."""
    if shared_settings.EMAIL_AUTH_MODE == "gmail_api":
        print("  Using Gmail API OAuth authorization.")
        return None
    if existing:
        return existing
    print("  (Password will not be shown as you type)")
    return input(f"  SMTP Password ({CONFIG['smtp_user']}): ").strip()

def send_email(conn, to, subj, html, images=None):
    """
    Send an HTML email, optionally with inline images (CID attachments).
    images: {rId: (mime_type, bytes)}  — produced by _doc_images / parse_template.
    Structure: multipart/mixed > multipart/related > text/html + image parts.
    """
    from email.mime.image import MIMEImage

    # Inner: related (HTML + inline images)
    related = MIMEMultipart("related")
    related.attach(MIMEText(html, "html", "utf-8"))
    if images:
        for cid, (mime_type, blob) in images.items():
            subtype  = mime_type.split("/")[-1]   # e.g. "png", "jpeg"
            img_part = MIMEImage(blob, _subtype=subtype)
            img_part.add_header("Content-ID", f"<{cid}>")
            img_part.add_header("Content-Disposition", "inline")
            related.attach(img_part)

    # Outer: standard envelope
    msg            = MIMEMultipart()
    msg["From"]    = f"{CONFIG['sender_name']} <{CONFIG['smtp_user']}>"
    msg["To"]      = to
    msg["Subject"] = subj
    msg.attach(related)

    raw = msg.as_bytes()
    conn.send_raw(raw, to)
    logging.info(f"Sent → {to} | {subj}")

# ============================================================
# STEP 1 — MERGE  (one type, no email)
# ============================================================
def merge_for_type(rt_key, all_records, tracker_working, inactive_keys: frozenset = frozenset()):
    """
    Merge master data into tracker for one reminder type.
    Updates tracker_working[sheet_name] in place.
    Returns (merged_dict, candidates_for_current_round).
    inactive_keys: set of upper-cased Legal Names known to be inactive in NocoDB;
                   these are removed from the tracker rather than preserved.
    """
    rt         = REMINDER_TYPES[rt_key]
    sheet_name = rt["tracker_sheet"]
    cols       = rt["tracker_cols"]
    round_num  = get_current_round()

    filtered = filter_clients(all_records, rt)
    existing = tracker_working.get(sheet_name, {})
    merged   = {}

    for r in filtered:
        key = r["Legal Name"].upper()
        if key in existing:
            merged_rec = dict(existing[key])
            for f in ["Owner Name", "Owner Email", "Language", "Assigned",
                      "Client Type", "GST Frequency", "PST Frequency"]:
                if f in r:
                    merged_rec[f] = r[f]
            merged[key] = merged_rec
        else:
            new_rec = {c: r.get(c, "") for c in cols}
            new_rec["Status"]     = "Active"
            new_rec["R1SentDate"] = ""
            new_rec["R2SentDate"] = ""
            new_rec["R3SentDate"] = ""
            new_rec["Notes"]      = ""
            merged[key] = new_rec

    # Keep clients removed from master but still in tracker,
    # except those now marked inactive in NocoDB.
    for k, v in existing.items():
        if k not in merged and k not in inactive_keys:
            merged[k] = v

    tracker_working[sheet_name] = merged

    # Regular (monthly-round) candidates — respect send_freq restriction if set
    send_freq = rt.get("send_freq")
    candidates = [
        v for v in merged.values()
        if str(v.get("Status", "") or "").strip().lower() != "inactive"
        and not already_sent_this_round(v, round_num)
        and (send_freq is None
             or str(v.get(rt["freq_field"], "") or "").strip().upper() == send_freq.upper())
    ]
    return merged, candidates


def merge_t2(all_records, tracker_working, inactive_keys: frozenset = frozenset()):
    """
    Merge T2-eligible clients (Client Type=Annual, valid T2 YE) into T2 tracker sheet.
    Updates tracker_working["T2"] in place.
    Returns merged dict.
    inactive_keys: set of upper-cased Legal Names to remove from tracker.
    """
    sn   = T2_MODULE["tracker_sheet"]
    cols = T2_MODULE["tracker_cols"]

    filtered = [
        r for r in all_records
        if str(r.get("Client Type", "") or "").strip().upper() == "ANNUAL"
        and parse_t2_ye(r.get("T2 YE", "")) is not None
        and str(r.get("Owner Email", "") or "").strip()
        and "@" in str(r.get("Owner Email", "") or "")
    ]

    existing = tracker_working.get(sn, {})
    merged   = {}

    for r in filtered:
        key = r["Legal Name"].upper()
        if key in existing:
            merged_rec = dict(existing[key])
            for f in ["Owner Name", "Owner Email", "Language", "Assigned", "T2 YE"]:
                if f in r:
                    merged_rec[f] = r[f]
            merged[key] = merged_rec
        else:
            new_rec = {c: r.get(c, "") for c in cols}
            new_rec["Status"]     = "Active"
            new_rec["R1SentDate"] = ""
            new_rec["R2SentDate"] = ""
            new_rec["R3SentDate"] = ""
            new_rec["Notes"]      = ""
            merged[key] = new_rec

    for k, v in existing.items():
        if k not in merged and k not in inactive_keys:
            merged[k] = v

    tracker_working[sn] = merged
    return merged


def t2_candidates(tracker_working):
    """Return list of (rec, round_num) for all T2 clients due this month."""
    merged = tracker_working.get(T2_MODULE["tracker_sheet"], {})
    out = []
    for rec in merged.values():
        if str(rec.get("Status", "") or "").strip().lower() == "inactive":
            continue
        ye_mon    = parse_t2_ye(rec.get("T2 YE", ""))
        round_num = t2_round_for_client(ye_mon)
        if round_num is None:
            continue
        if already_sent_this_round(rec, round_num):
            continue
        out.append((rec, round_num))
    return out


def send_t2_flow(tracker_working, smtp_pass=None):
    """Send T2 reminders for all eligible clients this month. Returns number sent."""
    label = T2_MODULE["label"]
    now   = datetime.now()

    if not tracker_working.get(T2_MODULE["tracker_sheet"]):
        print(f"\n  [{label}] Tracker is empty. Run option [1] first.")
        return 0

    candidates = t2_candidates(tracker_working)

    print(f"\n  {label}  |  {now.strftime('%B %Y')}  (1st of month)")
    print(f"  Clients due this month: {len(candidates)}")

    if not candidates:
        print("  Nothing to send.")
        return 0

    print(f"\n  {'No.':<5} {'Legal Name':<35} {'Email':<32} {'T2 YE':<8} {'Round'}")
    print(f"  {'-' * 90}")
    for i, (r, rn) in enumerate(candidates, 1):
        print(f"  {i:<5} {r.get('Legal Name', ''):<35} "
              f"{r.get('Owner Email', ''):<32} {r.get('T2 YE', ''):<8} R{rn}")

    ch = input(f"\n  Send {len(candidates)} T2 email(s)? (Y/N): ").strip().upper()
    if ch != "Y":
        print("  Skipped.")
        return 0

    smtp_pass = request_email_password(smtp_pass)

    sent = fail = 0
    try:
        srv = connect_smtp(smtp_pass)
        for r, rn in candidates:
            try:
                subj, body, images = build_t2_email_content(r, rn)
                send_email(srv, r["Owner Email"].strip(), subj, body, images)
                r[r_sent_key(rn)] = now.strftime("%Y-%m-%d")
                r["Status"] = f"R{rn} Sent"
                sent += 1
            except Exception as e:
                logging.error(f"T2 send fail [{r.get('Legal Name')}]: {e}")
                print(f"    [FAIL] {r.get('Legal Name')}: {e}")
                fail += 1
        srv.quit()
        print(f"\n  Sent: {sent}   Failed: {fail}")
    except Exception as e:
        logging.error(f"SMTP error (T2): {e}", exc_info=True)
        print(f"\n  [SMTP ERROR] {e}")

    return sent


def update_tracker_flow(all_records, tracker_working, inactive_keys: frozenset = frozenset()):
    """
    Step 1: Merge all 3 types from master, print pending list for current round.
    PST is shown in two sections: Monthly (round-based) and Quarterly (quarter-based).
    inactive_keys: upper-cased Legal Names to remove from tracker (passed through to merges).
    """
    now       = datetime.now()
    round_num = get_current_round()
    print(f"\n  Updating tracker — {now.strftime('%B %Y')}  |  {round_label(round_num)}\n")

    for rt_key, rt in REMINDER_TYPES.items():
        merged, candidates = merge_for_type(rt_key, all_records, tracker_working, inactive_keys)
        label      = rt["label"]
        freq_field = rt["freq_field"]

        print(f"  [{rt_key}] {label}")
        print(f"      Total tracked: {len(merged)}   |   "
              f"Pending ({round_label(round_num)}): {len(candidates)}")
        logging.info(f"Merged '{rt['tracker_sheet']}': {len(merged)} total, "
                     f"{len(candidates)} pending for {round_label(round_num)}")

        if candidates:
            print(f"\n  {'No.':<5} {'Legal Name':<35} {'Email':<32} {freq_field}")
            print(f"  {'-' * 88}")
            for i, r in enumerate(candidates, 1):
                print(f"  {i:<5} {r.get('Legal Name', ''):<35} "
                      f"{r.get('Owner Email', ''):<32} {r.get(freq_field, '')}")

        # ── PST Quarterly section ──────────────────────────────
        if rt_key == "2":
            q_candidates = [
                v for v in merged.values()
                if str(v.get("Status", "") or "").strip().lower() != "inactive"
                and str(v.get(freq_field, "") or "").strip().upper() == "QUARTERLY"
                and not already_sent_this_quarter(v)
            ]
            q_label = "PST Quarterly（04,07,10,01）"
            q_flag  = " ★ 季度月 — 本月可发送" if is_quarterly_month() else " （非季度月，本月不发送）"
            print(f"\n      {q_label}{q_flag}")
            print(f"      Quarterly tracked: "
                  f"{sum(1 for v in merged.values() if str(v.get(freq_field,'')).strip().upper()=='QUARTERLY')}"
                  f"   |   Pending this quarter: {len(q_candidates)}")
            if q_candidates:
                print(f"\n  {'No.':<5} {'Legal Name':<35} {'Email':<32} {freq_field}")
                print(f"  {'-' * 88}")
                for i, r in enumerate(q_candidates, 1):
                    print(f"  {i:<5} {r.get('Legal Name', ''):<35} "
                          f"{r.get('Owner Email', ''):<32} {r.get(freq_field, '')}")

        print()

    # ── T2 section ────────────────────────────────────────────
    merged_t2 = merge_t2(all_records, tracker_working, inactive_keys)
    due        = t2_candidates(tracker_working)
    print(f"  [T2] {T2_MODULE['label']}")
    print(f"      Total tracked: {len(merged_t2)}   |   Due this month: {len(due)}")
    if due:
        print(f"\n  {'No.':<5} {'Legal Name':<35} {'Email':<32} {'T2 YE':<8} {'Round'}")
        print(f"  {'-' * 90}")
        for i, (r, rn) in enumerate(due, 1):
            print(f"  {i:<5} {r.get('Legal Name', ''):<35} "
                  f"{r.get('Owner Email', ''):<32} {r.get('T2 YE', ''):<8} R{rn}")
    print()


# ============================================================
# STEP 2 — SEND  (one type, uses existing tracker)
# ============================================================
def send_flow(rt_key, tracker_working, smtp_pass=None):
    """
    Step 2: Display pending list for the current round, confirm, send.
    smtp_pass: if provided, skips the password prompt (used by Send All).
    Returns number of emails sent.
    """
    rt         = REMINDER_TYPES[rt_key]
    sheet_name = rt["tracker_sheet"]
    freq_field = rt["freq_field"]
    label      = rt["label"]
    round_num  = get_current_round()

    merged = tracker_working.get(sheet_name, {})
    if not merged:
        print(f"  [{label}] Tracker is empty. Run option [1] first.")
        return 0

    # If this reminder type restricts sends to a specific frequency, apply it.
    send_freq = rt.get("send_freq")
    candidates = [
        v for v in merged.values()
        if str(v.get("Status", "") or "").strip().lower() != "inactive"
        and not already_sent_this_round(v, round_num)
        and (send_freq is None
             or str(v.get(freq_field, "") or "").strip().upper() == send_freq.upper())
    ]

    now = datetime.now()
    print(f"\n  {label}  |  {round_label(round_num)}  |  {now.strftime('%B %Y')}")
    print(f"  Pending this round: {len(candidates)}")

    if not candidates:
        print(f"  Nothing to send.")
        return 0

    print(f"\n  {'No.':<5} {'Legal Name':<35} {'Email':<32} {freq_field}")
    print(f"  {'-' * 88}")
    for i, r in enumerate(candidates, 1):
        print(f"  {i:<5} {r.get('Legal Name', ''):<35} "
              f"{r.get('Owner Email', ''):<32} {r.get(freq_field, '')}")

    ch = input(f"\n  Send {len(candidates)} email(s)? (Y/N): ").strip().upper()
    if ch != "Y":
        print("  Skipped.")
        return 0

    smtp_pass = request_email_password(smtp_pass)

    sent = fail = 0
    sent_key = r_sent_key(round_num)
    try:
        srv = connect_smtp(smtp_pass)
        for r in candidates:
            try:
                subj, body, images = build_email_content(r, rt, round_num)
                send_email(srv, r["Owner Email"].strip(), subj, body, images)
                r[sent_key] = datetime.now().strftime("%Y-%m-%d")
                r["Status"] = f"R{round_num} Sent"
                sent += 1
            except Exception as e:
                logging.error(f"Send fail [{r.get('Legal Name')}]: {e}")
                print(f"    [FAIL] {r.get('Legal Name')}: {e}")
                fail += 1
        srv.quit()
        print(f"\n  Sent: {sent}   Failed: {fail}")
    except Exception as e:
        logging.error(f"SMTP error: {e}", exc_info=True)
        print(f"\n  [SMTP ERROR] {e}")

    return sent

# ============================================================
# QUARTERLY PST SEND FLOW
# ============================================================
def send_quarterly_pst_flow(tracker_working, smtp_pass=None):
    """
    Send quarterly PST reminders to all Quarterly clients not yet sent this quarter.
    Only meaningful in months 1, 4, 7, 10.
    Returns number of emails sent.
    """
    sheet_name = QUARTERLY_PST["tracker_sheet"]
    freq_field = QUARTERLY_PST["freq_field"]
    label      = QUARTERLY_PST["label"]
    now        = datetime.now()

    if not is_quarterly_month():
        print(f"\n  [{label}]")
        print(f"  当前为 {now.month} 月，PST 季度提醒仅在 1 / 4 / 7 / 10 月发送。")
        print(f"  Current month ({now.month}) is not a PST quarterly filing month (1/4/7/10).")
        return 0

    merged = tracker_working.get(sheet_name, {})
    if not merged:
        print(f"\n  [{label}] Tracker is empty. Run option [1] first.")
        return 0

    candidates = [
        v for v in merged.values()
        if str(v.get("Status", "") or "").strip().lower() != "inactive"
        and str(v.get(freq_field, "") or "").strip().upper() == "QUARTERLY"
        and not already_sent_this_quarter(v)
    ]

    print(f"\n  {label}  |  {now.strftime('%B %Y')}  (季度月 Quarterly Month)")
    print(f"  待发送 Pending: {len(candidates)}")

    if not candidates:
        print("  Nothing to send.")
        return 0

    print(f"\n  {'No.':<5} {'Legal Name':<35} {'Email':<32} {freq_field}")
    print(f"  {'-' * 88}")
    for i, r in enumerate(candidates, 1):
        print(f"  {i:<5} {r.get('Legal Name', ''):<35} "
              f"{r.get('Owner Email', ''):<32} {r.get(freq_field, '')}")

    ch = input(f"\n  Send {len(candidates)} quarterly email(s)? (Y/N): ").strip().upper()
    if ch != "Y":
        print("  Skipped.")
        return 0

    smtp_pass = request_email_password(smtp_pass)

    sent = fail = 0
    try:
        srv = connect_smtp(smtp_pass)
        for r in candidates:
            try:
                subj, body, images = build_quarterly_email_content(r)
                send_email(srv, r["Owner Email"].strip(), subj, body, images)
                r["R1SentDate"] = now.strftime("%Y-%m-%d")   # quarterly sent marker
                r["Status"]     = "Quarterly Sent"
                sent += 1
            except Exception as e:
                logging.error(f"Quarterly send fail [{r.get('Legal Name')}]: {e}")
                print(f"    [FAIL] {r.get('Legal Name')}: {e}")
                fail += 1
        srv.quit()
        print(f"\n  Sent: {sent}   Failed: {fail}")
    except Exception as e:
        logging.error(f"SMTP error (quarterly): {e}", exc_info=True)
        print(f"\n  [SMTP ERROR] {e}")

    return sent

# ============================================================
# DELETE CLIENT
# ============================================================
def delete_client_flow(tracker_working):
    """
    Search all tracker sheets by name and remove matching entries.
    Does NOT touch the master Excel file.
    Returns True if any record was deleted (tracker needs saving).
    """
    print("\n  === Delete Client from Tracker ===")
    print("  Note: this removes the client from the TRACKER only.")
    print("        The master Excel file is NOT modified.")

    query = input("\n  Enter client name (or partial name) to search: ").strip()
    if not query:
        print("  Cancelled.")
        return False

    q_upper = query.strip().upper()
    matches = []   # list of (sheet_name, key, record)

    all_sheets = [rt["tracker_sheet"] for rt in REMINDER_TYPES.values()] + [T2_MODULE["tracker_sheet"]]
    for sn in dict.fromkeys(all_sheets):   # deduplicated, order preserved
        sheet_data = tracker_working.get(sn, {})
        for key, rec in sheet_data.items():
            if q_upper in key:
                matches.append((sn, key, rec))

    if not matches:
        print(f"\n  No client found matching: '{query}'")
        return False

    print(f"\n  Found {len(matches)} match(es):\n")
    for i, (sn, key, rec) in enumerate(matches, 1):
        print(f"    [{i}] {rec.get('Legal Name',''):<40}  "
              f"Sheet: {sn:<14}  Email: {rec.get('Owner Email','')}")

    sel = input(
        "\n  Enter number to delete, 'A' to delete all matches, or Enter to cancel: "
    ).strip().upper()

    if not sel:
        print("  Cancelled.")
        return False

    if sel == "A":
        to_delete = matches
    elif sel.isdigit() and 1 <= int(sel) <= len(matches):
        to_delete = [matches[int(sel) - 1]]
    else:
        print("  Invalid selection. Cancelled.")
        return False

    print("\n  About to delete:")
    for sn, key, rec in to_delete:
        print(f"    - {rec.get('Legal Name','')}  (Sheet: {sn})")

    confirm = input("\n  Confirm deletion? (Y/N): ").strip().upper()
    if confirm != "Y":
        print("  Cancelled.")
        return False

    for sn, key, rec in to_delete:
        if sn in tracker_working and key in tracker_working[sn]:
            del tracker_working[sn][key]
            logging.info(f"Deleted from tracker [{sn}]: {rec.get('Legal Name','')}")

    print(f"\n  {len(to_delete)} record(s) deleted.")
    return True

# ============================================================
# HELPERS
# ============================================================
def load_master(master_path):
    """Read encrypted or unencrypted master file. Returns records list or None."""
    tmp = None
    try:
        with open(master_path, "rb") as f:
            is_encrypted = msoffcrypto.OfficeFile(f).is_encrypted()

        if not is_encrypted:
            logging.info("Master file is not encrypted; reading directly")
            print("\n  [Read] Master file is not encrypted; opening directly.")
            return read_master_data(master_path)

        print("\n  (Password will not be shown as you type)")
        password = input("  Enter Master File Password: ").strip()
        if not password:
            print("  [ERROR] Empty password.")
            return None

        tmp = decrypt_to_tempfile(master_path, password)
        return read_master_data(tmp)
    except Exception as e:
        logging.error(f"Failed to read master: {e}", exc_info=True)
        print(f"\n  [ERROR] {e}")
        return None
    finally:
        if tmp:
            try: os.unlink(tmp)
            except: pass


def load_clients(master_path):
    """NocoDB-primary client loader with Master Excel fallback.

    Returns (records, inactive_keys) where inactive_keys is a frozenset of
    upper-cased Legal Names whose client status is "inactive" in NocoDB.
    When falling back to Master Excel, inactive_keys is always empty.
    """
    try:
        import nocodb_source
        records, inactive_keys = nocodb_source.load_from_nocodb()
        suffix = f", {len(inactive_keys)} inactive excluded" if inactive_keys else ""
        print(f"  [Read] Source: NocoDB  ({len(records)} client records{suffix}).")
        logging.info("Loaded %d records from NocoDB (%d inactive excluded)",
                     len(records), len(inactive_keys))
        return records, inactive_keys
    except Exception as e:
        logging.warning(
            "NocoDB read failed (%s); falling back to Master Excel",
            e, exc_info=True,
        )
        print(f"\n  [WARN] NocoDB unavailable ({e.__class__.__name__}); "
              f"falling back to Master Excel.")
        return load_master(master_path), frozenset()


# ============================================================
# MAIN
# ============================================================
def main():
    print("=" * 60)
    print("  Tax Reminder System")
    print("  ExampleCo Group / Example CPA Firm P.C. Inc.")
    print("=" * 60 + "\n")

    print("  Detecting master file path...")
    master_path = resolve_master_path()
    if not master_path:
        print("\n[ERROR] Master file not found on any known path:")
        for p in PATH_OPTIONS:
            print(f"    - {p}")
        print("\n  Please check the network drive connection.")
        input("\nPress Enter to exit..."); sys.exit(1)

    CONFIG["master_path"] = master_path
    print(f"  [OK] {master_path}\n")

    os.makedirs(CONFIG["output_dir"], exist_ok=True)
    setup_logging()

    tracker_path    = os.path.join(CONFIG["output_dir"], CONFIG["tracker_file"])
    tracker_working = load_existing_tracker(tracker_path)
    all_records     = None   # loaded once per session when [1] is run

    while True:
        now       = datetime.now()
        round_num = get_current_round()
        print()
        print("=" * 60)
        print(f"  Tax Reminder System   [{now.strftime('%Y-%m-%d')}]")
        print(f"  Current: {now.strftime('%B %Y')}  |  {round_label(round_num)}")
        print("=" * 60)
        q_flag = " ★ 季度月" if is_quarterly_month() else ""
        print("  [1] Update Tracker        (read NocoDB -> update all 3 lists)")
        print("  ----------------------------------------")
        print(f"  [2] Send Bookkeeping      ({round_label(round_num)})")
        print(f"  [3] Send PST Monthly      ({round_label(round_num)})")
        print(f"  [4] Send GST/HST          ({round_label(round_num)})")
        print(f"  [5] Send All Three        ({round_label(round_num)})")
        print("  ----------------------------------------")
        print(f"  [6] PST Quarterly（04,07,10,01）{q_flag}")
        print("  ----------------------------------------")
        print("  [7] Delete Client from Tracker")
        print("  [8] T2 Corporate Tax Reminder  (1st of month)")
        print("  [0] Exit")
        print("=" * 60)
        choice = input("  Your choice (0-8): ").strip()

        if choice == "0":
            print("  Goodbye!")
            break

        # ── [1] Update tracker ─────────────────────────────────
        elif choice == "1":
            records, inactive_keys = load_clients(master_path)
            if records is None:
                input("\n  Press Enter to continue...")
                continue
            all_records = records
            update_tracker_flow(all_records, tracker_working, inactive_keys)
            save_tracker(tracker_working, tracker_path)
            print(f"  Tracker saved: {tracker_path}")
            input("\n  Press Enter to continue...")

        # ── [2-5] Send emails ──────────────────────────────────
        elif choice in ("2", "3", "4", "5"):
            # Reload from disk so any manual Status edits made in the Excel file
            # after option [1] ran are picked up before the send list is built.
            tracker_working = load_existing_tracker(tracker_path)
            send_map = {"2": ["1"], "3": ["2"], "4": ["3"], "5": ["1", "2", "3"]}
            keys = send_map[choice]

            if not any(tracker_working.get(REMINDER_TYPES[k]["tracker_sheet"]) for k in keys):
                print("\n  Tracker is empty. Please run option [1] first.")
                input("\n  Press Enter to continue...")
                continue

            if len(keys) > 1:
                # Send All: obtain password once, or use Gmail OAuth.
                smtp_pass = request_email_password()
                total_sent = 0
                for k in keys:
                    print(f"\n  {'=' * 50}")
                    total_sent += send_flow(k, tracker_working, smtp_pass=smtp_pass)
                print(f"\n  Total sent across all types: {total_sent}")

                # ── Quarterly PST prompt (only in 1/4/7/10 months) ────────
                if is_quarterly_month():
                    now = datetime.now()
                    print(f"\n  {'=' * 50}")
                    print(f"  ★ 当前为 {now.month} 月（PST 季度月）")
                    print(f"  ★ It's a PST quarterly filing month ({now.month}).")
                    q_ch = input("  是否同时发送 PST 季度提醒邮件？Send PST Quarterly reminders? (Y/N): ").strip().upper()
                    if q_ch == "Y":
                        print(f"\n  {'=' * 50}")
                        send_quarterly_pst_flow(tracker_working, smtp_pass=smtp_pass)
            else:
                send_flow(keys[0], tracker_working)

            save_tracker(tracker_working, tracker_path)
            print(f"\n  Tracker saved: {tracker_path}")
            input("\n  Press Enter to continue...")

        # ── [6] PST Quarterly ─────────────────────────────────
        elif choice == "6":
            tracker_working = load_existing_tracker(tracker_path)
            if not tracker_working.get(QUARTERLY_PST["tracker_sheet"]):
                print("\n  Tracker is empty. Please run option [1] first.")
                input("\n  Press Enter to continue...")
                continue
            send_quarterly_pst_flow(tracker_working)
            save_tracker(tracker_working, tracker_path)
            print(f"\n  Tracker saved: {tracker_path}")
            input("\n  Press Enter to continue...")

        # ── [7] Delete client ──────────────────────────────────
        elif choice == "7":
            changed = delete_client_flow(tracker_working)
            if changed:
                save_tracker(tracker_working, tracker_path)
                print(f"  Tracker saved: {tracker_path}")
            input("\n  Press Enter to continue...")

        # ── [8] T2 ─────────────────────────────────────────────
        elif choice == "8":
            tracker_working = load_existing_tracker(tracker_path)
            if not tracker_working.get(T2_MODULE["tracker_sheet"]):
                print("\n  Tracker is empty. Please run option [1] first.")
                input("\n  Press Enter to continue...")
                continue
            send_t2_flow(tracker_working)
            save_tracker(tracker_working, tracker_path)
            print(f"\n  Tracker saved: {tracker_path}")
            input("\n  Press Enter to continue...")

        else:
            print("  Invalid choice. Please enter 0-8.")

    input("\nPress Enter to exit...")


if __name__ == "__main__":
    main()
