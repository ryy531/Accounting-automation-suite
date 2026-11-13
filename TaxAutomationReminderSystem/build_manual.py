# -*- coding: utf-8 -*-
from docx import Document
from docx.shared import Pt, RGBColor, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

doc = Document()

sec = doc.sections[0]
sec.top_margin    = Cm(2.0)
sec.bottom_margin = Cm(2.0)
sec.left_margin   = Cm(2.5)
sec.right_margin  = Cm(2.5)

BLUE  = RGBColor(0x2F, 0x54, 0x96)
TEAL  = RGBColor(0x0A, 0x5C, 0x6A)
GRAY  = RGBColor(0x60, 0x60, 0x60)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)

def shade_cell(cell, hex_color):
    tc   = cell._tc
    tcPr = tc.get_or_add_tcPr()
    shd  = OxmlElement('w:shd')
    shd.set(qn('w:val'),   'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'),  hex_color)
    tcPr.append(shd)

def h1(text):
    p = doc.add_heading(text, level=1)
    for run in p.runs:
        run.font.color.rgb = BLUE
    return p

def h2(text):
    p = doc.add_heading(text, level=2)
    for run in p.runs:
        run.font.color.rgb = TEAL
    return p

def body(text, bolds=None):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(4)
    if bolds:
        remaining = text
        for bp in bolds:
            idx = remaining.find(bp)
            if idx > 0:
                p.add_run(remaining[:idx]).font.size = Pt(11)
            r = p.add_run(bp)
            r.bold = True
            r.font.size = Pt(11)
            remaining = remaining[idx + len(bp):]
        if remaining:
            p.add_run(remaining).font.size = Pt(11)
    else:
        p.add_run(text).font.size = Pt(11)
    return p

def bullet(text, bold_word=None):
    p = doc.add_paragraph(style='List Bullet')
    p.paragraph_format.space_after = Pt(2)
    if bold_word:
        idx = text.find(bold_word)
        if idx >= 0:
            p.add_run(text[:idx]).font.size = Pt(11)
            r = p.add_run(bold_word)
            r.bold = True
            r.font.size = Pt(11)
            p.add_run(text[idx + len(bold_word):]).font.size = Pt(11)
            return p
    p.add_run(text).font.size = Pt(11)
    return p

def tip(text):
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.8)
    p.paragraph_format.space_after = Pt(4)
    run = p.add_run("\u2139  " + text)
    run.font.size      = Pt(10)
    run.font.color.rgb = GRAY
    run.font.italic    = True
    return p

def tbl_header(table, cols, color="2F5496"):
    hdr = table.rows[0].cells
    for cell, txt in zip(hdr, cols):
        shade_cell(cell, color)
        r = cell.paragraphs[0].add_run(txt)
        r.bold = True
        r.font.color.rgb = WHITE
        r.font.size = Pt(11)

def tbl_row(table, values):
    row = table.add_row().cells
    for cell, val in zip(row, values):
        cell.paragraphs[0].add_run(val).font.size = Pt(10)
    return row

# ── TITLE ────────────────────────────────────────────────────
for txt, sz, col in [
    ("\u7a0e\u52a1\u63d0\u9192\u7cfb\u7edf", 22, BLUE),
    ("Tax Reminder System", 14, TEAL),
    ("\u4f7f\u7528\u8bf4\u660e  \u2014  \u9002\u7528\u4e8e\u65e0\u8ba1\u7b97\u673a\u4e13\u4e1a\u80cc\u666f\u7684\u65e5\u5e38\u64cd\u4f5c\u5458", 11, GRAY),
    ("\u7248\u672c\u65e5\u671f\uff1a2026-03  |  ExampleCo Group / Example CPA Firm P.C. Inc.", 10, GRAY),
]:
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(txt)
    r.font.size = Pt(sz)
    r.font.color.rgb = col
    if sz == 22:
        r.bold = True

doc.add_paragraph()

# ════════════════════════════════════════════════════════
# 1. 系统是做什么的
# ════════════════════════════════════════════════════════
h1("1.  \u7cfb\u7edf\u662f\u505a\u4ec0\u4e48\u7684\uff1f")
body("\u672c\u7cfb\u7edf\u4f1a\u81ea\u52a8\u4ece\u5ba2\u6237\u4e3b\u8868\u4e2d\u8bfb\u53d6\u4fe1\u606f\uff0c\u6bcf\u6708\u5728\u56fa\u5b9a\u65e5\u671f\u5411\u5ba2\u6237\u53d1\u9001\u63d0\u9192\u90ae\u4ef6\uff0c\u63d0\u9192\u4ed6\u4eec\u63d0\u4ea4\u6587\u4ef6\u6216\u5b8c\u6210\u7533\u62a5\u3002")
body("\u60a8\u4e0d\u9700\u8981\u624b\u52a8\u627e\u5ba2\u6237\u540d\u5355\u3001\u9010\u6761\u53d1\u90ae\u4ef6\u2014\u2014\u53ea\u9700\u6309\u7167\u672c\u6587\u6863\u7684\u6b65\u9aa4\u64cd\u4f5c\uff0c\u7cfb\u7edf\u4f1a\u81ea\u52a8\u5b8c\u6210\u3002")

h2("\u4e09\u79cd\u63d0\u9192\u7c7b\u578b")
body("\u7cfb\u7edf\u6bcf\u6708\u53d1\u9001\u4ee5\u4e0b\u4e09\u7c7b\u63d0\u9192\u90ae\u4ef6\uff1a")

