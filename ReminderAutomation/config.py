# ReminderAutomation — central constants (no credentials here)

# ── OpenClaw gateway ──────────────────────────────────────────────────────────
OPENCLAW_CONTAINER  = "openclaw-openclaw-gateway-1"
WHATSAPP_GROUP_JID  = "000000000000000000@g.us"  # placeholder — real ID redacted

# ── Tracker paths (UNC first, Z: mapped drive as fallback) ───────────────────
ANNUAL_RETURN_TRACKER_OPTIONS = [
    r"\\file-server\Shared Folder\Scripts\AnnualReturn\AnnualReturn_Tracker.xlsx",
    r"Z:\Scripts\AnnualReturn\AnnualReturn_Tracker.xlsx",
]

TAX_REMINDER_TRACKER_OPTIONS = [
    r"\\file-server\Shared Folder\Scripts\TaxAutomationReminderSystem\TaxReminder_Tracker.xlsx",
    r"Z:\Scripts\TaxAutomationReminderSystem\TaxReminder_Tracker.xlsx",
]

# ── Annual Return follow-up window ───────────────────────────────────────────
# How many months back to look for overdue unfiled clients (1 = last month only)
AR_OVERDUE_MONTHS_BACK = 1

# ── Tax Reminder sheets to scan ───────────────────────────────────────────────
TAX_REMINDER_SHEETS = ["Bookkeeping", "PST", "GST", "T2"]
