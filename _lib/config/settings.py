"""
Shared settings for all ExampleCo scripts.
Reads credentials from the .env file in the same directory.
"""

import os
import warnings

try:
    from dotenv import load_dotenv
except ImportError:
    raise SystemExit(
        "[config] python-dotenv is not installed.\n"
        "  Run:  pip install python-dotenv"
    )

# ---------------------------------------------------------------------------
# Load .env from the same directory as this file
# ---------------------------------------------------------------------------
_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

if not os.path.exists(_ENV_PATH):
    raise FileNotFoundError(
        f"[config] .env file not found: {_ENV_PATH}\n"
        "  Create it from the template and fill in the values."
    )

load_dotenv(_ENV_PATH)

# ---------------------------------------------------------------------------
# API Keys
# ---------------------------------------------------------------------------
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "").strip()
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "").strip()
DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "").strip()
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "").strip()
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.1-pro-preview")
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-5.4")
DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")

if not GEMINI_API_KEY:
    warnings.warn(
        "[config] GEMINI_API_KEY is not set in .env. "
        "AI-powered features will not work.",
        stacklevel=2,
    )

if not OPENAI_API_KEY:
    warnings.warn(
        "[config] OPENAI_API_KEY is not set in .env. "
        "OpenAI-powered features will not work.",
        stacklevel=2,
    )

if not ANTHROPIC_API_KEY:
    warnings.warn(
        "[config] ANTHROPIC_API_KEY is not set in .env. "
        "Anthropic-powered features will not work.",
        stacklevel=2,
    )

# ---------------------------------------------------------------------------
# Email / SMTP / IMAP
# ---------------------------------------------------------------------------
EMAIL_AUTH_MODE: str = os.getenv("EMAIL_AUTH_MODE", "password").strip().lower()
EMAIL_ADDRESS: str = os.getenv("EMAIL_ADDRESS", "").strip()
EMAIL_PASSWORD: str = os.getenv("EMAIL_PASSWORD", "").strip()
EMAIL_SMTP_SERVER: str = os.getenv("EMAIL_SMTP_SERVER", "").strip()
EMAIL_IMAP_SERVER: str = os.getenv("EMAIL_IMAP_SERVER", "").strip()
_GMAIL_SECRET_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "_secrets",
    "gmail",
)
GMAIL_OAUTH_CLIENT_SECRET_PATH: str = os.getenv(
    "GMAIL_OAUTH_CLIENT_SECRET_PATH",
    os.path.join(_GMAIL_SECRET_DIR, "google_oauth_client_secret.json"),
).strip()
GMAIL_OAUTH_REFRESH_TOKEN_PATH: str = os.getenv(
    "GMAIL_OAUTH_REFRESH_TOKEN_PATH",
    os.path.join(_GMAIL_SECRET_DIR, "gmail_refresh_token.json"),
).strip()
EMAIL_FALLBACK_ADDRESS: str = os.getenv("EMAIL_FALLBACK_ADDRESS", "").strip()
PAYROLL_REPORT_EMAIL_TO: str = os.getenv("PAYROLL_REPORT_EMAIL_TO", "").strip()
DAILY_REPORT_ADDITIONAL_EMAIL_TO: str = os.getenv(
    "DAILY_REPORT_ADDITIONAL_EMAIL_TO", "infosk@example-firm.com"
).strip()
DAILY_REPORT_ARCHIVE_FOLDER: str = os.getenv(
    "DAILY_REPORT_ARCHIVE_FOLDER", "ExampleCo Daily Reports"
).strip()

try:
    EMAIL_SMTP_PORT: int = int(os.getenv("EMAIL_SMTP_PORT", "0"))
except ValueError:
    EMAIL_SMTP_PORT = 0
    warnings.warn("[config] EMAIL_SMTP_PORT is not a valid integer.", stacklevel=2)

try:
    EMAIL_IMAP_PORT: int = int(os.getenv("EMAIL_IMAP_PORT", "0"))
except ValueError:
    EMAIL_IMAP_PORT = 0
    warnings.warn("[config] EMAIL_IMAP_PORT is not a valid integer.", stacklevel=2)

