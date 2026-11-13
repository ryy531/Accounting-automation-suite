# -*- coding: utf-8 -*-
"""
Annual Return Management Tool
ExampleCo Group / Example CPA Firm P.C. Inc.

Modules:
  1. Decrypt master -> generate/update tracker
  2. Create monthly work folders
  3. Send Reminder emails (30-60 days before due)
  4. Send Confirmation + PDF receipt emails
"""

import os, sys, io, shutil, logging, glob
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime, timedelta
from pathlib import Path

_SCRIPTS_ROOT = Path(__file__).resolve().parents[1]
_LIB_ROOT = _SCRIPTS_ROOT / "_lib"
if str(_LIB_ROOT) not in sys.path:
    sys.path.insert(0, str(_LIB_ROOT))

import config.settings as shared_settings
from connectors.email_client import EmailClient

try:
    import msoffcrypto
except ImportError:
    print("[ERROR] Missing msoffcrypto. Run: pip install msoffcrypto-tool"); sys.exit(1)
try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
except ImportError:
    print("[ERROR] Missing openpyxl. Run: pip install openpyxl"); sys.exit(1)
try:
    from docx import Document
except ImportError:
    print("[ERROR] Missing python-docx. Run: pip install python-docx"); sys.exit(1)

# ============================================================
# CONFIG
# ============================================================

# ---- 双路径配置：脚本自动检测哪个可用 ----
# Path A: UNC 路径（从服务器直接访问）
# Path B: 映射盘符（本机 Z: 映射）
PATH_OPTIONS = {
    "master_paths": [
        r"\\file-server\Shared Folder\4. General - Scan-Notes-Form-PPT\4. Accounting client list\#Business Master Client List.xlsx",
        r"Z:\4. General - Scan-Notes-Form-PPT\4. Accounting client list\#Business Master Client List.xlsx",
    ],
    "output_dirs": [
        r"\\file-server\Shared Folder\Scripts\AnnualReturn",
        r"Z:\Scripts\AnnualReturn",
    ],
    "monthly_bases": [
        r"\\file-server\Shared Folder\4. General - Scan-Notes-Form-PPT\15. Notes\1. AI\Annual Return",
        r"Z:\4. General - Scan-Notes-Form-PPT\15. Notes\1. AI\Annual Return",
    ],
    "templet_dirs": [
        r"\\file-server\Shared Folder\Scripts\AnnualReturn\Templet",
        r"Z:\Scripts\AnnualReturn\Templet",
    ],
}


def resolve_paths():
    """自动检测可用路径（UNC 或 Z: 盘符）"""
    master_path = None
    for p in PATH_OPTIONS["master_paths"]:
        if os.path.exists(p):
            master_path = p
            break

    output_dir = None
    for p in PATH_OPTIONS["output_dirs"]:
        parent = os.path.dirname(p)
        if os.path.exists(parent):
            output_dir = p
            break

    monthly_base = None
    for p in PATH_OPTIONS["monthly_bases"]:
        parent = os.path.dirname(p)
        if os.path.exists(parent):
            monthly_base = p
            break

    templet_dir = None
    for p in PATH_OPTIONS["templet_dirs"]:
        if os.path.exists(p):
            templet_dir = p
            break

    return master_path, output_dir, monthly_base, templet_dir


CONFIG = {
    # 路径在运行时由 resolve_paths() 填充
    "master_path": "",
    "master_sheet": "Master",
    "output_dir": "",
    "output_filename": "AnnualReturn_Tracker.xlsx",
    "monthly_base": "",
    "monthly_subfolders": ["1. Intake", "2. Filling Confirmation", "3. Invoice and Payment"],
    "smtp_user": shared_settings.EMAIL_ADDRESS or "infosk@example-firm.com",
    "sender_name": "ExampleCo Group",
    "payment_url": "https://www.cognitoforms.com/ExampleFirmLLP/AnnualReturnPayment",
    "info_form_url": "https://example-firm.com/annual-return/",
    "column_map": {
        "No.": 1, "Legal Name": 2, "Entity Number 2": 4,
        "Operating Name (previous Name)": 5, "Owner Name": 6,
        "Owner Email": 7, "Language": 8, "Assigned": 10,
        "Incorporate Date": 19, "Annual Return": 20, "B/B+ Charge": 15,
    },
    "output_columns": [
        "No.", "Legal Name", "Entity Number 2",
        "Operating Name (previous Name)", "Province",
        "Annual Return", "Incorporate Date",
        "Calculated Due Date",
        "Owner Name", "Owner Email",
        "Language", "Assigned",
        "B/B+ Charge",
        "Service Fee", "Tax", "Gov Fee", "Total Fee",
        "Status", "ReminderSentDate",
        "FiledDate", "ConfirmationSentDate", "BCAccessSentDate", "Notes",
    ],
    "unique_keys": ["Legal Name", "Entity Number 2"],
    "master_sync_fields": [
        "No.", "Legal Name", "Entity Number 2",
        "Operating Name (previous Name)", "Owner Name", "Owner Email",
        "Language", "Assigned", "Incorporate Date", "Annual Return", "B/B+ Charge",
    ],
    "protected_fields": ["Province", "Status", "ReminderSentDate", "FiledDate", "ConfirmationSentDate", "BCAccessSentDate", "Notes",
                         "Calculated Due Date", "Service Fee", "Tax", "Gov Fee", "Total Fee"],
    "sheet_filter": {"Alex": "Alex's", "MK": "MK"},
    "default_status": "Pending",
    # Template paths (filled at runtime by resolve_paths)
    "tpl_reminder_cn": "",   # Annual Return Reminder - CN.docx
    "tpl_reminder_en": "",   # Annual Return Reminder - EN.docx
}

MONTH_MAP = {
    "january":1,"february":2,"march":3,"april":4,"may":5,"june":6,
    "july":7,"august":8,"september":9,"october":10,"november":11,"december":12,
    "jan":1,"feb":2,"mar":3,"apr":4,"jun":6,"jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12,
}

# ============================================================
# PROVINCE RULES: Due Date, Tax, Gov Fee
# ============================================================
PROVINCE_RULES = {
    "Federal": {
        "due_rule": "anniversary+60days",       # 周年日 + 60 天
        "gov_fee": 12.00,
        "tax_type": "GST",                       # GST only (5%)
        "gst_rate": 0.05,
        "pst_rate": 0.00,
    },
    "BC": {
        "due_rule": "anniversary+2months",       # 周年日 + 2 个月
        "gov_fee": 43.39,
        "tax_type": "GST+PST",
        "gst_rate": 0.05,
        "pst_rate": 0.07,                        # BC PST 7%
    },
    "AB": {
        "due_rule": "anniversary_month",         # 周年月到期
        "gov_fee": 135.00,                        # 政府费 $50 + Agent 费 ~$85
        "tax_type": "GST",                       # AB 无 PST
        "gst_rate": 0.05,
        "pst_rate": 0.00,
    },
    "SK": {
        "due_rule": "anniversary+1month_end",    # 周年日 + 1 个月（月末）
        "gov_fee": 60.00,                        # SK Annual Return (on time); late = $110
        "tax_type": "GST+PST",
        "gst_rate": 0.05,
        "pst_rate": 0.06,                        # SK PST 6%
    },
    "ON": {
        "due_rule": "fye+6months",               # FYE + 6 个月
        "gov_fee": 0.00,                         # ON 无政府费
        "tax_type": "HST",                       # ON HST 13%
        "gst_rate": 0.13,                        # HST combined
        "pst_rate": 0.00,                        # included in HST
    },
}

SERVICE_FEE = 150.00  # 事务所标准服务费