t = doc.add_table(rows=1, cols=3)
t.style = 'Table Grid'
tbl_header(t, ["\u7c7b\u578b", "\u89e6\u53d1\u6761\u4ef6", "\u53d1\u9001\u9891\u7387"])
for row_data in [
    ("\u8bb0\u8d26\u63d0\u9192 Bookkeeping",
     "\u5ba2\u6237\u4e3b\u8868\uff1aClient Type = Monthly",
     "\u6bcf\u6708\u4e09\u8f6e\uff081\u65e5 / 15\u65e5 / 25\u65e5\uff09"),
    ("PST \u6708\u5ea6\u63d0\u9192",
     "\u5ba2\u6237\u4e3b\u8868\uff1aPST Frequency = Monthly",
     "\u6bcf\u6708\u4e09\u8f6e\uff081\u65e5 / 15\u65e5 / 25\u65e5\uff09"),
    ("PST \u5b63\u5ea6\u63d0\u9192",
     "\u5ba2\u6237\u4e3b\u8868\uff1aPST Frequency = Quarterly",
     "\u6bcf\u5e74 4 \u6b21\uff081\u6708 / 4\u6708 / 7\u6708 / 10\u6708\uff09"),
    ("GST/HST \u63d0\u9192",
     "\u5ba2\u6237\u4e3b\u8868\uff1aGST Frequency = Monthly \u6216 Quarterly",
     "\u6bcf\u6708\u4e09\u8f6e\uff081\u65e5 / 15\u65e5 / 25\u65e5\uff09"),
]:
    tbl_row(t, row_data)

doc.add_paragraph()

# ════════════════════════════════════════════════════════
# 2. 每月如何操作
# ════════════════════════════════════════════════════════
h1("2.  \u6bcf\u6708\u5982\u4f55\u64cd\u4f5c\uff1f\uff08\u5feb\u901f\u6307\u5357\uff09")
body("\u6bcf\u6708\u5728 1 \u65e5\u30015 \u65e5\u300125 \u65e5\u4e09\u4e2a\u65f6\u95f4\u70b9\u5404\u64cd\u4f5c\u4e00\u6b21\uff0c\u6bcf\u6b21\u5927\u7ea6 3 \u5206\u949f\u3002")
body("\u5728 1 \u6708\u30014 \u6708\u30017 \u6708\u300110 \u6708\u65f6\uff0c\u8fd8\u9700\u8981\u989d\u5916\u53d1\u9001\u4e00\u6b21 PST \u5b63\u5ea6\u63d0\u9192\uff08\u7cfb\u7edf\u4f1a\u81ea\u52a8\u5f39\u51fa\u63d0\u793a\uff09\u3002",
     ["1 \u6708\u30014 \u6708\u30017 \u6708\u300110 \u6708"])

h2("\u6807\u51c6\u64cd\u4f5c\u6b65\u9aa4\uff08\u6bcf\u6b21\uff09")
for step in [
    "\u53cc\u51fb run_reminder.bat \u542f\u52a8\u7a0b\u5e8f",
    "\u9009 [1] Update Tracker  \u2014  \u7a0b\u5e8f\u8bfb\u53d6\u4e3b\u8868\u3001\u66f4\u65b0\u540d\u5355\uff08\u9996\u6b21\u6216\u4e3b\u8868\u66f4\u65b0\u540e\u5fc5\u505a\uff09",
    "\u9009 [5] Send All Three  \u2014  \u4e00\u952e\u53d1\u9001\u8bb0\u8d26\u3001PST \u6708\u5ea6\u3001GST/HST \u4e09\u7c7b\u63d0\u9192",
    "\u82e5\u5f53\u6708\u4e3a 1 / 4 / 7 / 10 \u6708\uff0c\u7cfb\u7edf\u81ea\u52a8\u8be2\u95ee\u662f\u5426\u53d1\u9001 PST \u5b63\u5ea6\u63d0\u9192\uff0c\u9009 Y \u5373\u53ef",
    "\u8f93\u5165 SMTP \u5bc6\u7801\uff08\u53ea\u9700\u8f93\u5165\u4e00\u6b21\uff09\uff0c\u7b49\u5f85\u53d1\u9001\u5b8c\u6210",
    "\u7a0b\u5e8f\u81ea\u52a8\u4fdd\u5b58\u8ffd\u8e2a\u8868\uff0c\u6309 Enter \u9000\u51fa",
]:
    bullet(step)

h2("\u4e09\u4e2a\u53d1\u9001\u65f6\u95f4\u70b9\uff08\u6bcf\u6708\uff09")
t2 = doc.add_table(rows=1, cols=3)
t2.style = 'Table Grid'
tbl_header(t2, ["\u8f6e\u6b21", "\u64cd\u4f5c\u65e5\u671f", "\u6a21\u677f\u540e\u7f00"])
for rd in [
    ("Round 1", "\u6bcf\u6708 1 \u65e5\u524d\u540e", "- 1st"),
    ("Round 2", "\u6bcf\u6708 15 \u65e5\u524d\u540e", "- 15th"),
    ("Round 3", "\u6bcf\u6708 25 \u65e5\u524d\u540e", "- 25th"),
]:
    tbl_row(t2, rd)
doc.add_paragraph()
body("\u7a0b\u5e8f\u4f1a\u6839\u636e\u4eca\u5929\u7684\u65e5\u671f\uff0c\u81ea\u52a8\u5224\u65ad\u5f53\u524d\u5904\u4e8e\u54ea\u4e00\u8f6e\uff0c\u65e0\u9700\u624b\u52a8\u5207\u6362\u3002")

# ════════════════════════════════════════════════════════
# 3. 程序菜单说明
# ════════════════════════════════════════════════════════
h1("3.  \u7a0b\u5e8f\u83dc\u5355\u8bf4\u660e")
body("\u542f\u52a8\u7a0b\u5e8f\u540e\uff0c\u60a8\u4f1a\u770b\u5230\u5982\u4e0b\u83dc\u5355\uff08\u9ed1\u8272\u547d\u4ee4\u7a97\u53e3\uff09\uff1a")

