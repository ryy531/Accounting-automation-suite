# =============================================================================
# config.py — Tax Automation Configuration
# =============================================================================

import os

# ── Auto-detect server root from this file's location ─────────────────────────
# Layout:  {server_root}\Scripts\Tax Automation\src\config.py
# _script_dir  → …\Tax Automation\src
# _project_dir → …\Tax Automation
# _server_root → …\  (server root, 3 levels up from this file)
_script_dir  = os.path.dirname(os.path.abspath(__file__))          # …\Tax Automation\src
_project_dir = os.path.dirname(_script_dir)                         # …\Tax Automation
_server_root = os.path.dirname(os.path.dirname(_project_dir))       # …\  (server root)

# ── Server paths ──────────────────────────────────────────────────────────────
BASE_WORKFLOW_PATH = os.path.join(_server_root, "Quickbook Files", "6. 2025 Personal Tax")
SCRIPTS_DATA_PATH  = os.path.join(_project_dir, "data")
SCRIPTS_KEYS_PATH    = os.path.join(SCRIPTS_DATA_PATH, "keys")     # credentials & API keys
SCRIPTS_WORKING_PATH = os.path.join(SCRIPTS_DATA_PATH, "working")  # input/output data files
SCRIPTS_LOGS_PATH    = os.path.join(SCRIPTS_DATA_PATH, "logs")     # log files

# ── Key workflow folder names (must match exactly) ────────────────────────────
TEMPLATE_FOLDER_NAME = "1. Template"
INTAKE_FOLDER_NAME   = "2. Intake"

# Folders to scan when building the name mapping (exclude Template)
WORKFLOW_FOLDERS_TO_SCAN = [
    "2. Intake",
    "3. Preparing",
    "4. Review",
    "5. Waiting Info",
    "6. Sign & Pay",
    "7. To efile",
    "8. Efiled",
    "9. Special Cases",
]

# ── Data files ────────────────────────────────────────────────────────────────
NEW_CLIENT_FILE  = os.path.join(SCRIPTS_WORKING_PATH, "NewClient.xlsx")
TAXCYCLE_EXPORT  = os.path.join(SCRIPTS_WORKING_PATH, "TaxCycle_Export.xlsx")
MAPPING_FILE     = os.path.join(SCRIPTS_WORKING_PATH, "client_mapping.xlsx")

# ── Name mapping settings ──────────────────────────────────────────────────────
# Matches above this score are auto-confirmed (green)
AUTO_CONFIRM_THRESHOLD = 90
# Matches below this score are flagged for manual review (red)
REVIEW_THRESHOLD = 70

# ── Client subfolder structure (copied from Template) ─────────────────────────
CLIENT_SUBFOLDERS = [
    "1. Client Intake",
    "2. Taxcycle",
    "3. Signed and efile confirmation",
    "4. Admin",
]

CHILD_SUBFOLDERS = [
    "1. Client Intake",
    "2. Taxcycle",
    "3. Signed and efile confirmation",
]

# ── CRA file renaming ──────────────────────────────────────────────────────────
# Tax year of returns being processed (2025 filing season = 2024 tax year)
TAX_YEAR             = "2026"
CRA_INTAKE_SUBFOLDER = "1.Client Intake"    # Subfolder where CRA files are stored

# ── Gemini AI ─────────────────────────────────────────────────────────────────
GEMINI_KEY_FILE    = os.path.join(SCRIPTS_KEYS_PATH, "gemini_key.enc")
GEMINI_MODEL       = "gemini-3.1-pro-preview"
GEMINI_MAX_FILE_MB = 15    # Files larger than this are skipped
GEMINI_RPM         = 12    # Max requests per minute (free tier = 15, keep buffer)
GEMINI_MAX_RETRIES = 5     # Retries on quota / transient errors
GEMINI_TIMEOUT_SEC = 60    # Per-request timeout in seconds

# ── OpenAI (fallback) ──────────────────────────────────────────────────────────
OPENAI_KEY_FILE    = os.path.join(SCRIPTS_KEYS_PATH, "openai_key.enc")
OPENAI_MODEL       = "gpt-5.4"

# ── Email ─────────────────────────────────────────────────────────────────────
CREDENTIALS_FILE    = os.path.join(SCRIPTS_KEYS_PATH, "credentials.json")
EMAIL_LOG_FILE      = os.path.join(SCRIPTS_LOGS_PATH, "email_log.json")
EMAIL_COL           = "Email"