if not EMAIL_ADDRESS:
    warnings.warn(
        "[config] EMAIL_ADDRESS is not set in .env. "
        "Email sending will not work.",
        stacklevel=2,
    )

if EMAIL_AUTH_MODE not in ("password", "gmail_api"):
    warnings.warn(
        "[config] EMAIL_AUTH_MODE must be 'password' or 'gmail_api'.",
        stacklevel=2,
    )

# ---------------------------------------------------------------------------
# Master file password
# ---------------------------------------------------------------------------
CLIENT_MASTER_PASSWORD: str = os.getenv("CLIENT_MASTER_PASSWORD", "").strip()

if not CLIENT_MASTER_PASSWORD:
    warnings.warn(
        "[config] CLIENT_MASTER_PASSWORD is not set in .env. "
        "Encrypted Excel files cannot be opened.",
        stacklevel=2,
    )

# ---------------------------------------------------------------------------
# Google Document AI
# ---------------------------------------------------------------------------
GCP_PROJECT_ID: str = os.getenv("GCP_PROJECT_ID", "").strip()
GCP_DOCAI_REGION: str = os.getenv("GCP_DOCAI_REGION", "us").strip()
GCP_DOCAI_EXPENSE_PROCESSOR_ID: str = os.getenv("GCP_DOCAI_EXPENSE_PROCESSOR_ID", "").strip()
GCP_DOCAI_BANKSTATEMENT_PROCESSOR_ID: str = os.getenv("GCP_DOCAI_BANKSTATEMENT_PROCESSOR_ID", "").strip()

# ---------------------------------------------------------------------------
# NocoDB
# ---------------------------------------------------------------------------
NOCODB_URL: str = os.getenv("NOCODB_URL", "").strip().rstrip("/")
NOCODB_TOKEN: str = os.getenv("NOCODB_TOKEN", "").strip()
NOCODB_COMPANIES_TABLE_ID: str = os.getenv("NOCODB_COMPANIES_TABLE_ID", "").strip()
NOCODB_ENGAGEMENTS_TABLE_ID: str = os.getenv("NOCODB_ENGAGEMENTS_TABLE_ID", "").strip()
NOCODB_EVENTS_TABLE_ID: str = os.getenv("NOCODB_EVENTS_TABLE_ID", "").strip()
NOCODB_PEOPLE_TABLE_ID: str = os.getenv("NOCODB_PEOPLE_TABLE_ID", "").strip()

# NocoDB column types (uidt) that the generic bot field-update tool must never
# write to, regardless of table: system-managed or computed by NocoDB itself.
NOCODB_PROTECTED_FIELD_TYPES = frozenset({
    "ID", "CreatedTime", "LastModifiedTime", "CreatedBy", "LastModifiedBy",
    "Order", "Deleted", "Formula", "Rollup", "Lookup", "LinkToAnotherRecord",
})

if not NOCODB_URL or not NOCODB_TOKEN:
    warnings.warn(
        "[config] NOCODB_URL or NOCODB_TOKEN is not set in .env. "
        "NocoDB integration will not work.",
        stacklevel=2,
    )

if not NOCODB_COMPANIES_TABLE_ID:
    warnings.warn(
        "[config] NOCODB_COMPANIES_TABLE_ID is not set in .env. "
        "Cannot access Companies table.",
        stacklevel=2,
    )

if not NOCODB_ENGAGEMENTS_TABLE_ID:
    warnings.warn(
        "[config] NOCODB_ENGAGEMENTS_TABLE_ID is not set in .env. "
        "Cannot access Engagements table.",
        stacklevel=2,
    )

if not NOCODB_EVENTS_TABLE_ID:
    warnings.warn(
        "[config] NOCODB_EVENTS_TABLE_ID is not set in .env. "
        "Cannot access Events table.",
        stacklevel=2,
    )

if not NOCODB_PEOPLE_TABLE_ID:
    warnings.warn(
        "[config] NOCODB_PEOPLE_TABLE_ID is not set in .env. "
        "Cannot access People table.",
        stacklevel=2,
    )

# Companies.Owners -> People LinksToRecord field. NocoDB column ID (not a
# field title -- the /links/ endpoint addresses link fields by column ID,
# unlike the generic records endpoint). Empty until Step 1b creates the
# field in the NocoDB product (people-company topology bridging).
NOCODB_COMPANY_OWNERS_LINK_FIELD_ID: str = os.getenv(
    "NOCODB_COMPANY_OWNERS_LINK_FIELD_ID", ""
).strip()

