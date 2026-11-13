"""WCB Batch Fill v3 - AI-powered info extraction"""
import openpyxl, os, re, sys, json, requests, time
import pikepdf
sys.stdout.reconfigure(encoding='utf-8')

TEMPLATE = '//file-server/Shared Folder/4. General - Scan-Notes-Form-PPT/WCB/SampleClean.pdf'
OUTPUT_DIR = '//file-server/Shared Folder/4. General - Scan-Notes-Form-PPT/WCB'
CLIENTS_BASE = '//file-server/Shared Folder/1. Accounting - Business Clients'
WCB_TRACKING = '//file-server/Shared Folder/4. General - Scan-Notes-Form-PPT/3. Task/2026/WCB/WCB tracking 2025.03 (03.17.2026) SS.xlsx'
TODAY_STR = __import__('datetime').date.today().strftime('%m/%d/%Y')

GEMINI_KEY = os.environ.get('GEMINI_API_KEY', '')
GEMINI_URL = f'https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-pro-preview:generateContent?key={GEMINI_KEY}'

def ask_gemini(prompt):
    resp = requests.post(GEMINI_URL, headers={'Content-Type': 'application/json'},
        json={'contents': [{'parts': [{'text': prompt}]}],
              'generationConfig': {'temperature': 0}})
    data = resp.json()
    if 'candidates' in data:
        return data['candidates'][0]['content']['parts'][0]['text']
    return ''