# ============================================================
# LOGGING
# ============================================================
def setup_logging(output_dir):
    log_dir = os.path.join(output_dir, "Logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"run_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.log")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8"), logging.StreamHandler(sys.stdout)])
    return log_file

# ============================================================
# MONTHLY FOLDERS
# ============================================================
def create_monthly_folders(config):
    now = datetime.now()
    month_dir = os.path.join(config["monthly_base"], str(now.year), f"{now.year}.{now.month:02d}")
    created = False
    for sub in config["monthly_subfolders"]:
        p = os.path.join(month_dir, sub)
        if not os.path.exists(p):
            os.makedirs(p, exist_ok=True); created = True
    if created:
        logging.info(f"Created: {month_dir}"); print(f"  [Folders] Created: {month_dir}")
    else:
        logging.info(f"Exists: {month_dir}")
    return month_dir

# ============================================================
# DECRYPT
# ============================================================
def decrypt_to_tempfile(filepath, password):
    import shutil
    import tempfile
    logging.info(f"Decrypting: {filepath}")
    print("  [Decrypt] Large file, please wait (1-2 min)...")
    with open(filepath, "rb") as f:
        of = msoffcrypto.OfficeFile(f)
        tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
        if of.is_encrypted():
            of.load_key(password=password)
            of.decrypt(tmp); tmp.close()
        else:
            logging.info("File is not password-protected; copying as-is.")
            print("  [Decrypt] File is not password-protected; skipping decryption.")
            tmp.close()
            shutil.copyfile(filepath, tmp.name)
    print("  [Decrypt] Done"); return tmp.name

# ============================================================
# READ MASTER (read_only mode)
# ============================================================
def extract_master_data(decrypted_path, config):
    TARGET_SHEETS = ["master", "Alex's", "mk"]
    print(f"  [Read] Loading sheets: {', '.join(s.title() for s in TARGET_SHEETS)}...")
    wb = openpyxl.load_workbook(decrypted_path, read_only=True, data_only=True)
    logging.info(f"Sheets available: {wb.sheetnames}")
    col_map = config["column_map"]; seen_keys = set(); records = []
    for sheet_name in wb.sheetnames:
        if sheet_name.strip().lower() not in TARGET_SHEETS:
            continue
        ws = wb[sheet_name]; sheet_new = 0
        for row in ws.iter_rows(min_row=2, values_only=False):
            c = row[col_map["Legal Name"]-1] if len(row) >= col_map["Legal Name"] else None
            v = c.value if c else None
            if not v or str(v).strip() == "": continue
            rec = {}
            for field, cn in col_map.items():
                rec[field] = row[cn-1].value if cn <= len(row) else None
            key = make_key(rec, config["unique_keys"])
            if key and key != "||" and key not in seen_keys:
                seen_keys.add(key); records.append(rec); sheet_new += 1
        logging.info(f"Sheet '{sheet_name}': {sheet_new} new records")
        print(f"  [Read] Sheet '{sheet_name}': {sheet_new} new records")
    wb.close(); print(f"  [Read] Done: {len(records)} total records"); return records

# ============================================================
# TRACKER I/O
# ============================================================
def make_key(rec, keys):
    return "||".join(str(rec.get(k,"") or "").strip().upper() for k in keys)

def load_existing_tracker(filepath):
    if not os.path.exists(filepath): return {}
    wb = openpyxl.load_workbook(filepath)
    if "master" not in wb.sheetnames: return {}
    ws = wb["master"]
    headers = [ws.cell(row=1, column=c).value for c in range(1, ws.max_column+1)]
    existing = {}
    for ri in range(2, ws.max_row+1):
        rd = {}
        for ci, h in enumerate(headers, 1):
            if h: rd[h] = ws.cell(row=ri, column=ci).value
        key = make_key(rd, CONFIG["unique_keys"])
        if key and key != "||": existing[key] = rd
    logging.info(f"Existing: {len(existing)} records"); return existing

# ============================================================
# STYLES & WRITE
# ============================================================
HF = Font(name="Arial", bold=True, size=11, color="FFFFFF")
HFL = PatternFill("solid", fgColor="2F5496")
HA = Alignment(horizontal="center", vertical="center", wrap_text=True)
CF = Font(name="Arial", size=10)
CA = Alignment(vertical="center", wrap_text=True)
BD = Border(left=Side(style="thin"), right=Side(style="thin"), top=Side(style="thin"), bottom=Side(style="thin"))
CW = {"No.":6,"Legal Name":30,"Entity Number 2":18,"Operating Name (previous Name)":28,
      "Province":12,"Annual Return":15,"Incorporate Date":22,"Calculated Due Date":20,
      "Owner Name":18,"Owner Email":28,"Language":10,
      "Assigned":12,"B/B+ Charge":16,
      "Service Fee":14,"Tax":12,"Gov Fee":12,"Total Fee":14,
      "Status":14,"ReminderSentDate":18,"FiledDate":14,"ConfirmationSentDate":20,"Notes":25}

def write_sheet(ws, cols, recs):
    from openpyxl.worksheet.datavalidation import DataValidation
    for ci, cn in enumerate(cols, 1):
        c = ws.cell(row=1, column=ci, value=cn)
        c.font=HF; c.fill=HFL; c.alignment=HA; c.border=BD
        ws.column_dimensions[openpyxl.utils.get_column_letter(ci)].width = CW.get(cn, 15)
    ws.auto_filter.ref = f"A1:{openpyxl.utils.get_column_letter(len(cols))}1"
    ws.freeze_panes = "A2"
    for ri, rec in enumerate(recs, 2):
        for ci, cn in enumerate(cols, 1):
            c = ws.cell(row=ri, column=ci, value=rec.get(cn, ""))
            c.font=CF; c.alignment=CA; c.border=BD
    # Status 下拉菜单
    if "Status" in cols:
        status_col = openpyxl.utils.get_column_letter(cols.index("Status") + 1)
        dv = DataValidation(
            type="list",
            formula1='"Pending,Reminded,Filed,Confirmed"',
            allow_blank=True, showDropDown=False,
        )
        dv.sqref = f"{status_col}2:{status_col}5000"
        ws.add_data_validation(dv)

def save_tracker(final_records, output_path, columns):
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "master"
    write_sheet(ws, columns, final_records)
    for sn, fv in CONFIG["sheet_filter"].items():
        fl = [r for r in final_records if str(r.get("Assigned","") or "").strip().upper() == fv.upper()]
        write_sheet(wb.create_sheet(sn), columns, fl)
        logging.info(f"{sn}: {len(fl)} records")
    wb.save(output_path); logging.info(f"Saved: {output_path}")

# ============================================================
# DATE UTILS (Province-Aware)
# ============================================================
def parse_due_month(val):
    if val is None: return None
    v = str(val).strip().lower()
    if v in MONTH_MAP: return MONTH_MAP[v]
    try:
        m = int(v); return m if 1 <= m <= 12 else None
    except ValueError: return None

def _add_months(dt, months):
    """Add months to a date, clamping day to valid range."""
    import calendar
    m = dt.month - 1 + months
    y = dt.year + m // 12
    m = m % 12 + 1
    d = min(dt.day, calendar.monthrange(y, m)[1])
    return datetime(y, m, d)

def _end_of_month(y, m):
    """Return last day of given month."""
    import calendar
    return datetime(y, m, calendar.monthrange(y, m)[1])