p_code = doc.add_paragraph()
p_code.paragraph_format.left_indent = Cm(0.8)
menu_text = "\n".join([
    "============================================================",
    "  Tax Reminder System   [2026-03-18]",
    "  Current: March 2026  |  Round 1 (1st)",
    "============================================================",
    "  [1] Update Tracker        (read master -> update all 3 lists)",
    "  ----------------------------------------",
    "  [2] Send Bookkeeping      (Round 1 (1st))",
    "  [3] Send PST Monthly      (Round 1 (1st))",
    "  [4] Send GST/HST          (Round 1 (1st))",
    "  [5] Send All Three        (Round 1 (1st))",
    "  ----------------------------------------",
    "  [6] PST Quarterly\uff0804,07,10,01\uff09",
    "  ----------------------------------------",
    "  [7] Delete Client from Tracker",
    "  [0] Exit",
    "============================================================",
])
rc = p_code.add_run(menu_text)
rc.font.name = 'Courier New'
rc.font.size = Pt(9)

doc.add_paragraph()
h2("\u5404\u9009\u9879\u529f\u80fd\u8bf4\u660e")
t3 = doc.add_table(rows=1, cols=3)
t3.style = 'Table Grid'
tbl_header(t3, ["\u9009\u9879", "\u529f\u80fd", "\u8bf4\u660e"])
menu_rows = [
    ("[1]", "Update Tracker",
     "\u4ece\u4e3b\u8868\u8bfb\u53d6\u6700\u65b0\u5ba2\u6237\u6570\u636e\uff0c\u66f4\u65b0\u4e09\u5f20\u8ffd\u8e2a\u8868\u3002\u6bcf\u6708\u9996\u6b21\u64cd\u4f5c\u6216\u4e3b\u8868\u6709\u66f4\u65b0\u65f6\u5fc5\u987b\u5148\u8fd0\u884c\u3002\u540c\u65f6\u663e\u793a\u672c\u8f6e\u5f85\u53d1\u9001\u540d\u5355\uff08\u542b PST \u5b63\u5ea6\u5f85\u53d1\u6570\u91cf\uff09\u3002"),
    ("[2]", "Send Bookkeeping",
     "\u5411 Client Type = Monthly \u7684\u5ba2\u6237\u53d1\u9001\u5f53\u524d\u8f6e\u6b21\u8bb0\u8d26\u63d0\u9192\u90ae\u4ef6\u3002"),
    ("[3]", "Send PST Monthly",
     "\u5411 PST Frequency = Monthly \u7684\u5ba2\u6237\u53d1\u9001\u5f53\u524d\u8f6e\u6b21 PST \u6708\u5ea6\u63d0\u9192\u90ae\u4ef6\u3002\uff08PST Quarterly \u5ba2\u6237\u4e0d\u5728\u6b64\u5217\uff09"),
    ("[4]", "Send GST/HST",
     "\u5411 GST Frequency = Monthly \u6216 Quarterly \u7684\u5ba2\u6237\u53d1\u9001\u5f53\u524d\u8f6e\u6b21 GST/HST \u63d0\u9192\u90ae\u4ef6\u3002"),
    ("[5]", "Send All Three \u2605\u63a8\u8350",
     "\u4e00\u952e\u53d1\u9001\u8bb0\u8d26\u3001PST \u6708\u5ea6\u3001GST/HST \u4e09\u7c7b\u90ae\u4ef6\uff0c\u53ea\u9700\u8f93\u5165\u4e00\u6b21 SMTP \u5bc6\u7801\u3002\n\u5728 1/4/7/10 \u6708\u5b8c\u6210\u540e\uff0c\u7cfb\u7edf\u4f1a\u989d\u5916\u8be2\u95ee\u662f\u5426\u53d1\u9001 PST \u5b63\u5ea6\u63d0\u9192\u3002"),
    ("[6]", "PST Quarterly\uff0804,07,10,01\uff09",
     "\u5355\u72ec\u53d1\u9001 PST \u5b63\u5ea6\u63d0\u9192\u90ae\u4ef6\u3002\u4ec5\u5728 1/4/7/10 \u6708\u6709\u6548\uff0c\u5176\u4ed6\u6708\u4efd\u9009\u62e9\u540e\u4f1a\u63d0\u793a\u201c\u975e\u5b63\u5ea6\u6708\u201d\u5e76\u8df3\u8fc7\u3002"),
    ("[7]", "Delete Client from Tracker",
     "\u4ece\u8ffd\u8e2a\u8868\u4e2d\u5220\u9664\u5ba2\u6237\uff08\u4e0d\u4fee\u6539\u4e3b\u8868\uff09\u3002\u5ba2\u6237\u53d6\u6d88\u670d\u52a1\u65f6\u4f7f\u7528\u3002"),
    ("[0]", "Exit", "\u9000\u51fa\u7a0b\u5e8f\u3002"),
]
for sel, func, desc in menu_rows:
    row = t3.add_row().cells
    row[0].paragraphs[0].add_run(sel).font.size = Pt(10)
    r = row[1].paragraphs[0].add_run(func)
    r.font.size = Pt(10)
    r.bold = True
    row[2].paragraphs[0].add_run(desc).font.size = Pt(10)

doc.add_paragraph()

