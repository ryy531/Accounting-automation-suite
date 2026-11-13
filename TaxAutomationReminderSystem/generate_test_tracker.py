# -*- coding: utf-8 -*-
"""
generate_test_tracker.py
────────────────────────
1. Clears TaxReminder_Tracker.xlsx and writes test records
   covering ENG / CHN / VIE languages and all three reminder types.
2. Sends the file as an e-mail attachment to a specified address
   via the same Hostinger SMTP used by tax_reminder.py.
"""

import os, sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
except ImportError:
    print("[ERROR] Missing openpyxl.  Run: pip install openpyxl")
    sys.exit(1)

# ──────────────────────────────────────────────
# PATHS / CONFIG
# ──────────────────────────────────────────────
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
_LIB_ROOT    = os.path.join(os.path.dirname(SCRIPT_DIR), "_lib")
if _LIB_ROOT not in sys.path:
    sys.path.insert(0, _LIB_ROOT)

import config.settings as shared_settings
from connectors.email_client import EmailClient

TRACKER_PATH = os.path.join(SCRIPT_DIR, "TaxReminder_Tracker.xlsx")

SMTP_USER    = shared_settings.EMAIL_ADDRESS or "infosk@example-firm.com"
SENDER_NAME  = "ExampleCo Group"

RECIPIENT    = "staff2.example@gmail.com"

# ──────────────────────────────────────────────
# STYLES (matching tax_reminder.py)
# ──────────────────────────────────────────────
HF  = Font(name="Arial", bold=True, size=11, color="FFFFFF")
HFL = PatternFill("solid", fgColor="2F5496")
HA  = Alignment(horizontal="center", vertical="center", wrap_text=True)
CF  = Font(name="Arial", size=10)
CA  = Alignment(vertical="center", wrap_text=True)
BD  = Border(
    left=Side(style="thin"), right=Side(style="thin"),
    top=Side(style="thin"),  bottom=Side(style="thin"),
)
COL_WIDTHS = {
    "Legal Name": 32, "Owner Name": 20, "Owner Email": 32,
    "Language": 10, "Assigned": 12,
    "Client Type": 15, "GST Frequency": 16, "PST Frequency": 16,
    "Status": 14, "R1SentDate": 16, "R2SentDate": 16, "R3SentDate": 16, "Notes": 28,
}

# ──────────────────────────────────────────────
# TEST DATA
# Each dict key matches the column header for that sheet.
# All owner emails point to the test recipient.
# ──────────────────────────────────────────────
TEST_EMAIL = RECIPIENT

BOOKKEEPING_RECORDS = [
    {
        "Legal Name":  "ENG Test Company Ltd.",
        "Owner Name":  "John Smith",
        "Owner Email": TEST_EMAIL,
        "Language":    "ENG",
        "Assigned":    "Alex",
        "Client Type": "Monthly",
        "Status":      "Active",
        "R1SentDate":  "",
        "R2SentDate":  "",
        "R3SentDate":  "",
        "Notes":       "English test — bookkeeping",
    },
    {
        "Legal Name":  "中文测试企业有限公司",
        "Owner Name":  "张伟",
        "Owner Email": TEST_EMAIL,
        "Language":    "CHN",
        "Assigned":    "Alex",
        "Client Type": "Monthly",
        "Status":      "Active",
        "R1SentDate":  "",
        "R2SentDate":  "",
        "R3SentDate":  "",
        "Notes":       "Chinese test — bookkeeping",
    },
    {
        "Legal Name":  "Công ty Kiểm tra Tiếng Việt",
        "Owner Name":  "Nguyễn Văn An",
        "Owner Email": TEST_EMAIL,
        "Language":    "VIE",
        "Assigned":    "Alex",
        "Client Type": "Monthly",
        "Status":      "Active",
        "R1SentDate":  "",
        "R2SentDate":  "",
        "R3SentDate":  "",
        "Notes":       "Vietnamese test — bookkeeping",
    },
    {
        "Legal Name":  "R2 Already Sent Corp.",
        "Owner Name":  "Alice Brown",
        "Owner Email": TEST_EMAIL,
        "Language":    "ENG",
        "Assigned":    "MK",
        "Client Type": "Monthly",
        "Status":      "R2 Sent",
        "R1SentDate":  datetime.now().strftime("%Y-%m-01"),
        "R2SentDate":  datetime.now().strftime("%Y-%m-15"),
        "R3SentDate":  "",
        "Notes":       "R1 & R2 already sent this month",
    },
]