def extract_info_with_ai(sheet_text, company_name):
    """Use Gemini to extract structured info from raw sheet text."""
    prompt = f"""从以下公司信息表内容中提取这些字段，返回纯JSON（不要markdown代码块）：
- company_name: 公司全名
- address: 街道地址（不含城市/省/邮编）
- city: 城市
- province: 省份缩写（如SK, AB, BC）
- postal_code: 邮编（格式如S7K 0B8）
- phone: 公司或股东电话号码
- wcb_firm_number: WCB firm number/policy number
- owner_name: 股东/owner姓名

如果某字段找不到，值留空字符串。

公司名: {company_name}

信息表内容:
{sheet_text}

只返回JSON，不要其他文字。"""
    raw = ask_gemini(prompt)
    # Clean up response - remove markdown code blocks if present
    raw = raw.strip()
    raw = re.sub(r'^```json\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    try:
        return json.loads(raw)
    except:
        print(f'    AI返回解析失败: {raw[:200]}')
        return None

def find_info_sheet(name):
    letter = name[0].upper()
    folder = os.path.join(CLIENTS_BASE, '#Number Companies' if letter.isdigit() else letter)
    if not os.path.isdir(folder): return None
    key = name.upper()[:8]
    for d in os.listdir(folder):
        if key in d.upper():
            for sub in ['Company info', 'Company Information']:
                sd = os.path.join(folder, d, sub)
                if os.path.isdir(sd):
                    for f in os.listdir(sd):
                        if ('info sheet' in f.lower() or 'company info' in f.lower()) and f.endswith(('.xlsx','.xls')) and not f.startswith('~'):
                            return os.path.join(sd, f)
    return None

def read_sheet_raw(filepath):
    """Read entire sheet content as text for AI to parse."""
    try:
        wb = openpyxl.load_workbook(filepath, data_only=True)
    except:
        return ''
    lines = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(min_row=1, max_row=35, values_only=False):
            cells = []
            for c in row:
                if c.value is not None:
                    cells.append(f'{c.column_letter}{c.row}: {c.value}')
            if cells:
                lines.append(' | '.join(cells))
    wb.close()
    return '\n'.join(lines)

# ── Read WCB tracking ──
print('Reading WCB tracking...')
wb = openpyxl.load_workbook(WCB_TRACKING, read_only=True, data_only=True)
ws = wb['Sheet3']
companies = []
for row in ws.iter_rows(min_row=2, max_row=200, max_col=15, values_only=True):
    name = row[1] if len(row)>1 else None
    contract = row[12] if len(row)>12 else None
    if not name or not contract: continue
    name = str(name).strip(); contract = str(contract).strip()
    if not contract: continue
    companies.append({
        'name': name,
        'policy': str(row[5]).strip() if row[5] else '',
        'owner': str(row[8]).strip() if row[8] else '',
        'email': str(row[10]).strip() if row[10] else '',
    })
wb.close()
print(f'  {len(companies)} companies')

# ── AI extraction ──
print('\nExtracting company info with AI...')
enriched, skipped = [], []
for co in companies:
    ip = find_info_sheet(co['name'])
    if not ip:
        skipped.append(co['name']); print(f'  SKIP: {co["name"]}'); continue

    raw = read_sheet_raw(ip)
    if not raw.strip():
        skipped.append(co['name']); print(f'  SKIP (empty): {co["name"]}'); continue

    print(f'  AI提取: {co["name"]}...', end=' ', flush=True)
    info = extract_info_with_ai(raw, co['name'])

    if not info:
        print('FAILED')
        skipped.append(co['name'])
        continue

    # Merge: tracker data takes priority for policy/owner/email
    co['address'] = info.get('address', '')
    co['city'] = info.get('city', '')
    co['province'] = info.get('province', 'SK')
    co['postal_code'] = info.get('postal_code', '')
    co['phone'] = info.get('phone', '')
    co['policy'] = co.get('policy', '') or info.get('wcb_firm_number', '')
    co['owner'] = co.get('owner', '') or info.get('owner_name', '')
    co['email'] = co.get('email', '') or info.get('email', '')

    # ── Clean up data ──
    # Address: remove "None", extra commas/spaces
    for key in ('address', 'city'):
        co[key] = co[key].replace('None', '').strip()
    co['address'] = re.sub(r'\s*,\s*(,\s*)+', ', ', co['address']).strip(', ')
    co['address'] = re.sub(r'\s{2,}', ' ', co['address'])
    # Phone: keep first number only (split by / or ; or ,)
    phone = re.split(r'\s*[/;,]\s*', co['phone'])
    co['phone'] = phone[0].strip() if phone else ''
    # Phone: normalize to (xxx) xxx-xxxx
    ph = re.sub(r'[()\s\.\-]', '', co['phone'])  # strip all formatting
    if re.match(r'^\d{10}$', ph):
        co['phone'] = f'({ph[:3]}) {ph[3:6]}-{ph[6:]}'
    elif re.match(r'^1\d{10}$', ph):
        co['phone'] = f'({ph[1:4]}) {ph[4:7]}-{ph[7:]}'
    elif re.match(r'^\+\d', co['phone']):
        pass  # keep international as-is
    # Policy: convert '0', 'NA', 'N/A', 'None' to empty
    if co['policy'].strip().upper() in ('0', 'NA', 'N/A', 'NONE', 'NAN'):
        co['policy'] = ''

    print(f'OK | {co["address"]}, {co["city"]}, {co["province"]} {co["postal_code"]} | phone={co["phone"]} firm={co["policy"]}')
    enriched.append(co)
    time.sleep(0.5)  # rate limit

print(f'\nReady: {len(enriched)}, Skipped: {len(skipped)}')

# ── Fill PDFs ──
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
            if not annots: continue
            for ann in annots:
                if isinstance(ann, pikepdf.Dictionary):
                    t = str(ann.get('/T','')); ft = str(ann.get('/FT',''))
                    if ft == '/Tx' and t: fmap[t] = ann

        fills = {
            'Legal name of company': co['name'],
            'SK WCB firm number': co.get('policy',''),
            'Company address': co.get('address',''),
            'City': co.get('city',''),
            'Province': co.get('province','SK'),
            'Postal code': co.get('postal_code',''),
            'Phone number': co.get('phone',''),
            'Email address': co.get('email',''),
            'Printed name': co.get('owner','').upper(),
        }
        for k, v in fills.items():
            if k in fmap:
                fmap[k]['/V'] = pikepdf.String(v)
                if '/AP' in fmap[k]: del fmap[k]['/AP']

        af = pdf.Root['/AcroForm']
        if '/ADBE_EchoSign' in af: del af['/ADBE_EchoSign']
        pdf.save(out)
        pdf.close()
        ok += 1
        print(f'  OK: {safe}')
    except Exception as e:
        errs.append((safe, str(e)))
        print(f'  ERROR: {safe} - {e}')

print(f'\nDone! {ok} PDFs created, {len(errs)} errors')
if skipped:
    print(f'Skipped ({len(skipped)}):')
    for s in skipped: print(f'  - {s}')