# ── Menu 5: Package email ──────────────────────────────────────────────────────
SIGN_PAY_PATH      = os.path.join(BASE_WORKFLOW_PATH, "6. Sign & Pay")
SENT_PATH          = os.path.join(BASE_WORKFLOW_PATH, "6.1 Sent")
EMAIL_TEMPLATE     = os.path.join(
    BASE_WORKFLOW_PATH, "0. Admin", "1.3 Communications",
    "2. 2025 Personal Tax Return PackageForAI.docx",
)
TAXCYCLE_SUBFOLDER = "2. Taxcycle"
T1_DUE_DATE        = "April 30, 2026"
T1_CONTACT_DATE    = "April 15, 2026"   # 15 days before due date
EMAIL_SUBJECT      = "2024 Personal Tax Return \u2013 {client_name}"

# Filename keyword → display name (order matters: specific before generic)
FILENAME_MAP = [
    ("T1135",         "T1135"),
    ("T183",          "T183"),
    ("UHT",           "UHT"),
    ("Authorization", "Authorization of Representative"),
    ("AuthRep",       "Authorization of Representative"),
    ("Invoice",       "Client Invoice"),
    ("Cletter",       "Client Letter"),
    ("Client Letter", "Client Letter"),
    ("T1",            "T1 General"),
]

# ── Menu 7: Efile confirmation ────────────────────────────────────────────────
EFILED_PATH    = os.path.join(BASE_WORKFLOW_PATH, "8. Efiled")
EFILE_TEMPLATE = os.path.join(
    BASE_WORKFLOW_PATH, "0. Admin", "1.3 Communications",
    "4 Efile_confirmation_2025_en_AI.docx",
)
EFILE_SUBJECT  = "Your 2024 Personal Tax Filing is Successfully Completed \u2013 {client_name}"

# ── Menu 6: Follow-up reminders ───────────────────────────────────────────────
WAITING_INFO_PATH     = os.path.join(BASE_WORKFLOW_PATH, "5. Waiting Info")
REMINDER_TEMPLATE     = os.path.join(
    BASE_WORKFLOW_PATH, "0. Admin", "1.3 Communications",
    "3. 2025 Follow-up Reminder PackageForAI.docx",
)
REMINDER_LOG_FILE     = os.path.join(SCRIPTS_LOGS_PATH, "reminder_log.json")
REMINDER_TRIGGER_DAYS = 3
REMINDER_MAX_SENDS    = 3
REMINDER_SUBJECT      = "2024 Personal Tax Return \u2013 {client_name} (Follow-up)"

# ── Menu 4: Prior year client reminder ───────────────────────────────────────
QUICKBOOK_FILES_PATH           = os.path.join(_server_root, "Quickbook Files")
T1_CLIENT_LIST_FILENAME        = "T1 client list.xlsx"
LOOK_BACK_YEARS                = 2
MISSING_CLIENT_LIST            = os.path.join(SCRIPTS_WORKING_PATH, f"missing_clients_{TAX_YEAR}.xlsx")
MISSING_CLIENT_LOG             = os.path.join(SCRIPTS_LOGS_PATH, "missing_clients_log.json")
MISSING_CLIENT_TEMPLATE        = os.path.join(
    BASE_WORKFLOW_PATH, "0. Admin", "1.3 Communications",
    f"5. {TAX_YEAR} Prior Year Reminder.docx",
)
MISSING_CLIENT_SUBJECT         = f"{TAX_YEAR} Personal Tax Season \u2013 {{client_name}}"
MISSING_CLIENT_FUZZY_THRESHOLD = 85
MISSING_CLIENT_MAX_SENDS       = 3
REPLY_LOG_FILE                 = os.path.join(SCRIPTS_LOGS_PATH, "reply_scan_log.json")
REPLY_SCAN_SINCE_DAYS          = 90
IMAP_INBOX_FOLDER              = "INBOX"

# ── Menu 9: TaxFolder tracker ─────────────────────────────────────────────────
TAXFOLDER_EXPORT          = os.path.join(SCRIPTS_DATA_PATH, "taxfolder_export.csv")
SIGN_PAY_TRACKER          = os.path.join(SCRIPTS_WORKING_PATH, "sign_pay_tracker.xlsx")
TAXFOLDER_MATCH_THRESHOLD = 80   # min score to link a TaxFolder client to a parsed folder person name
TO_EFILE_PATH             = os.path.join(BASE_WORKFLOW_PATH, "7. To efile")
PAID_EXPORT               = os.path.join(SCRIPTS_WORKING_PATH, "paid_export.xlsx")   # QB paid invoice export

