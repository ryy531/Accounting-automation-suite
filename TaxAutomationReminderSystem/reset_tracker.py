# -*- coding: utf-8 -*-
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.worksheet.datavalidation import DataValidation

TRACKER   = r"Z:\Scripts\TaxAutomationReminderSystem\TaxReminder_Tracker.xlsx"
TEST_EMAIL = "staff2.example@gmail.com"
MAX_ROW    = 1048576

HF  = Font(name="Arial", bold=True, size=11, color="FFFFFF")
HFL = PatternFill("solid", fgColor="2F5496")
HA  = Alignment(horizontal="center", vertical="center", wrap_text=True)
CF  = Font(name="Arial", size=10)
CA  = Alignment(vertical="center", wrap_text=True)
BD  = Border(left=Side(style="thin"), right=Side(style="thin"),
             top=Side(style="thin"),  bottom=Side(style="thin"))
COL_WIDTHS = {
    "Legal Name":32,"Owner Name":20,"Owner Email":32,"Language":10,"Assigned":12,
    "Client Type":15,"GST Frequency":16,"PST Frequency":16,"T2 YE":10,
    "Status":14,"R1SentDate":16,"R2SentDate":16,"R3SentDate":16,"Notes":28,
}

SHEETS = {
    "Bookkeeping": {
        "cols": ["Legal Name","Owner Name","Owner Email","Language","Assigned",
                 "Client Type","Status","R1SentDate","R2SentDate","R3SentDate","Notes"],
        "status_opts": '"Active,R1 Sent,R2 Sent,R3 Sent,Inactive"',
        "rows": [
            ["ENG Test Company Ltd.",                 "John Smith",           TEST_EMAIL, "ENG", "Alex", "Monthly", "Active",  "",           "",           "", "ENG - bookkeeping"],
            ["\u4e2d\u6587\u6d4b\u8bd5\u4f01\u4e1a\u6709\u9650\u516c\u53f8", "\u5f20\u4f1f",             TEST_EMAIL, "CHN", "Alex", "Monthly", "Active",  "",           "",           "", "CHN - bookkeeping"],
            ["Cong ty Kiem tra Tieng Viet",           "Nguyen Van An",        TEST_EMAIL, "VIE", "Alex", "Monthly", "Active",  "",           "",           "", "VIE - bookkeeping"],
            ["Already Sent Corp.",                    "Alice Brown",          TEST_EMAIL, "ENG", "MK",    "Monthly", "R2 Sent", "2026-03-01", "2026-03-15", "", "R1+R2 sent - pending R3"],
        ],
    },
    "PST": {
        "cols": ["Legal Name","Owner Name","Owner Email","Language","Assigned",
                 "PST Frequency","Status","R1SentDate","R2SentDate","R3SentDate","Notes"],
        "status_opts": '"Active,R1 Sent,R2 Sent,R3 Sent,Quarterly Sent,Inactive"',
        "rows": [
            ["ENG Monthly PST Inc.",                  "Bob Johnson",          TEST_EMAIL, "ENG", "Alex", "Monthly",   "Active", "", "", "", "ENG - monthly PST"],
            ["Cong ty PST Viet Nam",                  "Tran Thi Binh",        TEST_EMAIL, "VIE", "Alex", "Monthly",   "Active", "", "", "", "VIE - monthly PST"],
            ["\u4e2d\u6587PST\u6708\u62a5\u516c\u53f8", "\u674e\u6885",             TEST_EMAIL, "CHN", "Alex", "Monthly",   "Active", "", "", "", "CHN - monthly PST"],
            ["Quarterly PST Holdings",                "Carol White",          TEST_EMAIL, "ENG", "MK",    "Quarterly", "Active", "", "", "", "ENG - quarterly PST"],
            ["\u4e2d\u6587PST\u5b63\u62a5\u516c\u53f8", "\u738b\u82b3",             TEST_EMAIL, "CHN", "Alex", "Quarterly", "Active", "", "", "", "CHN - quarterly PST"],
        ],
    },
    "GST": {
        "cols": ["Legal Name","Owner Name","Owner Email","Language","Assigned",
                 "GST Frequency","Status","R1SentDate","R2SentDate","R3SentDate","Notes"],
        "status_opts": '"Active,R1 Sent,R2 Sent,R3 Sent,Inactive"',
        "rows": [
            ["ENG Monthly GST Corp.",                 "David Lee",            TEST_EMAIL, "ENG", "Alex", "Monthly",   "Active",   "", "", "", "ENG - monthly GST"],
            ["\u4e2d\u6587GST\u6708\u62a5\u516c\u53f8", "\u738b\u82b3",             TEST_EMAIL, "CHN", "MK",    "Monthly",   "Active",   "", "", "", "CHN - monthly GST"],
            ["Cong ty GST Hang Quy",                  "Le Thi Cuc",           TEST_EMAIL, "VIE", "Alex", "Quarterly", "Active",   "", "", "", "VIE - quarterly GST"],
            ["ENG Quarterly GST Holdings",            "Frank Wilson",         TEST_EMAIL, "ENG", "Alex", "Quarterly", "Active",   "", "", "", "ENG - quarterly GST"],
            ["\u4e2d\u6587GST\u5b63\u62a5\u516c\u53f8", "\u9648\u5170",             TEST_EMAIL, "CHN", "Alex", "Quarterly", "Active",   "", "", "", "CHN - quarterly GST"],
            ["Inactive Test Ltd.",                    "Eve Martin",           TEST_EMAIL, "ENG", "Alex", "Monthly",   "Inactive", "", "", "", "Inactive - must be skipped"],
        ],
    },
    "T2": {
        "cols": ["Legal Name","Owner Name","Owner Email","Language","Assigned",
                 "T2 YE","Status","R1SentDate","R2SentDate","R3SentDate","Notes"],
        "status_opts": '"Active,R1 Sent,R2 Sent,R3 Sent,Inactive"',
        "rows": [
            # Feb YE → R1 in March
            ["ENG T2 Feb YE Corp.",              "George Hall",    TEST_EMAIL, "ENG", "Alex", "Feb", "Active", "", "", "", "ENG - Feb YE -> R1 in Mar"],
            ["\u4e2d\u6587T2\u4e8c\u6708\u516c\u53f8", "\u5468\u660e",   TEST_EMAIL, "CHN", "Alex", "Feb", "Active", "", "", "", "CHN - Feb YE -> R1 in Mar"],
            # Jan YE → R2 in March
            ["ENG T2 Jan YE Ltd.",               "Hannah Scott",   TEST_EMAIL, "ENG", "MK",    "Jan", "Active", "", "", "", "ENG - Jan YE -> R2 in Mar"],
            ["\u4e2d\u6587T2\u4e00\u6708\u516c\u53f8", "\u5434\u5170",   TEST_EMAIL, "CHN", "MK",    "Jan", "Active", "", "", "", "CHN - Jan YE -> R2 in Mar"],
            # Dec YE → R3 in March
            ["ENG T2 Dec YE Inc.",               "Ian Turner",     TEST_EMAIL, "ENG", "Alex", "Dec", "Active", "", "", "", "ENG - Dec YE -> R3 in Mar"],
            ["Cong ty T2 Thang 12",              "Pham Van Duc",   TEST_EMAIL, "VIE", "Alex", "Dec", "Active", "", "", "", "VIE - Dec YE -> R3 in Mar"],
            # NA YE → must be skipped by parse_t2_ye
            ["T2 NA YE Corp.",                   "Jane Adams",     TEST_EMAIL, "ENG", "Alex", "NA",  "Active", "", "", "", "NA YE - must be skipped"],
        ],
    },
}