PST_RECORDS = [
    {
        "Legal Name":  "ENG Monthly PST Inc.",
        "Owner Name":  "Bob Johnson",
        "Owner Email": TEST_EMAIL,
        "Language":    "ENG",
        "Assigned":    "Alex",
        "PST Frequency": "Monthly",
        "Status":      "Active",
        "R1SentDate":  "",
        "R2SentDate":  "",
        "R3SentDate":  "",
        "Notes":       "English test — monthly PST",
    },
    {
        "Legal Name":  "Quarterly PST Holdings",
        "Owner Name":  "Carol White",
        "Owner Email": TEST_EMAIL,
        "Language":    "ENG",
        "Assigned":    "MK",
        "PST Frequency": "Quarterly",
        "Status":      "Active",
        "R1SentDate":  "",
        "R2SentDate":  "",
        "R3SentDate":  "",
        "Notes":       "English test — quarterly PST",
    },
    {
        "Legal Name":  "越南语PST测试公司",
        "Owner Name":  "Trần Thị Bình",
        "Owner Email": TEST_EMAIL,
        "Language":    "VIE",
        "Assigned":    "Alex",
        "PST Frequency": "Monthly",
        "Status":      "Active",
        "R1SentDate":  "",
        "R2SentDate":  "",
        "R3SentDate":  "",
        "Notes":       "Vietnamese test — monthly PST",
    },
    {
        "Legal Name":  "中文PST季报公司",
        "Owner Name":  "李梅",
        "Owner Email": TEST_EMAIL,
        "Language":    "CHN",
        "Assigned":    "Alex",
        "PST Frequency": "Quarterly",
        "Status":      "Active",
        "R1SentDate":  "",
        "R2SentDate":  "",
        "R3SentDate":  "",
        "Notes":       "Chinese test — quarterly PST",
    },
]

GST_RECORDS = [
    {
        "Legal Name":  "ENG Monthly GST Corp.",
        "Owner Name":  "David Lee",
        "Owner Email": TEST_EMAIL,
        "Language":    "ENG",
        "Assigned":    "Alex",
        "GST Frequency": "Monthly",
        "Status":      "Active",
        "R1SentDate":  "",
        "R2SentDate":  "",
        "R3SentDate":  "",
        "Notes":       "English test — monthly GST",
    },
    {
        "Legal Name":  "中文GST月报公司",
        "Owner Name":  "王芳",
        "Owner Email": TEST_EMAIL,
        "Language":    "CHN",
        "Assigned":    "MK",
        "GST Frequency": "Monthly",
        "Status":      "Active",
        "R1SentDate":  "",
        "R2SentDate":  "",
        "R3SentDate":  "",
        "Notes":       "Chinese test — monthly GST",
    },
    {
        "Legal Name":  "Công ty GST Hàng Quý",
        "Owner Name":  "Lê Thị Cúc",
        "Owner Email": TEST_EMAIL,
        "Language":    "VIE",
        "Assigned":    "Alex",
        "GST Frequency": "Quarterly",
        "Status":      "Active",
        "R1SentDate":  "",
        "R2SentDate":  "",
        "R3SentDate":  "",
        "Notes":       "Vietnamese test — quarterly GST",
    },
    {
        "Legal Name":  "Inactive Test Ltd.",
        "Owner Name":  "Eve Martin",
        "Owner Email": TEST_EMAIL,
        "Language":    "ENG",
        "Assigned":    "Alex",
        "GST Frequency": "Monthly",
        "Status":      "Inactive",
        "R1SentDate":  "",
        "R2SentDate":  "",
        "R3SentDate":  "",
        "Notes":       "Inactive — should be skipped by send flow",
    },
]

SHEETS = {
    "Bookkeeping": {
        "cols":    ["Legal Name", "Owner Name", "Owner Email", "Language",
                    "Assigned", "Client Type", "Status",
                    "R1SentDate", "R2SentDate", "R3SentDate", "Notes"],
        "records": BOOKKEEPING_RECORDS,
    },
    "PST": {
        "cols":    ["Legal Name", "Owner Name", "Owner Email", "Language",
                    "Assigned", "PST Frequency", "Status",
                    "R1SentDate", "R2SentDate", "R3SentDate", "Notes"],
        "records": PST_RECORDS,
    },
    "GST": {
        "cols":    ["Legal Name", "Owner Name", "Owner Email", "Language",
                    "Assigned", "GST Frequency", "Status",
                    "R1SentDate", "R2SentDate", "R3SentDate", "Notes"],
        "records": GST_RECORDS,
    },
}