# ════════════════════════════════════════════════════════
# 4. 密码
# ════════════════════════════════════════════════════════
h1("4.  \u9700\u8981\u8f93\u5165\u54ea\u4e9b\u5bc6\u7801\uff1f")
body("\u7a0b\u5e8f\u8fd0\u884c\u8fc7\u7a0b\u4e2d\u4f1a\u8981\u6c42\u8f93\u5165\u4e24\u79cd\u5bc6\u7801\u3002\u8f93\u5165\u5bc6\u7801\u65f6\u5c4f\u5e55\u4e0d\u663e\u793a\u4efb\u4f55\u5b57\u7b26\uff0c\u8fd9\u662f\u5b89\u5168\u8bbe\u8ba1\uff0c\u8bf7\u653e\u5fc3\u6b63\u5e38\u8f93\u5165\u540e\u6309\u56de\u8f66\u3002")
t4 = doc.add_table(rows=1, cols=3)
t4.style = 'Table Grid'
tbl_header(t4, ["\u5bc6\u7801\u7c7b\u578b", "\u4f55\u65f6\u8f93\u5165", "\u5411\u8c01\u7d22\u53d6"])
for rd in [
    ("\u4e3b\u8868\u6587\u4ef6\u5bc6\u7801", "\u9009 [1] Update Tracker \u65f6", "\u8be2\u95ee Alex Yu"),
    ("SMTP \u90ae\u4ef6\u5bc6\u7801", "\u53d1\u9001\u90ae\u4ef6\u65f6\uff08\u6bcf\u6b21\u53d1\u9001\u53ea\u9700\u8f93\u5165\u4e00\u6b21\uff09", "\u8be2\u95ee Alex Yu"),
]:
    tbl_row(t4, rd)
doc.add_paragraph()

# ════════════════════════════════════════════════════════
# 5. 追踪表
# ════════════════════════════════════════════════════════
h1("5.  \u8ffd\u8e2a\u8868\uff08Tracker\uff09\u662f\u4ec0\u4e48\uff1f")
body("\u8ffd\u8e2a\u8868\u662f\u4e00\u4e2a Excel \u6587\u4ef6\uff08TaxReminder_Tracker.xlsx\uff09\uff0c\u7a0b\u5e8f\u81ea\u52a8\u7ef4\u62a4\uff0c\u8bb0\u5f55\u6bcf\u4f4d\u5ba2\u6237\u6bcf\u8f6e\u7684\u53d1\u9001\u60c5\u51b5\u3002\u60a8\u65e0\u9700\u624b\u52a8\u7f16\u8f91\uff0c\u4f46\u53ef\u4ee5\u67e5\u770b\u3002")
body("\u6587\u4ef6\u4f4d\u7f6e\uff1a  Z:\\Scripts\\TaxAutomationReminderSystem\\TaxReminder_Tracker.xlsx")
body("\u8ffd\u8e2a\u8868\u5305\u542b\u4e09\u4e2a\u5de5\u4f5c\u8868\uff0c\u5206\u522b\u5bf9\u5e94\u4e09\u7c7b\u63d0\u9192\uff1a")
for item in [
    "Bookkeeping \u2014 \u8bb0\u8d26\u63d0\u9192\u540d\u5355",
    "PST \u2014 PST \u6708\u5ea6 + \u5b63\u5ea6\u5ba2\u6237\u540d\u5355\uff08\u540c\u4e00\u5f20\u8868\uff0c\u6309 PST Frequency \u5217\u533a\u5206\uff09",
    "GST \u2014 GST/HST \u63d0\u9192\u540d\u5355",
]:
    bullet(item)

h2("\u5217\u8bf4\u660e")
t5 = doc.add_table(rows=1, cols=2)
t5.style = 'Table Grid'
tbl_header(t5, ["\u5217\u540d", "\u542b\u4e49"])
for col, desc in [
    ("Legal Name", "\u516c\u53f8\u6cd5\u5b9a\u540d\u79f0"),
    ("Owner Name", "\u5ba2\u6237\u8054\u7cfb\u4eba\u59d3\u540d"),
    ("Owner Email", "\u63a5\u6536\u63d0\u9192\u90ae\u4ef6\u7684\u90ae\u7b31"),
    ("Language", "\u90ae\u4ef6\u8bed\u8a00\uff1aENG\uff08\u82f1\u8bed\uff09/ CHN\uff08\u4e2d\u6587\uff09/ VIE\uff08\u8d8a\u5357\u8bed\uff09"),
    ("Assigned", "\u8d1f\u8d23\u5458\u5de5"),
    ("Client Type / PST Frequency / GST Frequency", "\u89e6\u53d1\u6761\u4ef6\uff08Monthly / Quarterly\uff09"),
    ("Status", "\u5f53\u524d\u72b6\u6001\uff08\u89c1\u4e0b\u8868\uff09"),
    ("R1SentDate / R2SentDate / R3SentDate",
     "\u5404\u8f6e\u90ae\u4ef6\u53d1\u9001\u65e5\u671f\uff08\u6708\u5ea6\u7c7b\u578b\uff09\uff1b\u5b63\u5ea6\u7c7b\u578b\u4ec5\u7528 R1SentDate \u8bb0\u5f55\u5b63\u5ea6\u53d1\u9001\u65e5\u671f"),
]:
    row = t5.add_row().cells
    r = row[0].paragraphs[0].add_run(col)
    r.font.size = Pt(10)
    r.bold = True
    row[1].paragraphs[0].add_run(desc).font.size = Pt(10)

doc.add_paragraph()
h2("Status \u72b6\u6001\u503c\u8bf4\u660e")
for status, desc in [
    ("Active", "\u6b63\u5e38\uff0c\u672c\u8f6e\u4f1a\u53d1\u9001\u90ae\u4ef6"),
    ("R1 Sent / R2 Sent / R3 Sent", "\u672c\u6708\u8be5\u8f6e\u5df2\u53d1\u9001\uff0c\u4e0d\u4f1a\u91cd\u590d\u53d1\u9001"),
    ("Quarterly Sent", "\u672c\u5b63\u5ea6\u5b63\u5ea6\u63d0\u9192\u5df2\u53d1\u9001\uff08PST Quarterly \u4e13\u7528\uff09"),
    ("Inactive", "\u5df2\u505c\u7528\uff0c\u4e0d\u518d\u53d1\u9001\u4efb\u4f55\u63d0\u9192"),
]:
    p = doc.add_paragraph(style='List Bullet')
    p.paragraph_format.space_after = Pt(2)
    r = p.add_run(status + "  ")
    r.bold = True
    r.font.size = Pt(11)
    p.add_run("\u2014 " + desc).font.size = Pt(11)