# ── Menu 9 Option 4: Sign & Pay reminder email ──────────────────────────────────
# 税年（报的是哪一年的税），与工作年差 1。
# 例：2025 年税季报的是 2024 年税年 → 此处填 2024。每年税季初更新。
CURRENT_TAX_YEAR = 2024

SIGNPAY_REMINDER_TEMPLATE = os.path.join(
    BASE_WORKFLOW_PATH, "0. Admin", "1.3 Communications",
    # 注意：文件名用"崔"是原文件命名，非笔误。不要"修正"为"催"。
    "3.4 崔签字和付款通知 2 ENG ForAI.docx",
)
SIGNPAY_REMINDER_SUBJECT = f"Action Required \u2013 {CURRENT_TAX_YEAR} Personal Tax Return \u2013 {{client_name}}"
SIGNPAY_REMINDER_LOG = os.path.join(SCRIPTS_LOGS_PATH, "signpay_reminder_log.json")

# ── Menu 2: AI tax info extraction ──────────────────────────────────────────────
INFO_TEMPLATE_PATH = os.path.join(_project_dir, "docs", "Info Template.xlsx")

# Core fields to extract from client intake documents (high-frequency fields).
# Organized by category. Each entry is a Field Code from Info Template Export sheet.
# Future expansion: add more field codes here as needed.
CORE_FIELD_CODES = [
    # Personal identification
    "ID.SIN", "ID.BirthDate", "ID.Title", "ID.FirstName", "ID.Initial",
    "ID.LastName", "ID.Nickname", "ID.Gender", "ID.MaritalStatus",
    # Address & contact
    "Address.Street", "Address.Apartment", "Address.POBox",
    "Address.City", "Address.Province", "Address.PostalCode",
    "Address.Country", "Address.Email",
    "Address.HomePhone", "Address.MobilePhone", "Address.WorkPhone",
    # Filing
    "Filing.LanguageOfCorrespondence", "Filing.CanadianCitizen",
    "Filing.ForeignProperty", "Filing.PrincipalResidence",
    "Filing.FirstTimeFiler", "Filing.AuthorizeElectionsCanada",
    # Residency
    "Residency.ProvinceDec31", "Residency.AddressSame",
    # Spouse identification
    "SpouseID.SIN", "SpouseID.FirstName", "SpouseID.LastName",
    "SpouseID.BirthDate", "SpouseID.Gender",
    # Spouse contact
    "SpouseAddress.Email", "SpouseAddress.SameAddressAsPrinciple",
]

# File extensions accepted for tax info extraction
INTAKE_FILE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf", ".docx", ".xlsx", ".doc"}

# Filename templates for output files (saved in client Intake subfolder)
INFO_OUTPUT_TEMPLATE    = "Info Template {client}.xlsx"    # Filled template for TaxCycle import
INFO_SOURCE_REPORT      = "Info_Source_{client}_{date}.txt"  # Source tracking report
INFO_MISSING_REPORT     = "Missing_Info_{client}_{date}.txt" # Missing fields report

# AI extraction log
AI_EXTRACT_LOG = os.path.join(SCRIPTS_LOGS_PATH, "ai_extract_log.json")

# ── Client master list (Corporate 总表) ────────────────────────────────────────
CLIENT_MASTER_FILE      = os.path.join(
    _server_root,
    "4. General - Scan-Notes-Form-PPT",
    "4. Accounting client list",
    "#Business Master Client List.xlsx",
)
# Load shared settings by direct path (avoids name collision with this config.py)
import importlib.util
_settings_path = os.path.join(os.path.dirname(_project_dir), "_lib", "config", "settings.py")
_spec = importlib.util.spec_from_file_location("shared_settings", _settings_path)
_shared_settings = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_shared_settings)
CLIENT_MASTER_PASSWORD = _shared_settings.CLIENT_MASTER_PASSWORD  # from shared .env
# Columns to search for person names (searched together, best match wins)
CLIENT_MASTER_NAME_COLS = ["Owner Name", "2nd Owner Name"]
# Columns to display when a match is found (for staff reference)
CLIENT_MASTER_DISPLAY_COLS = ["Legal Name", "Owner Name", "T2 YE", "T2 Filed Until 18"]