def get_anniversary_date(rec, year=None):
    """Get the anniversary date (incorporation date projected to given year)."""
    if year is None: year = datetime.now().year
    inc = rec.get("Incorporate Date")
    ar_month = parse_due_month(rec.get("Annual Return"))
    if inc is None and ar_month is None:
        return None
    if inc and isinstance(inc, datetime):
        day = inc.day; month = inc.month
    elif inc and isinstance(inc, str):
        try:
            d = datetime.strptime(inc.strip().split(" ")[0], "%Y-%m-%d")
            day = d.day; month = d.month
        except:
            day = 1; month = ar_month or 1
    else:
        day = 1; month = ar_month or 1
    if ar_month: month = ar_month
    import calendar
    day = min(day, calendar.monthrange(year, month)[1])
    try: return datetime(year, month, day)
    except: return datetime(year, month, 1)

def calculate_due_date(rec, year=None):
    """Calculate due date based on province rules."""
    prov = str(rec.get("Province", "") or "").strip().upper()
    # Normalize province names
    prov_map = {"FEDERAL":"Federal","BC":"BC","AB":"AB","SK":"SK","ON":"ON",
                "BRITISH COLUMBIA":"BC","ALBERTA":"AB","SASKATCHEWAN":"SK","ONTARIO":"ON"}
    prov_key = prov_map.get(prov, "")
    if prov_key not in PROVINCE_RULES:
        # Fallback: use old logic (anniversary + 60 days)
        return get_due_date_legacy(rec, year)

    rule = PROVINCE_RULES[prov_key]["due_rule"]
    anniv = get_anniversary_date(rec, year)
    if anniv is None: return None

    if rule == "anniversary+60days":
        return anniv + timedelta(days=60)
    elif rule == "anniversary+2months":
        return _add_months(anniv, 2)
    elif rule == "anniversary_month":
        # AB: due by end of anniversary month
        return _end_of_month(anniv.year, anniv.month)
    elif rule == "anniversary+1month_end":
        # SK: due end of month after anniversary
        next_m = _add_months(anniv, 1)
        return _end_of_month(next_m.year, next_m.month)
    elif rule == "fye+6months":
        # ON: FYE + 6 months (use Annual Return month as FYE proxy)
        return _add_months(anniv, 6)
    else:
        return anniv + timedelta(days=60)

def get_due_date_legacy(rec, year=None):
    """Legacy fallback when Province is not set."""
    month = parse_due_month(rec.get("Annual Return"))
    if month is None: return None
    if year is None: year = datetime.now().year
    inc = rec.get("Incorporate Date"); day = 1
    if inc:
        try: day = inc.day if isinstance(inc, datetime) else int(str(inc).split("-")[-1].split("/")[-1])
        except: day = 1
    try: return datetime(year, month, min(day, 28))
    except: return datetime(year, month, 1)

def get_due_date(rec, year=None):
    """Main entry: use province-aware if Province is set, else legacy."""
    prov = str(rec.get("Province", "") or "").strip()
    if prov:
        return calculate_due_date(rec, year)
    return get_due_date_legacy(rec, year)

# ============================================================
# FEE CALCULATOR
# ============================================================
def calculate_fees(rec):
    """Calculate Service Fee, Tax, Gov Fee, Total Fee based on Province."""
    prov = str(rec.get("Province", "") or "").strip().upper()
    prov_map = {"FEDERAL":"Federal","BC":"BC","AB":"AB","SK":"SK","ON":"ON",
                "BRITISH COLUMBIA":"BC","ALBERTA":"AB","SASKATCHEWAN":"SK","ONTARIO":"ON"}
    prov_key = prov_map.get(prov, "")

    if prov_key not in PROVINCE_RULES:
        return None, None, None, None

    rules = PROVINCE_RULES[prov_key]
    service_fee = SERVICE_FEE

    # B/B+ extra charge (taxable service charge, included in tax base)
    try:
        bb_charge = float(rec.get("B/B+ Charge") or 0)
    except (ValueError, TypeError):
        bb_charge = 0.0

    gov_fee = rules["gov_fee"]
    taxable = service_fee + bb_charge

    if rules["tax_type"] == "HST":
        tax = round(taxable * rules["gst_rate"], 2)
    else:
        gst = round(taxable * rules["gst_rate"], 2)
        pst = round(taxable * rules["pst_rate"], 2)
        tax = round(gst + pst, 2)

    total = round(taxable + tax + gov_fee, 2)
    return service_fee, tax, gov_fee, total

def apply_calculations(rec):
    """Apply Due Date and Fee calculations to a record (only if Province is set)."""
    prov = str(rec.get("Province", "") or "").strip()
    if not prov:
        return  # No province set, skip

    # Calculate due date
    due = calculate_due_date(rec)
    if due:
        rec["Calculated Due Date"] = due.strftime("%Y-%m-%d")

    # Calculate fees
    sf, tax, gf, total = calculate_fees(rec)
    if sf is not None:
        rec["Service Fee"] = sf
        rec["Tax"] = tax
        rec["Gov Fee"] = gf
        rec["Total Fee"] = total

# ============================================================
# DOCX → HTML  (template engine)
# ============================================================
_NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

_HL_COLORS = {
    1: "#000000", 2: "#0000FF", 3: "#00FFFF",  4: "#00FF00",
    5: "#FF00FF", 6: "#FF0000", 7: "#FFFF00",  8: "#FFFFFF",
    9: "#00008B", 10: "#008B8B", 11: "#006400", 12: "#8B008B",
    13: "#8B0000", 14: "#808000", 15: "#A9A9A9", 16: "#D3D3D3",
}

def _doc_images(doc):
    imgs = {}
    for rel in doc.part.rels.values():
        if "image" in rel.reltype:
            part = rel.target_part
            imgs[rel.rId] = (part.content_type, part.blob)
    return imgs

def _render_run(run, imgs, replacements):
    blips = run._r.findall(f".//{{{_NS_A}}}blip")
    if blips:
        out = []
        for blip in blips:
            rid = blip.get(f"{{{_NS_R}}}embed")
            if rid and rid in imgs:
                out.append(f'<img src="cid:{rid}" style="max-width:100%;vertical-align:middle;">')
        if out:
            return "".join(out)
    text = run.text
    if not text:
        return ""
    if replacements:
        for k, v in replacements.items():
            text = text.replace(k, str(v))
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    css = []
    try:
        c = run.font.color.rgb
        if c: css.append(f"color:#{c};")
    except Exception: pass
    try:
        hl = run.font.highlight_color
        if hl is not None:
            bg = _HL_COLORS.get(int(hl))
            if bg: css.append(f"background-color:{bg};")
    except Exception: pass
    try:
        sz = run.font.size
        if sz: css.append(f"font-size:{sz / 12700:.0f}pt;")
    except Exception: pass
    if css: text = f'<span style="{"".join(css)}">{text}</span>'
    if run.bold:      text = f"<b>{text}</b>"
    if run.italic:    text = f"<i>{text}</i>"
    if run.underline: text = f"<u>{text}</u>"
    return text

def _para_style(para):
    try:
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        _map = {WD_ALIGN_PARAGRAPH.CENTER: "center", WD_ALIGN_PARAGRAPH.RIGHT: "right",
                WD_ALIGN_PARAGRAPH.JUSTIFY: "justify"}
        a = _map.get(para.alignment)
        return f"text-align:{a};" if a else ""
    except Exception: return ""

def _render_para(para, imgs, replacements):
    inner = "".join(_render_run(r, imgs, replacements) for r in para.runs)
    if not inner.strip(): return "<br>"
    style = _para_style(para)
    attr  = f' style="{style}"' if style else ""
    return f"<p{attr}>{inner}</p>"