doc.add_paragraph()

# ════════════════════════════════════════════════════════
# 6. 删除客户
# ════════════════════════════════════════════════════════
h1("6.  \u5982\u4f55\u5220\u9664\u67d0\u4f4d\u5ba2\u6237\uff1f")
body("\u5f53\u5ba2\u6237\u53d6\u6d88\u670d\u52a1\u540e\uff0c\u53ef\u4ee5\u4ece\u8ffd\u8e2a\u8868\u4e2d\u79fb\u9664\u8be5\u5ba2\u6237\uff0c\u4f7f\u5176\u4e0d\u518d\u6536\u5230\u63d0\u9192\u90ae\u4ef6\u3002")
tip("\u6b64\u64cd\u4f5c\u53ea\u5220\u9664\u8ffd\u8e2a\u8868\u4e2d\u7684\u8bb0\u5f55\uff0c\u4e0d\u4f1a\u4fee\u6539\u5ba2\u6237\u4e3b\u8868 Excel \u6587\u4ef6\u3002")
h2("\u5220\u9664\u64cd\u4f5c\u6b65\u9aa4")
for step in [
    "\u542f\u52a8\u7a0b\u5e8f\uff0c\u5728\u83dc\u5355\u4e2d\u9009\u62e9 [7] Delete Client from Tracker",
    "\u8f93\u5165\u5ba2\u6237\u540d\u79f0\uff08\u652f\u6301\u6a21\u7cca\u641c\u7d22\uff0c\u53ea\u9700\u8f93\u5165\u90e8\u5206\u540d\u5b57\uff09",
    "\u7a0b\u5e8f\u663e\u793a\u6240\u6709\u5339\u914d\u9879\uff0c\u8f93\u5165\u7f16\u53f7\u5220\u9664\u5355\u6761\uff0c\u6216\u8f93\u5165 A \u5220\u9664\u5168\u90e8\u5339\u914d\u9879",
    "\u7cfb\u7edf\u8981\u6c42\u4e8c\u6b21\u786e\u8ba4\uff0c\u8f93\u5165 Y \u786e\u8ba4\u5220\u9664\uff0c\u8ffd\u8e2a\u8868\u81ea\u52a8\u4fdd\u5b58",
]:
    bullet(step)
doc.add_paragraph()

# ════════════════════════════════════════════════════════
# 7. 邮件模板说明
# ════════════════════════════════════════════════════════
h1("7.  \u90ae\u4ef6\u6a21\u677f\u8bf4\u660e")
body("\u6bcf\u5c01\u63d0\u9192\u90ae\u4ef6\u7684\u5185\u5bb9\u6765\u81ea\u5bf9\u5e94\u7684 Word \u6a21\u677f\u6587\u4ef6\uff08.docx\uff09\u3002\u6a21\u677f\u652f\u6301\u5f69\u8272\u5b57\u4f53\u3001\u9ad8\u4eae\u3001\u56fe\u7247\u3001\u8868\u683c\u7b49\u5bcc\u6587\u672c\u683c\u5f0f\uff0c\u53d1\u9001\u65f6\u4f1a\u5b8c\u6574\u4fdd\u7559\u3002")
body("\u6a21\u677f\u5b58\u653e\u5728\u4ee5\u4e0b\u6587\u4ef6\u5939\uff1a")
p_path = doc.add_paragraph()
p_path.paragraph_format.left_indent = Cm(0.8)
r_path = p_path.add_run("Z:\\Scripts\\TaxAutomationReminderSystem\\ReminderTemplet\\")
r_path.font.name = 'Courier New'
r_path.font.size = Pt(10)

doc.add_paragraph()
h2("\u6a21\u677f\u6587\u4ef6\u547d\u540d\u89c4\u5219")
body("\u547d\u540d\u683c\u5f0f\uff1a  \u7c7b\u578b - \u8f6e\u6b21 - \u8bed\u8a00.docx")
t6 = doc.add_table(rows=1, cols=3)
t6.style = 'Table Grid'
tbl_header(t6, ["\u7c7b\u578b", "\u8f6e\u6b21", "\u8bed\u8a00"])
for t_type, t_round, t_lang in [
    ("Monthly Bookkeeping Reminder", "- 1st / - 15th / - 25th", "- ENG / - CHN / - VIE"),
    ("Monthly PST Reminder",         "- 1st / - 15th / - 25th", "- ENG / - CHN / - VIE"),
    ("Monthly GST Reminder",         "- 1st / - 15th / - 25th", "- ENG / - CHN / - VIE"),
    ("Quarterly PST Reminder",       "\uff08\u65e0\u8f6e\u6b21\uff0c\u6bcf\u5b63\u5ea6\u53d1\u9001\u4e00\u6b21\uff09", "- ENG / - CHN / - VIE"),
]:
    tbl_row(t6, [t_type, t_round, t_lang])
doc.add_paragraph()
tip("\u8bed\u8a00\u56de\u9000\u89c4\u5219\uff1a\u82e5 CHN / VIE \u6a21\u677f\u6587\u4ef6\u4e0d\u5b58\u5728\uff0c\u7cfb\u7edf\u81ea\u52a8\u56de\u9000\u4f7f\u7528 ENG \u6a21\u677f\u3002ENG \u6a21\u677f\u4e3a\u5fc5\u987b\u5b58\u5728\u7684\u57fa\u7840\u6a21\u677f\u3002")