# ──────────────────────────────────────────────
# BUILD EXCEL
# ──────────────────────────────────────────────
def build_tracker():
    wb = openpyxl.Workbook()
    wb.remove(wb.active)

    for sheet_name, cfg in SHEETS.items():
        ws   = wb.create_sheet(sheet_name)
        cols = cfg["cols"]

        # Header row
        for ci, cn in enumerate(cols, 1):
            cell = ws.cell(row=1, column=ci, value=cn)
            cell.font      = HF
            cell.fill      = HFL
            cell.alignment = HA
            cell.border    = BD
            ws.column_dimensions[
                openpyxl.utils.get_column_letter(ci)
            ].width = COL_WIDTHS.get(cn, 15)

        ws.auto_filter.ref = f"A1:{openpyxl.utils.get_column_letter(len(cols))}1"
        ws.freeze_panes    = "A2"

        # Data rows
        for ri, rec in enumerate(cfg["records"], 2):
            for ci, cn in enumerate(cols, 1):
                cell = ws.cell(row=ri, column=ci, value=rec.get(cn, ""))
                cell.font      = CF
                cell.alignment = CA
                cell.border    = BD

    wb.save(TRACKER_PATH)
    print(f"[OK] Tracker saved: {TRACKER_PATH}")
    total = sum(len(v["records"]) for v in SHEETS.values())
    print(f"     Sheets: {list(SHEETS.keys())}  |  Total records: {total}")


# ──────────────────────────────────────────────
# SEND EMAIL WITH ATTACHMENT
# ──────────────────────────────────────────────
def send_with_attachment(smtp_pass=None):
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    msg = MIMEMultipart()
    msg["From"]    = f"{SENDER_NAME} <{SMTP_USER}>"
    msg["To"]      = RECIPIENT
    msg["Subject"] = f"[Test] TaxReminder_Tracker.xlsx — {now_str}"

    body_html = f"""
<div style="font-family:Arial;font-size:14px;color:#333;line-height:1.6;">
<p>Hi,</p>
<p>Please find attached the <b>TaxReminder_Tracker.xlsx</b> test file
generated on {now_str}.</p>
<p>It contains test records for all three reminder types
(<b>Bookkeeping / PST / GST</b>) in three languages
(<b>ENG / CHN / VIE</b>), covering scenarios such as:
<ul>
  <li>Pending Round 1 (no emails sent yet)</li>
  <li>Round 1 &amp; 2 already sent (should be skipped)</li>
  <li>Inactive client (should be skipped)</li>
  <li>Monthly and Quarterly frequencies</li>
</ul>
</p>
<p>Best regards,<br><b>ExampleCo Group</b><br>Example CPA Firm P.C. Inc.<br>
infosk@example-firm.com</p>
</div>
"""
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    # Attach the Excel file
    with open(TRACKER_PATH, "rb") as f:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(f.read())
    encoders.encode_base64(part)
    part.add_header(
        "Content-Disposition",
        f'attachment; filename="TaxReminder_Tracker.xlsx"',
    )
    msg.attach(part)

    client = EmailClient.from_settings(SENDER_NAME, password=smtp_pass)
    client.connect_imap()
    try:
        client.send_raw(msg.as_bytes(), RECIPIENT)
    finally:
        client.quit()

    print(f"[OK] Email sent → {RECIPIENT}")


# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 60)
    print("  TaxReminder — Generate Test Tracker & Send")
    print("=" * 60)

    print(f"\nStep 1: Building tracker with test data...")
    build_tracker()

    print(f"\nStep 2: Ready to send to {RECIPIENT}")
    if input("  Send this test email now? (Y/N): ").strip().upper() != "Y":
        print("[SKIP] File saved but email not sent.")
        sys.exit(0)

    smtp_pass = None
    if shared_settings.EMAIL_AUTH_MODE == "password":
        print("  (Password will not be shown as you type)")
        import getpass
        smtp_pass = getpass.getpass(f"  SMTP Password ({SMTP_USER}): ")
        if not smtp_pass:
            print("[SKIP] No password entered — file saved but email not sent.")
            sys.exit(0)
    else:
        print("  Using Gmail API OAuth authorization.")

    try:
        send_with_attachment(smtp_pass)
        print("\nDone.")
    except Exception as e:
        print(f"\n[ERROR] {e}")
        sys.exit(1)
