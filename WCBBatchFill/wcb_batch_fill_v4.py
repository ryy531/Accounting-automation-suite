"""WCB Batch Fill v4 - Correct data source from Missing Companies list"""
import openpyxl, os, re, sys, json, time, io
import pikepdf
from openai import OpenAI
from dotenv import load_dotenv
sys.stdout.reconfigure(encoding='utf-8')

load_dotenv(os.path.join(os.path.dirname(__file__), '_lib', 'config', '.env'))

# ── Paths ──────────────────────────────────────────────────────────────────────
TEMPLATE   = '//file-server/Shared Folder/4. General - Scan-Notes-Form-PPT/WCB/SampleClean.pdf'
OUTPUT_DIR = '//file-server/Shared Folder/4. General - Scan-Notes-Form-PPT/WCB'
MISSING_XL = os.path.join(OUTPUT_DIR, 'Missing Companies - paths.xlsx')
WCB_TRACKING = '//file-server/Shared Folder/4. General - Scan-Notes-Form-PPT/3. Task/2026/WCB/WCB tracking 2025.03 (03.17.2026) SS.xlsx'
TODAY_STR = __import__('datetime').date.today().strftime('%m/%d/%Y')

DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY', '')
DEEPSEEK_MODEL = 'deepseek-v4-pro'
MAX_RETRIES = 3