h2("\u6a21\u677f\u5185\u5bb9\u683c\u5f0f\u8981\u6c42")
for item in [
    "\u7b2c\u4e00\u884c\uff08\u53ef\u9009\uff09\uff1aSubject: \u90ae\u4ef6\u4e3b\u9898  \u2014  \u6b64\u884c\u4f1a\u88ab\u63d0\u53d6\u4e3a\u90ae\u4ef6\u6807\u9898\uff0c\u4e0d\u4f1a\u51fa\u73b0\u5728\u6b63\u6587",
    "\u6b63\u6587\u5185\u5bb9\uff1a\u53ef\u4f7f\u7528\u5f69\u8272\u5b57\u4f53\u3001\u9ad8\u4eae\u3001\u7c97\u4f53\u3001\u659c\u4f53\u3001\u56fe\u7247\u3001\u8868\u683c\u7b49\u683c\u5f0f\uff0c\u7cfb\u7edf\u4f1a\u5b8c\u6574\u4fdd\u7559",
    "\u5360\u4f4d\u7b26\uff1a{NAME}\u3001{COMPANY}\u3001{MONTH}  \u2014  \u53d1\u9001\u65f6\u81ea\u52a8\u66ff\u6362\u4e3a\u771f\u5b9e\u5ba2\u6237\u4fe1\u606f",
]:
    bullet(item)

h2("\u5360\u4f4d\u7b26\u8bf4\u660e\uff08\u81ea\u52a8\u66ff\u6362\u5185\u5bb9\uff09")
t7 = doc.add_table(rows=1, cols=3)
t7.style = 'Table Grid'
tbl_header(t7, ["\u5360\u4f4d\u7b26", "\u66ff\u6362\u5185\u5bb9", "\u793a\u4f8b"])
for ph, meaning, example in [
    ("{NAME}",    "\u5ba2\u6237\u8054\u7cfb\u4eba\u59d3\u540d (Owner Name)", "\u5f20\u4f1f"),
    ("{COMPANY}", "\u516c\u53f8\u6cd5\u5b9a\u540d\u79f0 (Legal Name)",     "\u4e2d\u6587\u6d4b\u8bd5\u4f01\u4e1a\u6709\u9650\u516c\u53f8"),
    ("{MONTH}",   "\u5f53\u524d\u6708\u4efd\uff08\u6309\u90ae\u4ef6\u8bed\u8a00\u663e\u793a\uff09", "March / 3\u6708 / Th\u00e1ng 3"),
]:
    row = t7.add_row().cells
    r = row[0].paragraphs[0].add_run(ph)
    r.font.name = 'Courier New'
    r.font.size = Pt(10)
    row[1].paragraphs[0].add_run(meaning).font.size = Pt(10)
    row[2].paragraphs[0].add_run(example).font.size = Pt(10)

doc.add_paragraph()

# ════════════════════════════════════════════════════════
# 8. 文件位置
# ════════════════════════════════════════════════════════
h1("8.  \u6587\u4ef6\u4f4d\u7f6e\u4e00\u89c8")
body("\u4ee5\u4e0b\u662f\u7cfb\u7edf\u76f8\u5173\u6587\u4ef6\u7684\u4f4d\u7f6e\uff0c\u65e5\u5e38\u64cd\u4f5c\u53ea\u9700\u5173\u6ce8\u52a0\u7c97\u7684\u6587\u4ef6\u3002")
t8 = doc.add_table(rows=1, cols=2)
t8.style = 'Table Grid'
tbl_header(t8, ["\u6587\u4ef6 / \u6587\u4ef6\u5939", "\u8bf4\u660e"])
for fname, fdesc in [
    ("run_reminder.bat \u2605", "\u53cc\u51fb\u542f\u52a8\u7cfb\u7edf\uff08\u4e3b\u5165\u53e3\uff09"),
    ("tax_reminder.py",         "\u7cfb\u7edf\u4e3b\u7a0b\u5e8f\uff08\u52ff\u4fee\u6539\uff09"),
    ("requirements.txt",        "Python \u4f9d\u8d56\u5305\u6e05\u5355\uff08\u65b0\u7535\u8111\u5b89\u88c5\u65f6\u4f7f\u7528\uff09"),
    ("TaxReminder_Tracker.xlsx \u2605", "\u8ffd\u8e2a\u8868\uff08\u81ea\u52a8\u7ef4\u62a4\uff0c\u53ef\u67e5\u770b\uff09"),
    ("ReminderTemplet\\ \u2605",       "\u90ae\u4ef6\u6a21\u677f\u6587\u4ef6\u5939\uff08\u5b58\u653e\u6240\u6709 .docx \u6a21\u677f\uff09"),
    ("Logs\\",                          "\u65e5\u5fd7\u6587\u4ef6\u5939\uff08\u53d1\u9001\u8bb0\u5f55\uff0c\u6309\u65e5\u671f\u547d\u540d\uff09"),
    ("Backup\\",                        "\u8ffd\u8e2a\u8868\u5907\u4efd\uff08\u6bcf\u6b21\u4fdd\u5b58\u524d\u81ea\u52a8\u5907\u4efd\uff09"),
]:
    row = t8.add_row().cells
    r = row[0].paragraphs[0].add_run(fname)
    r.font.name = 'Courier New'
    r.font.size = Pt(10)
    if "\u2605" in fname:
        r.bold = True
    row[1].paragraphs[0].add_run(fdesc).font.size = Pt(10)

doc.add_paragraph()

# ════════════════════════════════════════════════════════
# 9. 在新电脑上部署
# ════════════════════════════════════════════════════════
h1("9.  \u5728\u65b0\u7535\u8111\u4e0a\u90e8\u7f72")
body("\u5c06\u7cfb\u7edf\u8fc1\u79fb\u5230\u5176\u4ed6\u5458\u5de5\u7535\u8111\u65f6\uff0c\u6309\u4ee5\u4e0b\u6b65\u9aa4\u64cd\u4f5c\uff1a")