def _render_table(table, imgs, replacements):
    rows = []
    for row in table.rows:
        cells = []
        for cell in row.cells:
            inner = "".join(_render_para(p, imgs, replacements) for p in cell.paragraphs)
            cells.append(f'<td style="border:1px solid #ccc;padding:4px 8px;vertical-align:top;">{inner}</td>')
        rows.append(f"<tr>{''.join(cells)}</tr>")
    return '<table style="border-collapse:collapse;width:100%;margin:8px 0;">' + "".join(rows) + "</table>"

def parse_template(docx_path, replacements=None):
    """Parse a .docx template into (subject, html_body, images).
    First paragraph starting with 'Subject:' is extracted as subject and excluded from body.
    Supports font colour, bold/italic/underline, inline images (CID), tables."""
    from docx.oxml.ns import qn
    from docx.text.paragraph import Paragraph as DocxParagraph
    from docx.table import Table as DocxTable

    doc  = Document(docx_path)
    imgs = _doc_images(doc)

    subject = None; subject_elem = None
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text: continue
        if text.lower().startswith("subject:"):
            subject = text[8:].strip()
            if replacements and subject:
                for k, v in replacements.items():
                    subject = subject.replace(k, str(v))
            subject_elem = para._element
        break

    parts = ['<div style="font-family:Arial;font-size:14px;color:#333;line-height:1.6;">']
    past_subject = (subject_elem is None)
    for child in doc.element.body:
        if child is subject_elem:
            past_subject = True; continue
        if not past_subject: continue
        tag = child.tag
        if tag == qn("w:p"):
            parts.append(_render_para(DocxParagraph(child, doc), imgs, replacements))
        elif tag == qn("w:tbl"):
            parts.append(_render_table(DocxTable(child, doc), imgs, replacements))
    parts.append("</div>")
    return subject, "\n".join(parts), imgs

def load_reminder_template(rec):
    """Return (subject, html_body, images) from .docx template, or (None, None, {}) if not found."""
    lang = str(rec.get("Language", "") or "").strip().upper()
    tpl_key = "tpl_reminder_cn" if lang == "CN" else "tpl_reminder_en"
    tpl_path = CONFIG.get(tpl_key, "")
    if not tpl_path or not os.path.exists(tpl_path):
        return None, None, {}

    due_raw = rec.get("Calculated Due Date", "") or rec.get("Annual Return", "")
    due_en = due_raw; due_cn = due_raw
    try:
        due_dt = datetime.strptime(str(due_raw), "%Y-%m-%d")
        due_en = due_dt.strftime("%B %d, %Y")
        due_cn = due_raw
    except Exception: pass

    prov     = str(rec.get("Province", "") or "")
    registry = REGISTRY_NAME.get(prov, f"{prov} Corporate Registry")

    replacements = {
        "{NAME}":     str(rec.get("Owner Name",     "") or ""),
        "{COMPANY}":  str(rec.get("Legal Name",     "") or ""),
        "{CORP_NO}":  str(rec.get("Entity Number 2","") or ""),
        "{DUE_DATE}": due_en if lang != "CN" else due_cn,
        "{PROVINCE}": prov,
        "{REGISTRY}": registry,
        "{INFO_URL}": CONFIG.get("info_form_url", ""),
    }
    try:
        subj, body, imgs = parse_template(tpl_path, replacements)
        logging.info(f"Template loaded: {os.path.basename(tpl_path)}")
        return subj, body, imgs
    except Exception as e:
        logging.warning(f"Template load failed ({tpl_path}): {e}")
        return None, None, {}

# ============================================================
# EMAIL ENGINE
# ============================================================
def connect_smtp(password=None):
    client = EmailClient.from_settings(CONFIG["sender_name"], password=password)
    client.connect_imap()
    logging.info("Email client connected (%s)", shared_settings.EMAIL_AUTH_MODE)
    return client

def send_email(conn, to, subj, html, attachments=None, images=None):
    """Send an HTML email with optional file attachments and/or CID inline images.
    images: {rId: (mime_type, bytes)} — produced by parse_template."""
    from email.mime.image import MIMEImage

    # Inner: related (HTML + inline images)
    related = MIMEMultipart("related")
    related.attach(MIMEText(html, "html", "utf-8"))
    if images:
        for cid, (mime_type, blob) in images.items():
            subtype  = mime_type.split("/")[-1]
            img_part = MIMEImage(blob, _subtype=subtype)
            img_part.add_header("Content-ID", f"<{cid}>")
            img_part.add_header("Content-Disposition", "inline")
            related.attach(img_part)

    # Outer: mixed (related + file attachments)
    msg = MIMEMultipart("mixed")
    msg["From"] = f"{CONFIG['sender_name']} <{CONFIG['smtp_user']}>"
    msg["To"] = to; msg["Subject"] = subj
    msg.attach(related)
    for fp in (attachments or []):
        if os.path.exists(fp):
            with open(fp, "rb") as f:
                p = MIMEBase("application", "octet-stream"); p.set_payload(f.read())
            encoders.encode_base64(p)
            p.add_header("Content-Disposition", f'attachment; filename="{os.path.basename(fp)}"')
            msg.attach(p)
    raw = msg.as_bytes()
    conn.send_raw(raw, to)
    logging.info(f"Sent: {to} | {subj}")

# ============================================================
# EMAIL TEMPLATES
# Source: Annual_Return_Management.xlsx, "Email Templates" sheet
# ============================================================
REGISTRY_NAME = {
    "BC": "BC Registry",
    "AB": "Alberta Corporate Registry",
    "SK": "Saskatchewan Corporate Registry",
    "Federal": "Corporations Canada",
    "ON": "Ontario Business Registry",
}

BANNER_CN = """<div style="background:#FFF3CD;border-left:4px solid #FFC107;padding:10px 15px;margin-bottom:18px;font-size:13px;border-radius:3px;">
⚠️ <b>这是一封系统自动发送的提醒邮件。如您已完成相关事项，请忽略此邮件。</b>
</div>"""

BANNER_EN = """<div style="background:#FFF3CD;border-left:4px solid #FFC107;padding:10px 15px;margin-bottom:18px;font-size:13px;border-radius:3px;">
⚠️ <b>This is an automated reminder. If you have already completed the required action, please disregard this email.</b>
</div>"""

def build_reminder(rec):
    """Return (subject, html_body, images) for a reminder email.
    Tries .docx template first; falls back to hardcoded HTML."""
    # Try .docx template
    tpl_subj, tpl_body, images = load_reminder_template(rec)
    if tpl_body:
        lang = str(rec.get("Language","") or "").strip().upper()
        co   = rec.get("Legal Name","")
        if not tpl_subj:
            tpl_subj = f"年度公司注册信息申报提醒 – {co}" if lang == "CN" else f"Annual Return Filing Reminder – {co}"
        return tpl_subj, tpl_body, images

    # Fallback: hardcoded HTML
    lang  = str(rec.get("Language","") or "").strip().upper()
    co    = rec.get("Legal Name","")
    name  = rec.get("Owner Name","")
    corp  = rec.get("Entity Number 2","")
    prov  = str(rec.get("Province","") or "")
    registry = REGISTRY_NAME.get(prov, f"{prov} Corporate Registry")
    info_url  = CONFIG.get("info_form_url", "")

    # Format due date
    due_raw = rec.get("Calculated Due Date","") or rec.get("Annual Return","")
    due_en = due_raw; due_cn = due_raw
    try:
        due_dt = datetime.strptime(str(due_raw), "%Y-%m-%d")
        due_en = due_dt.strftime("%B %d, %Y")   # e.g. "April 15, 2026"
    except Exception: pass

    if lang == "CN":
        subj = f"年度公司注册信息申报提醒 – {co}"
        body = f"""<div style="font-family:Arial;font-size:14px;color:#333;">{BANNER_CN}
<p>尊敬的 {name}，</p>
<p>提醒您：<b>{co}</b>（公司编号：{corp}）的 Annual Return（公司注册信息年度确认）将于 <b>{due_cn}</b> 到期。</p>
<p>Annual Return 是向 {registry} 提交的年度合规申报，并非税务申报，但未按时提交可能导致公司被注销或吊销。</p>
<p>请填写以下信息确认表，我们将代为提交：</p>
<p><a href="{info_url}">{info_url}</a></p>
<br><p>谢谢！<br><b>ExampleCo Group</b><br>Example CPA Firm P.C. Inc.<br>infosk@example-firm.com</p></div>"""
    else:
        subj = f"Annual Return Filing Reminder – {co}"
        body = f"""<div style="font-family:Arial;font-size:14px;color:#333;">{BANNER_EN}
<p>Dear {name},</p>
<p>This is a friendly reminder that the Annual Return for <b>{co}</b> (Corp #: {corp}) is due on <b>{due_en}</b>.</p>
<p>The Annual Return is a mandatory annual filing to confirm your company's registered information with {registry}. It is not a tax filing, but failure to file on time may result in your company being dissolved or struck from the registry.</p>
<p>Please complete the information form below and we will proceed with the filing on your behalf:</p>
<p><a href="{info_url}">{info_url}</a></p>
<br><p>Thank you,<br><b>ExampleCo Group</b><br>Example CPA Firm P.C. Inc.<br>infosk@example-firm.com</p></div>"""
    return subj, body, {}