# â”€â”€â”€ NocoDB Webhook â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
NOCODB_WEBHOOK_SECRET: str = os.getenv("NOCODB_WEBHOOK_SECRET", "").strip()

# â”€â”€â”€ Payroll Cron Job field-name constants â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# (Based on Step 0 prerequisites verification â€” spell exactly as-is)

# Shared payroll frequency constants
FREQ_MONTHLY      = "Monthly"
FREQ_SEMI_MONTHLY = "Semi-monthly"
FREQ_BI_WEEKLY    = "Bi-weekly"
FREQ_QUARTERLY    = "Quarterly"
FREQ_YEARLY       = "Yearly"
VALID_FREQUENCIES = {FREQ_MONTHLY, FREQ_SEMI_MONTHLY, FREQ_BI_WEEKLY,
                     FREQ_QUARTERLY, FREQ_YEARLY}

# Timezone
PAYROLL_TIMEZONE = "America/Regina"   # Saskatchewan â€” no DST

# â”€â”€â”€ NocoDB Field Name Constants â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# (Based on nocodb_current_state_report_2026-06-03.md)
# Companies table
NOCODB_COMPANY_FIELD_LEGAL_NAME      = "Legal Name"
NOCODB_COMPANY_FIELD_IS_PAYROLL      = "Is Payroll Client"
NOCODB_COMPANY_FIELD_PAY_FREQUENCY   = "Pay Frequency"
NOCODB_COMPANY_FIELD_PAY_PERIOD_RULE = "Pay Period End Rule"
NOCODB_COMPANY_FIELD_PAYROLL_SCHEDULE_2_ACTIVE = "Payroll Schedule 2 Active"
NOCODB_COMPANY_FIELD_PAYROLL_SCHEDULE_2_FREQUENCY = "Payroll Schedule 2 Frequency"
NOCODB_COMPANY_FIELD_PAYROLL_SCHEDULE_2_PERIOD_END_RULE = "Payroll Schedule 2 Period End Rule"
NOCODB_COMPANY_FIELD_GST_FREQUENCY = "GST Frequency"
NOCODB_COMPANY_FIELD_GST_FILED_UNTIL = "GST Filed Until"
NOCODB_COMPANY_FIELD_GST_NEXT_PERIOD_END = "GST Next Period End"
NOCODB_COMPANY_FIELD_GST_DUE_NEXT_PERIOD = "GST Due Next Period"
NOCODB_COMPANY_FIELD_GST_URGENCY = "GST Urgency"
NOCODB_COMPANY_FIELD_PST_FREQUENCY = "PST Frequency"
NOCODB_COMPANY_FIELD_PST_FILED_UNTIL = "PST Filed Until"
NOCODB_COMPANY_FIELD_PST_NEXT_PERIOD_END = "PST Next Period End"
NOCODB_COMPANY_FIELD_PST_DUE_NEXT_PERIOD = "PST Due Next Period"
NOCODB_COMPANY_FIELD_PST_URGENCY = "PST Urgency"
NOCODB_COMPANY_FIELD_T2_FILED_UNTIL = "T2 Filed Until"
NOCODB_COMPANY_FIELD_T2_DUE_NEXT_PERIOD = "T2 Due Next Period"
NOCODB_COMPANY_FIELD_T2_URGENCY = "T2 Urgency"