h2("\u5fc5\u987b\u5b89\u88c5\uff08\u4e00\u6b21\u6027\uff09")
t9 = doc.add_table(rows=1, cols=3)
t9.style = 'Table Grid'
tbl_header(t9, ["\u9879\u76ee", "\u8bf4\u660e", "\u83b7\u53d6\u65b9\u5f0f"])
for item, desc, how in [
    ("Python 3.8+",
     "\u811a\u672c\u8fd0\u884c\u73af\u5883",
     "https://www.python.org/downloads/\n\u5b89\u88c5\u65f6\u5fc5\u987b\u52fe\u9009 \"Add Python to PATH\""),
    ("\u4e09\u4e2a Python \u5305",
     "msoffcrypto-tool / openpyxl / python-docx",
     "\u53cc\u51fb run_reminder.bat \u4f1a\u81ea\u52a8\u5b89\u88c5\uff1b\n\u6216\u624b\u52a8\u8fd0\u884c\uff1apip install -r requirements.txt"),
    ("\u7f51\u7edc\u9a71\u52a8\u5668 Z:",
     "\u6620\u5c04\u5230 \\\\file-server\\Shared Folder",
     "\u8054\u7cfb IT \u914d\u7f6e\uff0c\u6216\u4f7f\u7528 UNC \u8def\u5f84\uff08\u811a\u672c\u81ea\u52a8\u56de\u9000\uff09"),
]:
    row = t9.add_row().cells
    row[0].paragraphs[0].add_run(item).font.size = Pt(10)
    row[1].paragraphs[0].add_run(desc).font.size = Pt(10)
    row[2].paragraphs[0].add_run(how).font.size  = Pt(10)

doc.add_paragraph()
h2("\u7f51\u7edc\u8981\u6c42")
for item in [
    "\u9632\u706b\u5899\u51fa\u7ad9\u7aef\u53e3 465 \u2014 SMTP \u53d1\u9001\u90ae\u4ef6\u4f7f\u7528\uff0c\u4f01\u4e1a\u7f51\u7edc\u53ef\u80fd\u9700\u8981 IT \u5f00\u653e",
    "\u7f51\u7edc\u9a71\u52a8\u5668 Z: \u2014 \u6620\u5c04\u5230 \\\\file-server\\Shared Folder\uff08\u811a\u672c\u540c\u65f6\u652f\u6301 UNC \u8def\u5f84\uff0cZ: \u672a\u6302\u8f7d\u65f6\u81ea\u52a8\u56de\u9000\uff09",
]:
    bullet(item)

h2("\u90e8\u7f72\u6b65\u9aa4")
for step in [
    "\u5c06\u6574\u4e2a TaxAutomationReminderSystem\\ \u6587\u4ef6\u5939\u590d\u5236\u5230\u76ee\u6807\u7535\u8111\uff08\u6216\u4fdd\u7559\u5728\u5171\u4eab\u7f51\u7edc\u9a71\u52a8\u5668\u4e0a\u5171\u7528\uff09",
    "\u5b89\u88c5 Python 3.8+\uff08\u52fe\u9009 Add Python to PATH\uff09",
    "\u53cc\u51fb run_reminder.bat \u2014 \u81ea\u52a8\u68c0\u6d4b\u5e76\u5b89\u88c5\u7f3a\u5c11\u7684\u5305\uff0c\u7136\u540e\u542f\u52a8\u7cfb\u7edf",
    "\u9996\u6b21\u8fd0\u884c\u65f6\u9009 [1] Update Tracker\uff0c\u786e\u8ba4\u4e3b\u8868\u53ef\u6b63\u5e38\u8bfb\u53d6",
]:
    bullet(step)
tip("SMTP \u5bc6\u7801\u6bcf\u6b21\u8fd0\u884c\u65f6\u624b\u52a8\u8f93\u5165\uff0c\u4e0d\u5b58\u50a8\u5728\u4efb\u4f55\u6587\u4ef6\u4e2d\uff0c\u65e0\u9700\u989d\u5916\u914d\u7f6e\u3002")
doc.add_paragraph()