def build_payment_url(rec, base_url):
    """Generate Cognito Forms URL with pre-filled client data."""
    import urllib.parse, json
    co   = str(rec.get("Legal Name", "") or "").strip()
    name = str(rec.get("Owner Name", "") or "").strip()
    prov = str(rec.get("Province", "") or "").strip()

    # B/B+ identification — field names must match your Cognito Forms form definition
    try:
        bb_charge = float(rec.get("B/B+ Charge") or 0)
    except (ValueError, TypeError):
        bb_charge = 0.0
    is_bb_plus = bb_charge > 0

    entry = {
        "ClientInformation": {
            "BusinessName": co,
            "ClientName": name,
            "Province": prov,
            "IsBBPlus": is_bb_plus,
            "BBPlusCharge": bb_charge if is_bb_plus else 0,
        },
    }
    return f"{base_url}?entry={urllib.parse.quote(json.dumps(entry))}"

def build_confirmation(rec, pay_url):
    lang = str(rec.get("Language","") or "").strip().upper()
    co         = rec.get("Legal Name","")
    name       = rec.get("Owner Name","")
    prov       = rec.get("Province","")
    total_fee  = rec.get("Total Fee","") or rec.get("B/B+ Charge","") or ""
    service_fee = rec.get("Service Fee","") or ""
    tax        = rec.get("Tax","") or ""
    gov_fee    = rec.get("Gov Fee","") or ""
    prefilled_pay = build_payment_url(rec, pay_url)
    if lang == "CN":
        banner = BANNER_CN
        return f"Annual Return 已完成 - {co}", f"""<div style="font-family:Arial;font-size:14px;color:#333;">{banner}
<p>{name} 您好，</p><p><b>{co}</b> 的 Annual Return 已成功提交。</p>
<table style="border-collapse:collapse;margin:15px 0;">
<tr><td style="padding:5px 15px;background:#f5f5f5;"><b>公司名称</b></td><td style="padding:5px 15px;">{co}</td></tr>
<tr><td style="padding:5px 15px;background:#f5f5f5;"><b>注册省份</b></td><td style="padding:5px 15px;">{prov}</td></tr>
<tr><td style="padding:5px 15px;background:#f5f5f5;"><b>服务费</b></td><td style="padding:5px 15px;">${service_fee}</td></tr>
<tr><td style="padding:5px 15px;background:#f5f5f5;"><b>税费</b></td><td style="padding:5px 15px;">${tax}</td></tr>
<tr><td style="padding:5px 15px;background:#f5f5f5;"><b>政府注册费</b></td><td style="padding:5px 15px;">${gov_fee}</td></tr>
<tr><td style="padding:5px 15px;background:#f5f5f5;"><b>总计</b></td><td style="padding:5px 15px;color:#c00;"><b>${total_fee}</b></td></tr>
</table>
<p>Filing 确认收据已附在本邮件中。</p>
<p>在线付款：<br><a href="{prefilled_pay}" style="color:#2F5496;font-weight:bold;">点击此处完成付款</a></p>
<p>感谢您的信任！</p><br><p>Best regards,<br><b>ExampleCo Group</b><br>Example CPA Firm P.C. Inc.<br>infosk@example-firm.com</p></div>"""
    else:
        banner = BANNER_EN
        return f"Annual Return Filed - {co}", f"""<div style="font-family:Arial;font-size:14px;color:#333;">{banner}
<p>Dear {name},</p><p>The <b>Annual Return</b> for <b>{co}</b> has been successfully filed.</p>
<table style="border-collapse:collapse;margin:15px 0;">
<tr><td style="padding:5px 15px;background:#f5f5f5;"><b>Company</b></td><td style="padding:5px 15px;">{co}</td></tr>
<tr><td style="padding:5px 15px;background:#f5f5f5;"><b>Province</b></td><td style="padding:5px 15px;">{prov}</td></tr>
<tr><td style="padding:5px 15px;background:#f5f5f5;"><b>Service Fee</b></td><td style="padding:5px 15px;">${service_fee}</td></tr>
<tr><td style="padding:5px 15px;background:#f5f5f5;"><b>Tax</b></td><td style="padding:5px 15px;">${tax}</td></tr>
<tr><td style="padding:5px 15px;background:#f5f5f5;"><b>Government Fee</b></td><td style="padding:5px 15px;">${gov_fee}</td></tr>
<tr><td style="padding:5px 15px;background:#f5f5f5;"><b>Total</b></td><td style="padding:5px 15px;color:#c00;"><b>${total_fee}</b></td></tr>
</table>
<p>The filing confirmation receipt is attached.</p>
<p>Online Payment:<br><a href="{prefilled_pay}" style="color:#2F5496;font-weight:bold;">Click here to complete payment</a></p>
<p>Thank you for your continued trust!</p><br><p>Best regards,<br><b>ExampleCo Group</b><br>Example CPA Firm P.C. Inc.<br>infosk@example-firm.com</p></div>"""

# def build_bc_access_request(rec):
#     """Template 4: BC Registry access grant request. (Disabled — BC merged into regular Reminder)"""
#     lang = str(rec.get("Language","") or "").strip().upper()
#     co   = rec.get("Legal Name","")
#     name = rec.get("Owner Name","")
#     firm_email = CONFIG["smtp_user"]
#     if lang == "CN":
#         return f"操作请求 – 授权我们代为提交年度申报 – {co}", f"""..."""
#     else:
#         return f"Action Required – Grant Access for Annual Return Filing – {co}", f"""..."""

# ============================================================
# CANDIDATE SELECTION
# ============================================================
def get_reminder_candidates(records, all_months=False):
    """Pending/Reminded clients whose due date falls in the current month, not yet sent today.
    If all_months=True, include all due dates regardless of month."""
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    current_year  = now.year
    current_month = now.month
    out = []
    for r in records:
        st = str(r.get("Status","") or "").strip()
        if st not in ("Pending", "Reminded", ""): continue
        em = str(r.get("Owner Email","") or "").strip()
        if not em or "@" not in em: continue
        if r.get("ReminderSentDate") == today: continue  # already sent today
        due = get_due_date(r)
        if due is None: continue
        if all_months or (due.year == current_year and due.month == current_month):
            r["_days_until_due"] = (due - now).days
            out.append(r)
    return out

