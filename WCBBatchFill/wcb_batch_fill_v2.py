"""WCB Batch Fill v2 - uses SampleClean.pdf (AcroForm only, no XFA)"""
import openpyxl, os, re, sys, pikepdf
sys.stdout.reconfigure(encoding='utf-8')

TEMPLATE = '//file-server/Shared Folder/4. General - Scan-Notes-Form-PPT/WCB/SampleClean.pdf'
OUTPUT_DIR = '//file-server/Shared Folder/4. General - Scan-Notes-Form-PPT/WCB'
CLIENTS_BASE = '//file-server/Shared Folder/1. Accounting - Business Clients'
WCB_TRACKING = '//file-server/Shared Folder/4. General - Scan-Notes-Form-PPT/3. Task/2026/WCB/WCB tracking 2025.03 (03.17.2026) SS.xlsx'
TODAY_STR = __import__('datetime').date.today().strftime('%m/%d/%Y')

# ── Address parsing ──
def parse_address(raw):
    if not raw:
        return '', '', 'SK', ''
    raw = str(raw).strip().replace('\n', ' ').replace('\r', ' ')
    raw = re.sub(r',?\s*Canada\s*$', '', raw, flags=re.IGNORECASE)
    raw = re.sub(r',\s*Canada,', ',', raw, flags=re.IGNORECASE)

    postal = ''
    pm = re.search(r'[A-Z]\d[A-Z]\s*\d[A-Z]\d', raw.upper())
    if pm: postal = pm.group()

    parts = [p.strip() for p in raw.split(',') if p.strip()]
    street, city, province = '', '', 'SK'

    if len(parts) >= 3:
        street = parts[0]
        for p in parts[1:]:
            p = p.strip()
            if re.match(r'^Canada$', p, re.IGNORECASE): continue
            if re.match(r'^[A-Z]\d[A-Z]\s*\d[A-Z]\d$', p): continue

            prov_m = re.match(r'^(SK|AB|BC|MB|ON|QC|NB|NS|PE|NL|NT|YT|NU|Saskatchewan|Alberta)\b\s*', p, re.IGNORECASE)
            if prov_m:
                ps = prov_m.group(1).upper()
                if ps == 'SASKATCHEWAN': ps = 'SK'
                elif ps == 'ALBERTA': ps = 'AB'
                province = ps
                rest = p[prov_m.end():].strip()
                if rest:
                    rest_clean = re.sub(r'[A-Z]\d[A-Z]\s*\d[A-Z]\d', '', rest).strip()
                    if rest_clean and not city: city = rest_clean
            elif not city:
                mixed = re.match(r'^(.+?)\s+(SK|AB|BC|MB|ON|QC)\s+([A-Z]\d[A-Z]\s*\d[A-Z]\d)$', p, re.IGNORECASE)
                if mixed:
                    city = mixed.group(1).strip()
                    province = mixed.group(2).upper()
                else:
                    city = p
    elif len(parts) == 2:
        street = parts[0]
        rest = parts[1].strip()
        prov_m = re.match(r'^(SK|AB|BC|MB|ON|QC|Saskatchewan|Alberta)\b\s*', rest, re.IGNORECASE)
        if prov_m:
            province = prov_m.group(1).upper()
            if province == 'SASKATCHEWAN': province = 'SK'
            elif province == 'ALBERTA': province = 'AB'
        else:
            city = rest
    elif len(parts) == 1:
        street = parts[0]

    return street.strip(), city.strip(), province.strip(), postal.strip()

def clean_phone(ph):
    ph = re.sub(r'^[Pp]hone:\s*', '', str(ph)).strip()
    ph = re.sub(r'\s*\(temporary\)', '', ph).strip()
    return ph if ph and ph.lower() != 'phone:' else ''

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
                        if ('info sheet' in f.lower() or 'company info' in f.lower()) and f.endswith(('.xlsx','.xls')):
                            return os.path.join(sd, f)
    return None

def read_info(filepath):
    try:
        wb = openpyxl.load_workbook(filepath, data_only=True)
        ws = wb.active
    except: return None

    info = {'address':'','city':'','province':'SK','postal_code':'',
            'phone':'','wcb_firm_number':'','owner_phone':''}

    r1a = str(ws.cell(1,1).value or '').strip()
    r2b = str(ws.cell(2,2).value or '').strip()
    is_horizontal = ('No.' in r1a and 'ISC' in r2b) or r1a == 'No.'

    if is_horizontal:
        r3 = {}
        for c in ws[3]:
            if c.value is not None: r3[c.column_letter] = c.value
        addr_raw = str(r3.get('AT',''))
        street, city, prov, pc = parse_address(addr_raw)
        phone = clean_phone(str(r3.get('AQ','')))
        info.update({'address':street,'city':city,'province':prov,'postal_code':pc,
                     'phone':phone,'owner_phone':phone,'wcb_firm_number':str(r3.get('AO',''))})
    else:
        for row in ws.iter_rows(min_row=1, max_row=30, values_only=False):
            vals = {c.column_letter: c.value for c in row if c.value is not None}
            if not vals: continue
            a = str(vals.get('A','')).strip().lower()
            b = vals.get('B','')

            if 'physical address' in a and b:
                s, c, p, pc = parse_address(str(b))
                info.update({'address':s,'city':c,'province':p,'postal_code':pc})
            elif a == 'address' and b and not info['address']:
                info['address'] = str(b).strip()
            elif a == 'city' and b and not info['city']:
                info['city'] = str(b).strip()
            elif a == 'province' and b:
                info['province'] = str(b).strip()
            elif ('post code' in a or 'postal' in a) and b and not info['postal_code']:
                info['postal_code'] = str(b).strip()
            elif 'wcb' in a and 'firm' in a and b:
                info['wcb_firm_number'] = str(b).strip()

            if 'phone:' in a:
                ph = clean_phone(str(b) if b else str(vals.get('A','')).replace('Phone:','',1).replace('phone:','',1))
                if ph and not info['owner_phone']:
                    info['owner_phone'] = ph
            if 'owner phone' in a and b:
                info['owner_phone'] = str(b).strip()

    wb.close()
    # Normalize province
    if info['province'] == 'Saskatchewan': info['province'] = 'SK'
    elif info['province'] == 'Alberta': info['province'] = 'AB'
    return info

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

# ── Enrich ──
print('Looking up company info...')
enriched, skipped = [], []
for co in companies:
    ip = find_info_sheet(co['name'])
    if not ip:
        skipped.append(co['name']); print(f'  SKIP: {co["name"]}'); continue
    info = read_info(ip)
    if not info:
        skipped.append(co['name']); print(f'  SKIP: {co["name"]}'); continue

    co['address'] = info['address']
    co['city'] = info['city']
    co['province'] = info['province']
    co['postal_code'] = info['postal_code']
    co['phone'] = clean_phone(info.get('owner_phone','') or info.get('phone',''))
    co['email'] = co.get('email','') or ''
    co['policy'] = co.get('policy','') or info.get('wcb_firm_number','')
    enriched.append(co)
    print(f'  OK: {co["name"]} | {co["address"]}, {co["city"]}, {co["province"]} {co["postal_code"]} | phone={co["phone"]} firm={co["policy"]}')

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

        pdf.Root['/AcroForm']['/NeedAppearances'] = pikepdf.Boolean(True)
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