# ════════════════════════════════════════════════════════
# 10. 常见问题
# ════════════════════════════════════════════════════════
h1("10.  \u5e38\u89c1\u95ee\u9898\u4e0e\u89e3\u51b3\u65b9\u6cd5")
faqs = [
    ("Q1\uff1a\u53cc\u51fb BAT \u6587\u4ef6\u65f6\u51fa\u73b0\u9519\u8bef\u63d0\u793a",
     ["\u5e38\u89c1\u539f\u56e0\uff1aPython \u672a\u5b89\u88c5\uff0c\u6216\u5b89\u88c5\u65f6\u672a\u52fe\u9009 \"Add Python to PATH\"",
      "\u89e3\u51b3\uff1a\u91cd\u65b0\u5b89\u88c5 Python\uff0c\u5b89\u88c5\u754c\u9762\u52fe\u9009 \"Add Python to PATH\"\uff0c\u7136\u540e\u91cd\u65b0\u8fd0\u884c"]),
    ("Q2\uff1a\u8f93\u5165\u4e3b\u8868\u5bc6\u7801\u540e\u63d0\u793a\u89e3\u5bc6\u5931\u8d25",
     ["\u5bc6\u7801\u8f93\u5165\u6709\u8bef\uff0c\u6216\u4e3b\u8868\u6587\u4ef6\u5df2\u88ab\u66ff\u6362\uff08\u6587\u4ef6\u540d\u53d8\u66f4\uff09",
      "\u89e3\u51b3\uff1a\u786e\u8ba4\u5bc6\u7801\u6b63\u786e\uff1b\u82e5\u6587\u4ef6\u540d\u5df2\u66f4\u65b0\uff0c\u8bf7\u8054\u7cfb\u6280\u672f\u4eba\u5458\u66f4\u65b0\u811a\u672c\u4e2d\u7684\u8def\u5f84"]),
    ("Q3\uff1a\u67d0\u7c7b\u5ba2\u6237\u6570\u91cf\u663e\u793a\u4e3a 0",
     ["\u8be5\u7c7b\u578b\u5ba2\u6237\u5c1a\u672a\u5f55\u5165\u8ffd\u8e2a\u8868",
      "\u89e3\u51b3\uff1a\u5148\u8fd0\u884c [1] Update Tracker\uff0c\u4ece\u4e3b\u8868\u66f4\u65b0\u540e\u518d\u53d1\u9001"]),
    ("Q4\uff1a\u90ae\u4ef6\u53d1\u9001\u5931\u8d25\uff08SMTP ERROR\uff09",
     ["SMTP \u5bc6\u7801\u6709\u8bef\uff0c\u6216\u7f51\u7edc\u9632\u706b\u5899\u963b\u6b62\u4e86\u7aef\u53e3 465",
      "\u89e3\u51b3\uff1a\u786e\u8ba4\u5bc6\u7801\uff1b\u82e5\u7f51\u7edc\u53d7\u9650\uff0c\u8054\u7cfb IT \u5f00\u653e\u51fa\u7ad9\u7aef\u53e3 465"]),
    ("Q5\uff1a\u4fdd\u5b58\u8ffd\u8e2a\u8868\u65f6\u63d0\u793a Permission denied\uff08\u6743\u9650\u9519\u8bef\uff09",
     ["TaxReminder_Tracker.xlsx \u5728 Excel \u4e2d\u5904\u4e8e\u6253\u5f00\u72b6\u6001",
      "\u89e3\u51b3\uff1a\u5173\u95ed Excel \u4e2d\u8be5\u6587\u4ef6\uff0c\u56de\u5230\u7a0b\u5e8f\u6309\u63d0\u793a\u6309 Enter \u91cd\u8bd5"]),
    ("Q6\uff1a\u90ae\u4ef6\u6b63\u6587\u663e\u793a {NAME} \u6216 {MONTH}\uff08\u672a\u88ab\u66ff\u6362\uff09",
     ["\u6a21\u677f\u6587\u4ef6\u4e2d\u5bf9\u5e94\u5360\u4f4d\u7b26\u7684\u62fc\u5199\u6709\u8bef\uff08\u5927\u5c0f\u5199\u3001\u82b1\u62ec\u53f7\u987b\u5b8c\u5168\u5339\u914d\uff09",
      "\u89e3\u51b3\uff1a\u7528 Word \u6253\u5f00\u5bf9\u5e94 .docx \u6a21\u677f\uff0c\u5c06\u5360\u4f4d\u7b26\u6539\u4e3a {NAME}\u3001{COMPANY}\u3001{MONTH}"]),
    ("Q7\uff1aPST \u5b63\u5ea6\u63d0\u9192\u83dc\u5355 [6] \u63d0\u793a\u201c\u975e\u5b63\u5ea6\u6708\u201d",
     ["\u5f53\u524d\u6708\u4efd\u4e0d\u662f 1 / 4 / 7 / 10 \u6708\uff0c\u5b63\u5ea6\u63d0\u9192\u672c\u6708\u4e0d\u9700\u8981\u53d1\u9001\uff0c\u8fd9\u662f\u6b63\u5e38\u884c\u4e3a",
      "\u65e0\u9700\u5904\u7406\uff1b\u7b49\u5230\u5b63\u5ea6\u6708\u518d\u8fd0\u884c\u5373\u53ef"]),
    ("Q8\uff1a\u5e94\u6536\u5230\u63d0\u9192\u7684\u5ba2\u6237\u672a\u51fa\u73b0\u5728\u540d\u5355\u4e2d",
     ["\u5ba2\u6237\u4e3b\u8868\u4e2d\u8be5\u5ba2\u6237\u7684 Language / Frequency \u5b57\u6bb5\u4e3a\u7a7a\u6216\u62fc\u5199\u6709\u8bef",
      "\u89e3\u51b3\uff1a\u68c0\u67e5\u4e3b\u8868\u5bf9\u5e94\u5217\uff08Language \u5e94\u4e3a ENG / CHN / VIE\uff0cFrequency \u5e94\u4e3a Monthly / Quarterly\uff09"]),
]

for q, answers in faqs:
    h2(q)
    for ans in answers:
        bullet(ans)

doc.add_paragraph()

# ════════════════════════════════════════════════════════
# 11. 技术支持
# ════════════════════════════════════════════════════════
h1("11.  \u9700\u8981\u6280\u672f\u652f\u6301\uff1f")
body("\u5982\u9047\u4ee5\u4e0b\u60c5\u51b5\uff0c\u8bf7\u8054\u7cfb\u8d1f\u8d23\u7ef4\u62a4\u8be5\u811a\u672c\u7684\u6280\u672f\u4eba\u5458\uff1a")
for item in [
    "\u4e3b\u8868\u6587\u4ef6\u540d\u6216\u8def\u5f84\u53d1\u751f\u53d8\u66f4",
    "SMTP \u90ae\u7b31\u5bc6\u7801\u5df2\u66f4\u6539\uff0c\u9700\u8981\u66f4\u65b0\u811a\u672c\u914d\u7f6e",
    "\u9700\u8981\u6dfb\u52a0\u65b0\u7684\u63d0\u9192\u7c7b\u578b\u6216\u4fee\u6539\u7b5b\u9009\u6761\u4ef6",
    "\u9700\u8981\u6dfb\u52a0\u6216\u66f4\u65b0\u4e2d\u6587\uff08CHN\uff09/ \u8d8a\u5357\u8bed\uff08VIE\uff09\u90ae\u4ef6\u6a21\u677f",
    "\u7a0b\u5e8f\u8fd0\u884c\u65f6\u51fa\u73b0\u672c\u6587\u6863\u672a\u5217\u51fa\u7684\u9519\u8bef",
]:
    bullet(item)

out = r"Z:\Scripts\TaxAutomationReminderSystem\税务提醒系统_使用说明.docx"
doc.save(out)
print("Saved:", out)