def get_confirmation_candidates(records, month_dir):
    """Filed clients not yet sent confirmation today."""
    cdir = os.path.join(month_dir, "2. Filling Confirmation")
    today = datetime.now().strftime("%Y-%m-%d"); out = []
    for r in records:
        if str(r.get("Status","") or "").strip() != "Filed": continue
        if r.get("ConfirmationSentDate") == today: continue  # already sent today
        em = str(r.get("Owner Email","") or "").strip()
        if not em or "@" not in em: continue
        co = str(r.get("Legal Name","") or "").strip(); pdf = None
        if os.path.exists(cdir):
            for pat in [f"{co}*", f"*{co}*"]:
                ms = [m for m in glob.glob(os.path.join(cdir, pat)) if m.lower().endswith(".pdf")]
                if ms: pdf = ms[0]; break
        r["_pdf_path"] = pdf; out.append(r)
    return out

# def get_bc_access_candidates(records):
#     """Disabled — BC clients merged into regular Reminder flow."""
#     pass

# ============================================================
# MAIN
# ============================================================
def main():
    global SERVICE_FEE
    print("="*60)
    print("  Annual Return Management Tool")
    print("  ExampleCo Group / Example CPA Firm P.C. Inc.")
    print("="*60 + "\n")

    # ---- 自动检测可用路径 ----
    print("  Detecting server paths...")
    master_path, output_dir, monthly_base, templet_dir = resolve_paths()

    if not master_path:
        print("[ERROR] Cannot find master file on any known path.")
        print("  Tried:")
        for p in PATH_OPTIONS["master_paths"]:
            print(f"    - {p}")
        print("\n  Please check network drive connection or update PATH_OPTIONS in script.")
        input("\nPress Enter to exit...")
        sys.exit(1)

    CONFIG["master_path"] = master_path
    CONFIG["output_dir"] = output_dir or os.path.dirname(master_path)
    CONFIG["monthly_base"] = monthly_base or CONFIG["output_dir"]

    # Resolve template paths
    if templet_dir:
        CONFIG["tpl_reminder_cn"] = os.path.join(templet_dir, "Annual Return Reminder - CN.docx")
        CONFIG["tpl_reminder_en"] = os.path.join(templet_dir, "Annual Return Reminder - EN.docx")
        tpl_status = f"  [OK] Templet: {templet_dir}"
    else:
        tpl_status = "  [NOTE] Templet folder not found — using built-in email templates"

    print(f"  [OK] Master: {master_path}")
    print(f"  [OK] Output: {CONFIG['output_dir']}")
    print(f"  [OK] Monthly: {CONFIG['monthly_base']}")
    print(tpl_status)
    print()

    os.makedirs(CONFIG["output_dir"], exist_ok=True)
    log_file = setup_logging(CONFIG["output_dir"])
    op = os.path.join(CONFIG["output_dir"], CONFIG["output_filename"])
    cols = CONFIG["output_columns"]

    while True:
        print()
        print("=" * 60)
        print("  Select an option:")
        print("=" * 60)
        print("  [1] Update Tracker Only   (read master → update tracker)")
        print("  [2] Send Emails Only      (use existing tracker, no update)")
        print("  [3] Run All               (update tracker + send emails)")
        print("  [4] Review Fee Settings   (view/modify gov fees & tax rates)")
        print("  [5] Batch Update Status   (bulk change client status)")
        print("  [0] Exit")
        print("=" * 60)
        choice = input("  Your choice (0-5): ").strip()

        if choice == "0":
            print("  Goodbye!")
            break

        try:
            final = None

            # ---- Update Tracker (choice 1 or 3) ----
            if choice in ("1", "3"):
                if len(sys.argv) > 1:
                    password = sys.argv[1]
                else:
                    print("  (Password will not be shown as you type — this is normal)")
                    password = input("  Enter Master Password: ").strip()
                if not password:
                    print("  [ERROR] Empty password.")
                    continue

                mp = CONFIG["master_path"]
                if not os.path.exists(mp):
                    print(f"  [ERROR] Not found: {mp}")
                    continue

                # Monthly folders
                print("\n  --- Creating Monthly Folders ---")
                mdir = create_monthly_folders(CONFIG)

                # Decrypt & read
                print("\n  --- Reading Master ---")
                tmp = decrypt_to_tempfile(mp, password)
                try:
                    master = extract_master_data(tmp, CONFIG)
                finally:
                    try: os.unlink(tmp)
                    except: pass

                # Merge
                print("\n  --- Updating Tracker ---")
                existing = load_existing_tracker(op)
                nc = uc = rk = 0
                final = []
                for rec in master:
                    key = make_key(rec, CONFIG["unique_keys"])
                    if key in existing:
                        merged = dict(existing[key])
                        for f in CONFIG["master_sync_fields"]:
                            merged[f] = rec.get(f, merged.get(f))
                        final.append(merged)
                        if any(str(rec.get(f, "") or "") != str(existing[key].get(f, "") or "")
                               for f in CONFIG["master_sync_fields"]):
                            uc += 1
                    else:
                        nr = {c: rec.get(c, "") for c in cols}
                        nr["Status"] = CONFIG["default_status"]
                        for pf in CONFIG["protected_fields"]:
                            if pf != "Status":
                                nr[pf] = ""
                        final.append(nr)
                        nc += 1
                mk_keys = set(make_key(r, CONFIG["unique_keys"]) for r in master)
                for k, r in existing.items():
                    if k not in mk_keys:
                        final.append(r)
                        rk += 1
                logging.info(f"Merge: +{nc} new, ~{uc} updated, kept {rk}, total {len(final)}")

                # Apply Due Date and Fee calculations for all records with Province
                calc_count = 0
                for r in final:
                    if str(r.get("Province", "") or "").strip():
                        apply_calculations(r)
                        calc_count += 1
                if calc_count:
                    print(f"\n  Calculated Due Date + Fees for {calc_count} records (Province set)")

                # Backup & save
                if os.path.exists(op):
                    bd = os.path.join(CONFIG["output_dir"], "Backup")
                    os.makedirs(bd, exist_ok=True)
                    shutil.copy2(op, os.path.join(bd, f"Tracker_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"))

                save_tracker(final, op, cols)

                print(f"\n  Master records:  {len(master)}")
                print(f"  New clients:     {nc}")
                print(f"  Updated:         {uc}")
                print(f"  Total tracker:   {len(final)}")
                print(f"  Output: {op}")

            # ---- Send Emails (choice 2 or 3) ----
            if choice in ("2", "3"):
                # Load tracker if not already loaded from Step 1
                if final is None:
                    if not os.path.exists(op):
                        print(f"\n  [ERROR] Tracker not found: {op}")
                        print("  Run option [1] first to create the tracker.")
                        continue
                    existing = load_existing_tracker(op)
                    final = list(existing.values())
                    print(f"\n  Loaded {len(final)} records from existing tracker.")

                # Get monthly folder for PDF matching
                now = datetime.now()
                month_str = f"{now.year}.{now.month:02d}"
                mdir = os.path.join(CONFIG["monthly_base"], str(now.year), month_str)

                # Send-day advisory (1st, 15th, 25th)
                today_day = datetime.now().day
                if today_day not in (1, 15, 25):
                    print(f"\n  [NOTE] Today is the {today_day}th. Scheduled send days are 1st, 15th, 25th.")
                    off_sched = input("         Send anyway? (Y/N): ").strip().upper()
                    if off_sched != "Y":
                        print("  Skipped.")
                        input("\n  Press Enter to continue...")
                        continue

                print("\n  --- Email Module ---")
                rl = get_reminder_candidates(final)
                cl = get_confirmation_candidates(final, mdir)
                print(f"\n  Reminder candidates (current month): {len(rl)}")
                print(f"  Confirmation candidates:              {len(cl)}")

                # If no reminder candidates, offer to expand to all months
                if not rl:
                    exp = input("\n  No candidates for current month. Include ALL Pending/Reminded clients? (Y/N): ").strip().upper()
                    if exp == "Y":
                        rl = get_reminder_candidates(final, all_months=True)
                        print(f"  Expanded reminder candidates: {len(rl)}")

                if rl or cl:
                    if rl:
                        print("\n  --- Reminder List (Pending / Reminded) ---")
                        for i, r in enumerate(rl, 1):
                            days = r.get("_days_until_due", "?")
                            if isinstance(days, int):
                                day_str = f"OVERDUE {abs(days)}d" if days < 0 else ("DUE TODAY" if days == 0 else f"{days}d left")
                            else:
                                day_str = "?"
                            print(f"    {i}. {r.get('Legal Name', '')} | {r.get('Owner Email', '')} | Due: {r.get('Calculated Due Date','') or r.get('Annual Return','')} | {day_str}")
                    if cl:
                        print("\n  --- Confirmation List (Filed) ---")
                        for i, r in enumerate(cl, 1):
                            pdf_status = "PDF found" if r.get("_pdf_path") else "NO PDF"
                            print(f"    {i}. {r.get('Legal Name', '')} | {r.get('Owner Email', '')} | {pdf_status}")

                    ch = input("\n  Send emails now? (Y/N): ").strip().upper()
                    if ch == "Y":
                        sp = None
                        if shared_settings.EMAIL_AUTH_MODE == "password":
                            print("  (Password will not be shown as you type — this is normal)")
                            sp = input(f"  SMTP Password ({CONFIG['smtp_user']}): ").strip()
                        else:
                            print("  Using Gmail API OAuth authorization.")
                        try:
                            srv = connect_smtp(sp)
                            sr = sc = 0
                            failed = []   # (name, email, error)

                            for r in rl:
                                try:
                                    s, b, imgs = build_reminder(r)
                                    send_email(srv, r["Owner Email"].strip(), s, b, images=imgs)
                                    r["ReminderSentDate"] = datetime.now().strftime("%Y-%m-%d")
                                    r["Status"] = "Reminded"
                                    sr += 1
                                    print(f"    [OK]   {r.get('Legal Name','')}")
                                except Exception as e:
                                    logging.error(f"Remind fail {r.get('Legal Name')}: {e}")
                                    failed.append((r.get("Legal Name",""), r.get("Owner Email",""), str(e)))
                                    print(f"    [FAIL] {r.get('Legal Name','')}: {e}")

                            for r in cl:
                                try:
                                    s, b = build_confirmation(r, CONFIG["payment_url"])
                                    att = [r["_pdf_path"]] if r.get("_pdf_path") else []
                                    send_email(srv, r["Owner Email"].strip(), s, b, attachments=att)
                                    r["ConfirmationSentDate"] = datetime.now().strftime("%Y-%m-%d")
                                    r["Status"] = "Confirmed"
                                    sc += 1
                                    print(f"    [OK]   {r.get('Legal Name','')}")
                                except Exception as e:
                                    logging.error(f"Confirm fail {r.get('Legal Name')}: {e}")
                                    failed.append((r.get("Legal Name",""), r.get("Owner Email",""), str(e)))
                                    print(f"    [FAIL] {r.get('Legal Name','')}: {e}")

                            srv.quit()

                            # ── Send summary ──────────────────────────────
                            print(f"\n  {'='*56}")
                            print(f"  Reminders sent:      {sr}")
                            print(f"  Confirmations sent:  {sc}")
                            print(f"  Failed:              {len(failed)}")

                            if failed:
                                print(f"\n  [!] FAILED — {len(failed)} client(s):")
                                print(f"  {'#':<4} {'Client':<32} {'Email':<28} Error")
                                print("  " + "-" * 92)
                                for i, (nm, em, err) in enumerate(failed, 1):
                                    err_s = (err[:45] + "...") if len(err) > 45 else err
                                    print(f"  {i:<4} {nm[:31]:<32} {em[:27]:<28} {err_s}")
                                logging.warning(f"Failed sends: {[f[0] for f in failed]}")

                            # ── Post-send status editor ───────────────────
                            all_processed = rl + cl
                            if all_processed:
                                print(f"\n  {'='*56}")
                                print(f"  Post-Send Status Review  ({len(all_processed)} client(s))")
                                print(f"  {'='*56}")
                                POST_STATUS = ["Pending", "Reminded", "Filed", "Confirmed"]
                                while True:
                                    print(f"\n  {'#':<4} {'Client':<35} {'Status':<12} Email")
                                    print("  " + "-" * 82)
                                    for i, r in enumerate(all_processed, 1):
                                        print(f"  [{i:<2}] {str(r.get('Legal Name',''))[:34]:<35}"
                                              f" {str(r.get('Status','')):<12} {r.get('Owner Email','')}")
                                    print("\n  Modify status? (client number / 1,3,5 / 2-6 / all / Enter to finish)")
                                    sel_in = input("  Selection: ").strip().lower()
                                    if not sel_in:
                                        break
                                    idxs = set()
                                    if sel_in == "all":
                                        idxs = set(range(len(all_processed)))
                                    else:
                                        for part in sel_in.split(","):
                                            part = part.strip()
                                            if "-" in part:
                                                a, b2 = part.split("-", 1)
                                                try:
                                                    for x in range(int(a)-1, int(b2)):
                                                        if 0 <= x < len(all_processed): idxs.add(x)
                                                except ValueError: pass
                                            elif part.isdigit():
                                                x = int(part) - 1
                                                if 0 <= x < len(all_processed): idxs.add(x)
                                    if not idxs:
                                        print("  No valid selection."); continue
                                    print(f"\n  New status for {len(idxs)} client(s):")
                                    for si, sv in enumerate(POST_STATUS, 1):
                                        print(f"    [{si}] {sv}")
                                    ns = input("  Choice (1-4): ").strip()
                                    if ns.isdigit() and 1 <= int(ns) <= len(POST_STATUS):
                                        new_st = POST_STATUS[int(ns)-1]
                                        for idx in idxs:
                                            all_processed[idx]["Status"] = new_st
                                        print(f"  [OK] {len(idxs)} record(s) → '{new_st}'")
                                    else:
                                        print("  Invalid choice.")

                            save_tracker(final, op, cols)
                            print("\n  Tracker saved.")

                        except Exception as e:
                            logging.error(f"SMTP error: {e}", exc_info=True)
                            print(f"\n  [SMTP ERROR] {e}")
                    else:
                        print("  Skipped.")
                else:
                    print("\n  No emails to send.")

            # ---- Review Fee Settings (choice 4) ----
            if choice == "4":
                while True:
                    print("\n  === Current Fee & Tax Settings ===")
                    print(f"  [S] Service Fee: ${SERVICE_FEE:.2f}\n")
                    print(f"  {'#':<4} {'Province':<12} {'Gov Fee':>10} {'Tax Type':>10} {'GST':>8} {'PST':>8} {'Total Tax':>10} {'Total':>10}")
                    print("  " + "-" * 76)
                    prov_list = list(PROVINCE_RULES.keys())
                    for idx, prov in enumerate(prov_list, 1):
                        rules = PROVINCE_RULES[prov]
                        sf = SERVICE_FEE; gf = rules["gov_fee"]
                        if rules["tax_type"] == "HST":
                            tax = round(sf * rules["gst_rate"], 2)
                        else:
                            tax = round(sf * (rules["gst_rate"] + rules["pst_rate"]), 2)
                        total = round(sf + tax + gf, 2)
                        gst_pct = f"{rules['gst_rate']*100:.1f}%"
                        pst_pct = f"{rules['pst_rate']*100:.1f}%" if rules['pst_rate'] > 0 else "—"
                        print(f"  [{idx}] {prov:<12} ${gf:>8.2f} {rules['tax_type']:>10} {gst_pct:>8} {pst_pct:>8} ${tax:>8.2f} ${total:>8.2f}")

                    print()
                    print("  Options:")
                    print("    S          - Change Service Fee (currently ${:.2f})".format(SERVICE_FEE))
                    print("    1-5        - Edit a province (Gov Fee, GST rate, PST rate)")
                    print("    Enter/0    - Done, return to main menu")
                    sel = input("\n  Your choice: ").strip().upper()

                    if sel in ("", "0"):
                        break
                    elif sel == "S":
                        new_sf = input(f"  Current Service Fee: ${SERVICE_FEE:.2f}. New value: $").strip()
                        try:
                            SERVICE_FEE = float(new_sf)
                            print(f"  [OK] Service Fee updated to ${SERVICE_FEE:.2f}")
                        except ValueError:
                            print("  [ERROR] Invalid number.")
                    elif sel.isdigit() and 1 <= int(sel) <= len(prov_list):
                        pk = prov_list[int(sel) - 1]
                        rules = PROVINCE_RULES[pk]
                        print(f"\n  --- Editing: {pk} ---")
                        print(f"  Current values:")
                        print(f"    Gov Fee:  ${rules['gov_fee']:.2f}")
                        print(f"    Tax Type: {rules['tax_type']}")
                        print(f"    GST Rate: {rules['gst_rate']*100:.1f}%")
                        print(f"    PST Rate: {rules['pst_rate']*100:.1f}%")
                        print(f"  (Press Enter to keep current value)\n")

                        v = input(f"  Gov Fee [${rules['gov_fee']:.2f}]: $").strip()
                        if v:
                            try: rules["gov_fee"] = float(v); print(f"    -> Gov Fee = ${rules['gov_fee']:.2f}")
                            except ValueError: print("    [ERROR] Invalid number, kept original.")

                        v = input(f"  Tax Type [{rules['tax_type']}] (GST / GST+PST / HST): ").strip().upper()
                        if v and v in ("GST", "GST+PST", "HST"):
                            rules["tax_type"] = v; print(f"    -> Tax Type = {v}")

                        v = input(f"  GST/HST Rate [{rules['gst_rate']*100:.1f}%]: ").strip().replace("%", "")
                        if v:
                            try:
                                rate = float(v)
                                if rate > 1: rate = rate / 100
                                rules["gst_rate"] = rate; print(f"    -> GST/HST Rate = {rate*100:.1f}%")
                            except ValueError: print("    [ERROR] Invalid number, kept original.")

                        v = input(f"  PST Rate [{rules['pst_rate']*100:.1f}%]: ").strip().replace("%", "")
                        if v:
                            try:
                                rate = float(v)
                                if rate > 1: rate = rate / 100
                                rules["pst_rate"] = rate; print(f"    -> PST Rate = {rate*100:.1f}%")
                            except ValueError: print("    [ERROR] Invalid number, kept original.")

                        # Recalculate and show
                        sf = SERVICE_FEE; gf = rules["gov_fee"]
                        if rules["tax_type"] == "HST":
                            tax = round(sf * rules["gst_rate"], 2)
                        else:
                            tax = round(sf * (rules["gst_rate"] + rules["pst_rate"]), 2)
                        total = round(sf + tax + gf, 2)
                        print(f"\n  [OK] {pk} updated -> Total: ${total:.2f} (Service ${sf:.2f} + Tax ${tax:.2f} + Gov ${gf:.2f})")
                        print("  Note: Changes apply to this session. Edit PROVINCE_RULES in script for permanent changes.")
                    else:
                        print("  [ERROR] Invalid selection.")

            # ---- Batch Update Status (choice 5) ----
            if choice == "5":
                if not os.path.exists(op):
                    print(f"\n  [ERROR] Tracker not found: {op}")
                    print("  Run option [1] first to create the tracker.")
                else:
                    existing = load_existing_tracker(op)
                    all_records = list(existing.values())
                    if not all_records:
                        print("\n  No records found in tracker.")
                    else:
                        STATUS_OPTIONS = ["Pending", "Reminded", "Filed", "Confirmed"]
                        # Filter
                        print("\n  Filter by current Status:")
                        print("    [0] Show All")
                        for si, sv in enumerate(STATUS_OPTIONS, 1):
                            print(f"    [{si}] {sv}")
                        fsel = input("  Filter choice (0-4): ").strip()
                        if fsel.isdigit() and 1 <= int(fsel) <= len(STATUS_OPTIONS):
                            filtered = [r for r in all_records if str(r.get("Status","") or "").strip() == STATUS_OPTIONS[int(fsel)-1]]
                        else:
                            filtered = all_records

                        if not filtered:
                            print("  No records match the filter.")
                        else:
                            print(f"\n  {'#':<5} {'Legal Name':<35} {'Status':<12} {'Due Date'}")
                            print("  " + "-" * 70)
                            for i, r in enumerate(filtered, 1):
                                print(f"  [{i:<3}] {str(r.get('Legal Name',''))[:34]:<35} {str(r.get('Status','')):<12} {r.get('Calculated Due Date','') or r.get('Annual Return','')}")

                            print("\n  Select records to update:")
                            print("    all       - all records above")
                            print("    1,3,5     - specific numbers")
                            print("    2-8       - range")
                            sel_input = input("  Selection: ").strip().lower()

                            indices = set()
                            if sel_input == "all":
                                indices = set(range(len(filtered)))
                            else:
                                for part in sel_input.split(","):
                                    part = part.strip()
                                    if "-" in part:
                                        a, b = part.split("-", 1)
                                        try:
                                            for x in range(int(a)-1, int(b)):
                                                if 0 <= x < len(filtered): indices.add(x)
                                        except ValueError: pass
                                    elif part.isdigit():
                                        x = int(part) - 1
                                        if 0 <= x < len(filtered): indices.add(x)

                            if not indices:
                                print("  No valid selection.")
                            else:
                                print(f"\n  New Status:")
                                for si, sv in enumerate(STATUS_OPTIONS, 1):
                                    print(f"    [{si}] {sv}")
                                nsel = input("  Choice (1-4): ").strip()
                                if nsel.isdigit() and 1 <= int(nsel) <= len(STATUS_OPTIONS):
                                    new_status = STATUS_OPTIONS[int(nsel)-1]
                                    print(f"\n  Will update {len(indices)} record(s) → Status = '{new_status}'")
                                    confirm = input("  Confirm? (Y/N): ").strip().upper()
                                    if confirm == "Y":
                                        for idx in indices:
                                            filtered[idx]["Status"] = new_status
                                        save_tracker(all_records, op, cols)
                                        print(f"  [OK] {len(indices)} record(s) updated and saved.")
                                        logging.info(f"Batch status update: {len(indices)} records → {new_status}")
                                    else:
                                        print("  Cancelled.")
                                else:
                                    print("  Invalid status selection.")

            if choice not in ("0", "1", "2", "3", "4", "5"):
                print("  Invalid choice. Please enter 0-5.")

        except Exception as e:
            logging.error(f"Error: {e}", exc_info=True)
            print(f"\n  [ERROR] {e}")

        input("\n  Press Enter to continue...")

    input("\nPress Enter to exit...")

if __name__ == "__main__":
    main()