# Engagements table (field names from NocoDB actual schema)
NOCODB_ENGAGEMENT_FIELD_NAME         = "Name"
NOCODB_ENGAGEMENT_FIELD_TYPE         = "Type"
NOCODB_ENGAGEMENT_FIELD_STATUS       = "Status"
NOCODB_ENGAGEMENT_FIELD_COMPANY      = "Company"
NOCODB_ENGAGEMENT_FIELD_PERIOD_END   = "Payroll Period End"      # One space (NocoDB actual)
NOCODB_ENGAGEMENT_FIELD_PERIOD_START = "Payroll Period Start"    # One space (NocoDB actual)
NOCODB_ENGAGEMENT_FIELD_ASSIGNED_TO  = "Assigned To"
NOCODB_ENGAGEMENT_FIELD_FREQUENCY    = "Frequency"
NOCODB_ENGAGEMENT_FIELD_TAX_PERIOD_START = "Period Start"
NOCODB_ENGAGEMENT_FIELD_TAX_PERIOD_END = "Period End"
NOCODB_ENGAGEMENT_FIELD_DUE_DATE     = "Due Date"
NOCODB_ENGAGEMENT_FIELD_T2_YEAR_END  = "T2 Year End"
NOCODB_ENGAGEMENT_FIELD_PRIORITY     = "Priority"
NOCODB_ENGAGEMENT_FIELD_SOURCE       = "Source"
NOCODB_ENGAGEMENT_FIELD_NOTES        = "Notes"
NOCODB_ENGAGEMENT_FIELD_YEAR         = "Year"
NOCODB_ENGAGEMENT_FIELD_GST_STATUS   = "GST Status"
NOCODB_ENGAGEMENT_FIELD_PST_STATUS   = "PST Status"
NOCODB_ENGAGEMENT_FIELD_T2_STATUS    = "T2 Status"
NOCODB_ENGAGEMENT_FIELD_BOOKKEEPING_STATUS = "Bookkeeping Status"
NOCODB_ENGAGEMENT_FIELD_ANNUAL_RETURN_STATUS = "Annual Return Status"

NOCODB_GST_WORKBENCH_VIEW_ID = os.getenv(
    "NOCODB_GST_WORKBENCH_VIEW_ID", "vwxk6m2qwlmg79nb"
)
NOCODB_PST_WORKBENCH_VIEW_ID = os.getenv(
    "NOCODB_PST_WORKBENCH_VIEW_ID", "vwocqk0re3zu3mcy"
)
NOCODB_T2_WORKBENCH_VIEW_ID = os.getenv(
    "NOCODB_T2_WORKBENCH_VIEW_ID", "vwawahbe6y4se9rc"
)
NOCODB_BOOKKEEPING_WORKBENCH_VIEW_ID = os.getenv(
    "NOCODB_BOOKKEEPING_WORKBENCH_VIEW_ID", "vwknh9y2qmqg5v7p"
)
NOCODB_ANNUAL_RETURN_WORKBENCH_VIEW_ID = os.getenv(
    "NOCODB_ANNUAL_RETURN_WORKBENCH_VIEW_ID", "vw4a1xxn1t6czn7b"
)

# Pay Frequency option values (from NocoDB select options)
NOCODB_FREQ_MONTHLY      = "Monthly"
NOCODB_FREQ_SEMI_MONTHLY = "Semi-monthly"
NOCODB_FREQ_BI_WEEKLY    = "Bi-weekly"
NOCODB_FREQ_QUARTERLY    = "Quarterly"
NOCODB_FREQ_YEARLY       = "Yearly"
NOCODB_VALID_FREQUENCIES = {NOCODB_FREQ_MONTHLY, NOCODB_FREQ_SEMI_MONTHLY,
                            NOCODB_FREQ_BI_WEEKLY, NOCODB_FREQ_QUARTERLY,
                            NOCODB_FREQ_YEARLY}

# Engagement Type and Status write values (from NocoDB select options)
NOCODB_ENGAGEMENT_TYPE_PAYROLL        = "Payroll"
NOCODB_ENGAGEMENT_STATUS_WAITING_INFO = "Payroll | Waiting Info"  # Note: pipe | character
NOCODB_ENGAGEMENT_STATUS_PROCESSING   = "Payroll | Processing"
NOCODB_ENGAGEMENT_STATUS_COMPLETED    = "Payroll | Completed"
NOCODB_ENGAGEMENT_STATUS_ARCHIVED     = "Payroll | Archived"

# Events table (optional, for future use)
NOCODB_EVENT_FIELD_TIMESTAMP     = "Timestamp"
NOCODB_EVENT_FIELD_ENGAGEMENT    = "Engagement"
NOCODB_EVENT_FIELD_EVENT_TYPE    = "Event Type"
NOCODB_EVENT_FIELD_DESCRIPTION   = "Description"
NOCODB_EVENT_FIELD_ACTOR         = "Actor"