wb = openpyxl.Workbook()
wb.remove(wb.active)

for sheet_name, cfg in SHEETS.items():
    ws   = wb.create_sheet(sheet_name)
    cols = cfg["cols"]

    for ci, cn in enumerate(cols, 1):
        c = ws.cell(row=1, column=ci, value=cn)
        c.font = HF; c.fill = HFL; c.alignment = HA; c.border = BD
        ws.column_dimensions[openpyxl.utils.get_column_letter(ci)].width = COL_WIDTHS.get(cn, 15)
    ws.auto_filter.ref = f"A1:{openpyxl.utils.get_column_letter(len(cols))}1"
    ws.freeze_panes = "A2"

    for ri, row in enumerate(cfg["rows"], 2):
        for ci, val in enumerate(row, 1):
            c = ws.cell(row=ri, column=ci, value=val)
            c.font = CF; c.alignment = CA; c.border = BD

    status_col = openpyxl.utils.get_column_letter(cols.index("Status") + 1)
    dv = DataValidation(
        type="list",
        formula1=cfg["status_opts"],
        allow_blank=True,
        showDropDown=False,
        showErrorMessage=True,
        errorTitle="Invalid value",
        error="Please select a value from the dropdown list.",
    )
    dv.sqref = f"{status_col}2:{status_col}{MAX_ROW}"
    ws.add_data_validation(dv)
    print(f"  {sheet_name}: {len(cfg['rows'])} records | Status dropdown {status_col}2:{status_col}{MAX_ROW}")

wb.save(TRACKER)
print("Saved:", TRACKER)