def ask_deepseek(prompt, max_retries=MAX_RETRIES):
    """Call DeepSeek v4 Pro with retry logic."""
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.create(
                model=DEEPSEEK_MODEL,
                messages=[
                    {"role": "system", "content": "You are a data extraction assistant. Always respond with valid JSON only."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0, stream=False,
            )
            return resp.choices[0].message.content
        except Exception as e:
            wait = (2 ** attempt) * 5
            print(f'  retry {attempt+1}/{max_retries} in {wait}s ({e})', end=' ', flush=True)
            time.sleep(wait)
    return ''


def extract_pdf_text(pdf_bytes):
    """Extract text from PDF bytes."""
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(io.BytesIO(pdf_bytes))
        return '\n'.join(p.extract_text() or '' for p in reader.pages)
    except ImportError:
        pass
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            return '\n'.join(p.extract_text() or '' for p in pdf.pages)
    except ImportError:
        print('  No PDF text extraction library (install PyPDF2 or pdfplumber)')
        return ''


def extract_info_from_text(text, company_name):
    """Use DeepSeek to extract structured info from raw text."""
    prompt = f"""从以下公司信息内容中提取这些字段，返回纯JSON（不要markdown代码块）：
- company_name: 公司全名
- address: 街道地址（不含城市/省/邮编）
- city: 城市
- province: 省份缩写（如SK, AB, BC）
- postal_code: 邮编（格式如S7K 0B8）
- phone: 公司或股东电话号码
- wcb_firm_number: WCB firm number / policy number
- owner_name: 股东/owner姓名
- email: 邮箱

如果某字段找不到，值留空字符串。

公司名: {company_name}

信息内容:
{text}

只返回JSON，不要其他文字。"""
    raw = ask_deepseek(prompt).strip()
    raw = re.sub(r'^```json\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    try:
        return json.loads(raw)
    except:
        print(f'    AI JSON解析失败: {raw[:200]}')
        return None


def extract_info_from_pdf(pdf_bytes, company_name, pdf_name):
    """Use DeepSeek to extract structured info from a PDF file."""
    prompt = f"""从这份PDF文档中提取以下公司信息，返回纯JSON（不要markdown代码块）：
- company_name: 公司全名
- address: 街道地址（不含城市/省/邮编）
- city: 城市
- province: 省份缩写（如SK, AB, BC）
- postal_code: 邮编（格式如S7K 0B8）
- phone: 公司或股东电话号码
- wcb_firm_number: WCB firm number / policy number（如果有的话）
- owner_name: 股东/owner姓名
- email: 邮箱

如果某字段找不到，值留空字符串。
公司名: {company_name}
文件名: {pdf_name}
只返回JSON，不要其他文字。"""
    pdf_text = extract_pdf_text(pdf_bytes)
    if not pdf_text.strip():
        print('PDF_TEXT_EMPTY', end=' ')
        return None
    prompt += f'\n\nPDF内容:\n{pdf_text}'
    raw = ask_deepseek(prompt).strip()
    raw = re.sub(r'\s*```$', '', raw)
    try:
        return json.loads(raw)
    except:
        print(f'    AI PDF JSON解析失败: {raw[:200]}')
        return None


def read_sheet_raw(filepath):
    """Read Excel sheet content as text for AI to parse."""
    try:
        wb = openpyxl.load_workbook(filepath, data_only=True)
    except Exception as e:
        print(f'    Excel读取失败: {e}')
        return ''
    lines = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(min_row=1, max_row=40, values_only=False):
            cells = []
            for c in row:
                if c.value is not None:
                    cells.append(f'{c.column_letter}{c.row}: {c.value}')
            if cells:
                lines.append(' | '.join(cells))
    wb.close()
    return '\n'.join(lines)


# ── Read Missing Companies list ────────────────────────────────────────────────
print('Reading Missing Companies list...')
wb_miss = openpyxl.load_workbook(MISSING_XL, data_only=True)
ws_miss = wb_miss['WCB Company List']

companies = []
for row in ws_miss.iter_rows(min_row=2, max_row=ws_miss.max_row, values_only=True):
    idx, name, owner, email, status, info_path = (row[0], row[1], row[2], row[3], row[4], row[5])
    if not name or not idx:
        continue
    name = str(name).strip()
    if str(status or '').strip().upper() == 'DONE':
        print(f'  SKIP (done): {name}')
        continue
    companies.append({
        'name': name,
        'owner': str(owner or '').strip(),
        'email': str(email or '').strip(),
        'info_path': str(info_path or '').strip() if info_path else '',
    })
wb_miss.close()
print(f'  {len(companies)} pending companies to process\n')

# ── Read WCB tracking for firm numbers ─────────────────────────────────────────
print('Reading WCB tracking for firm numbers...')
tracking_map = {}  # company_name_upper -> policy_number
wb_tr = openpyxl.load_workbook(WCB_TRACKING, read_only=True, data_only=True)
ws_tr = wb_tr['Sheet3']
for row in ws_tr.iter_rows(min_row=2, max_row=200, max_col=15, values_only=True):
    name = str(row[1] or '').strip()
    policy = str(row[5] or '').strip() if row[5] else ''
    if name and name != 'None' and policy and policy not in ('None', '0', ''):
        tracking_map[name.upper()] = policy
wb_tr.close()
print(f'  {len(tracking_map)} firm numbers loaded\n')


def get_firm_number(company_name):
    """Look up firm number from tracking map by fuzzy match."""
    key = company_name.upper()
    if key in tracking_map:
        return tracking_map[key]
    # Try partial match
    for k, v in tracking_map.items():
        if key[:10] in k or k[:10] in key:
            return v
    return ''


# ── Extract info for each company ──────────────────────────────────────────────
print('Extracting company info...')
enriched, skipped = [], []

for co in companies:
    name = co['name']
    info_path = co['info_path']

    # Skip if PDF already exists in output folder
    safe = re.sub(r'[<>:"/\\|?*]', '', name).strip()
    out_path = os.path.join(OUTPUT_DIR, f'{safe} - WCB.pdf')
    if os.path.isfile(out_path):
        print(f'  SKIP (PDF exists): {name}')
        continue

    if not info_path:
        skipped.append(name)
        print(f'  SKIP (no path): {name}')
        continue

    if not os.path.isfile(info_path):
        skipped.append(name)
        print(f'  SKIP (file not found): {name} -> {info_path}')
        continue

    is_pdf = info_path.lower().endswith('.pdf')
    print(f'  {"PDF" if is_pdf else "Excel"}: {name}...', end=' ', flush=True)

    try:
        if is_pdf:
            with open(info_path, 'rb') as f:
                pdf_bytes = f.read()
            info = extract_info_from_pdf(pdf_bytes, name, os.path.basename(info_path))
        else:
            raw = read_sheet_raw(info_path)
            if not raw.strip():
                print('EMPTY SHEET')
                skipped.append(name)
                continue
            info = extract_info_from_text(raw, name)
    except Exception as e:
        print(f'ERROR: {e}')
        skipped.append(name)
        continue

    if not info:
        print('AI FAILED')
        skipped.append(name)
        continue

    # Merge extracted info
    co['address'] = info.get('address', '')
    co['city'] = info.get('city', '')
    co['province'] = info.get('province', 'SK')
    co['postal_code'] = info.get('postal_code', '')
    co['phone'] = info.get('phone', '')
    # Firm number: tracking > AI > empty
    co['policy'] = get_firm_number(name) or info.get('wcb_firm_number', '')
    # Owner/email: Excel data takes priority over AI
    co['owner'] = co.get('owner', '') or info.get('owner_name', '')
    co['email'] = co.get('email', '') or info.get('email', '')

    # ── Clean up data ──
    for key in ('address', 'city'):
        co[key] = co[key].replace('None', '').strip()
    co['address'] = re.sub(r'\s*,\s*(,\s*)+', ', ', co['address']).strip(', ')
    co['address'] = re.sub(r'\s{2,}', ' ', co['address'])
    # Phone: keep first number only
    phone = re.split(r'\s*[/;,]\s*', co['phone'])
    co['phone'] = phone[0].strip() if phone else ''
    # Phone: normalize to (xxx) xxx-xxxx
    ph = re.sub(r'[()\s\.\-]', '', co['phone'])
    if re.match(r'^\d{10}$', ph):
        co['phone'] = f'({ph[:3]}) {ph[3:6]}-{ph[6:]}'
    elif re.match(r'^1\d{10}$', ph):
        co['phone'] = f'({ph[1:4]}) {ph[4:7]}-{ph[7:]}'
    # Policy: clean invalid values
    if co['policy'].strip().upper() in ('0', 'NA', 'N/A', 'NONE', 'NAN', '') or co['policy'].startswith('#'):
        co['policy'] = ''

    print(f'OK | {co["address"]}, {co["city"]}, {co["province"]} {co["postal_code"]} '
          f'| phone={co["phone"]} | firm={co["policy"]}')

    enriched.append(co)
    time.sleep(1)  # rate limit

print(f'\nReady: {len(enriched)}, Skipped: {len(skipped)}')

# ── Fill PDFs ──────────────────────────────────────────────────────────────────
print('\nFilling PDFs...')
ok, errs = 0, []

for co in enriched:
    safe = re.sub(r'[<>:"/\\|?*]', '', co['name']).strip()
    out = os.path.join(OUTPUT_DIR, f'{safe} - WCB.pdf')
    try:
        pdf = pikepdf.open(TEMPLATE)
        fmap = {}
        for page in pdf.pages:
            annots = page.get('/Annots')
            if not annots:
                continue
            for ann in annots:
                if isinstance(ann, pikepdf.Dictionary):
                    t = str(ann.get('/T', ''))
                    ft = str(ann.get('/FT', ''))
                    if ft == '/Tx' and t:
                        fmap[t] = ann

        fills = {
            'Legal name of company': co['name'],
            'SK WCB firm number': co.get('policy', ''),
            'Company address': co.get('address', ''),
            'City': co.get('city', ''),
            'Province': co.get('province', 'SK'),
            'Postal code': co.get('postal_code', ''),
            'Phone number': co.get('phone', ''),
            'Email address': co.get('email', ''),
            'Printed name': co.get('owner', '').upper(),
        }
        for k, v in fills.items():
            if k in fmap:
                fmap[k]['/V'] = pikepdf.String(v)
                if '/AP' in fmap[k]:
                    del fmap[k]['/AP']

        af = pdf.Root['/AcroForm']
        if '/ADBE_EchoSign' in af:
            del af['/ADBE_EchoSign']
        pdf.save(out)
        pdf.close()
        ok += 1
        print(f'  OK: {safe}')
    except Exception as e:
        errs.append((safe, str(e)))
        print(f'  ERROR: {safe} - {e}')

print(f'\nDone! {ok} PDFs created, {len(errs)} errors')
if skipped:
    print(f'\nSkipped ({len(skipped)}):')
    for s in skipped:
        print(f'  - {s}')
if errs:
    print(f'\nErrors:')
    for s, e in errs:
        print(f'  - {s}: {e}')
