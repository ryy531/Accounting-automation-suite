"""
Fill Payroll Schedule in #Payroll 2026.xlsx from 5. Payroll 2026.04.xlsx.
Uses fuzzy matching to link source company names to target.
"""
import msoffcrypto, openpyxl, tempfile, os, sys, io, re, shutil
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

TARGET = r'\\file-server\Shared Folder\4. General - Scan-Notes-Form-PPT\4. Accounting client list\#Payroll 2026.xlsx'
SOURCE = r'\\file-server\Shared Folder\4. General - Scan-Notes-Form-PPT\3. Task\2026\2026.04\5. Payroll 2026.04.xlsx'
PASSWORD = '0000000000'
VALID_OPTIONS = {'Monthly', 'Semi-Monthly', 'By-Weekly', 'Annually', 'NA'}

# ── Normalize helpers ──────────────────────────────────────────
def normalize_schedule(raw):
    s = raw.lower().strip()
    if 'yearly' in s or 'annual' in s:
        return 'Annually'
    if 'semi-monthly' in s or 'semi monthly' in s:
        return 'Semi-Monthly'
    if 'bi-weekly' in s and 'monthly' in s:
        return 'By-Weekly'  # combined, keep more frequent
    if 'bi-weekly' in s or 'biweekly' in s or 'by-weekly' in s:
        return 'By-Weekly'
    if 'monthly' in s:
        return 'Monthly'
    if '15th' in s:
        return 'Monthly'
    return None

def extract_bn(text):
    """Extract 9-digit BN from text."""
    if not text:
        return ''
    m = re.search(r'\b(\d{9})\b', str(text))
    return m.group(1) if m else ''

def extract_sk_num(text):
    """Extract SK corporation number like 101234571."""
    if not text:
        return ''
    m = re.search(r'\b(\d{6,9})\b', str(text))
    return m.group(1) if m else ''

def fuzzy_match(target_legal, target_bn, target_operating, src_name):
    """Score how well target matches source entry. Higher = better."""
    tl = (target_legal or '').lower().strip()
    tb = str(target_bn or '').strip()
    to_ = (target_operating or '').lower().strip()
    sn = src_name.lower().strip()

    score = 0

    # BN match is strongest
    if tb and tb in sn:
        score += 100
    # SK number in legal name match
    sk_num = extract_sk_num(tl)
    if sk_num and len(sk_num) >= 6 and sk_num in sn:
        score += 90
    # SK number from source in target legal
    src_sk = extract_sk_num(sn)
    if src_sk and len(src_sk) >= 6 and src_sk in tl:
        score += 90

    # Direct legal name substring
    if tl and len(tl) > 8:
        # Remove common suffixes for matching
        tl_clean = re.sub(r'\b(saskatchewan|ltd|inc|corp|limited|corporation|co|sk)\b\.?\s*', '', tl).strip()
        sn_clean = re.sub(r'\b(saskatchewan|ltd|inc|corp|limited|corporation|co|sk)\b\.?\s*', '', sn).strip()

        if tl_clean and tl_clean in sn_clean:
            score += 60
        if sn_clean and sn_clean in tl_clean:
            score += 60
        # Token overlap
        tl_tokens = set(tl_clean.split()) - {'', 'the', 'and', '&', 'of'}
        sn_tokens = set(sn_clean.split()) - {'', 'the', 'and', '&', 'of'}
        if tl_tokens and sn_tokens:
            overlap = len(tl_tokens & sn_tokens)
            if overlap >= 2:
                score += overlap * 15

    # Operating name match
    if to_ and len(to_) > 3:
        to_clean = re.sub(r'\b(saskatchewan|ltd|inc|corp|limited|corporation|co)\b\.?\s*', '', to_).strip()
        sn_clean2 = re.sub(r'\b(saskatchewan|ltd|inc|corp|limited|corporation|co)\b\.?\s*', '', sn).strip()
        if to_clean in sn_clean2 or sn_clean2 in to_clean:
            score += 50

    return score

# ── Read source data ───────────────────────────────────────────
wb_src = openpyxl.load_workbook(SOURCE, data_only=True)
ws_src = wb_src['Master']

# Build source lookup: prefer Master sheet entries (authoritative)
source_map = {}  # key = lower(name), value = (sched, priority)
MASTER_PRIORITY = 10
SHEET_PRIORITY = 5

for row in range(2, ws_src.max_row + 1):
    name = ws_src.cell(row=row, column=1).value
    sched = ws_src.cell(row=row, column=2).value
    if name and sched:
        key = name.strip().lower()
        existing = source_map.get(key)
        if not existing or existing[1] < MASTER_PRIORITY:
            source_map[key] = (sched.strip(), MASTER_PRIORITY)

# Also extract from schedule sheets (lower priority, supplementary)
for sname in ['Biweekly', 'Monthly', 'Semi-Monthly', 'Quarterly', 'Yearly']:
    ws2 = wb_src[sname]
    for row in ws2.iter_rows(min_row=3, max_row=ws2.max_row, values_only=False):
        name = row[1].value  # Column B
        if name and isinstance(name, str) and name.strip():
            key = name.strip().lower()
            sched_val = sname
            existing = source_map.get(key)
            if not existing:
                source_map[key] = (sched_val, SHEET_PRIORITY)

source_list = [(k, v[0]) for k, v in source_map.items()]

print(f'Source entries: {len(source_list)}')

# ── Read target ────────────────────────────────────────────────
with open(TARGET, 'rb') as f:
    of = msoffcrypto.OfficeFile(f)
    of.load_key(password=PASSWORD)
    tmp = tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False)
    of.decrypt(tmp)
    tmp.close()

wb = openpyxl.load_workbook(tmp.name)
ws = wb['Master']

# ── Match each target client ───────────────────────────────────
updates = []  # (row, schedule_value, confidence)
unmatched = []

for row in range(2, ws.max_row + 1):
    no = ws.cell(row=row, column=1).value
    if not no or no == 0:
        continue

    legal = ws.cell(row=row, column=2).value or ''
    bn = ws.cell(row=row, column=3).value
    operating = ws.cell(row=row, column=4).value or ''
    current = ws.cell(row=row, column=9).value

    # Skip if already filled
    if current and current in VALID_OPTIONS and current != 'NA':
        continue

    best_score = 0
    best_sched = None
    best_src = ''

    for src_key, src_sched in source_list:
        score = fuzzy_match(legal, bn, operating, src_key)
        if score > best_score:
            best_score = score
            best_sched = src_sched
            best_src = src_key

    if best_score >= 50 and best_sched:
        dropdown_val = normalize_schedule(best_sched)
        if dropdown_val and dropdown_val in VALID_OPTIONS:
            updates.append((row, no, legal[:50], best_src[:50], best_sched, dropdown_val, best_score))
        else:
            unmatched.append((row, no, legal[:50], best_src[:50], best_sched, best_score))
    else:
        unmatched.append((row, no, legal[:50], best_src[:50] if best_src else 'NO MATCH', best_sched or '', best_score))

# ── Print results for review ───────────────────────────────────
print(f'\n=== WILL UPDATE ({len(updates)}) ===')
for r, no, name, src, raw, final, score in sorted(updates, key=lambda x: x[0]):
    print(f'  Row {r:3d} (#{no:>3}) score={score:>3}: {name[:35]:35s} <- {raw:20s} -> {final}')

print(f'\n=== UNMATCHED ({len(unmatched)}) ===')
for r, no, name, src, raw, score in sorted(unmatched, key=lambda x: x[0]):
    print(f'  Row {r:3d} (#{no:>3}) score={score:>3}: {name[:35]:35s} | best_src={src[:30]} | {raw}')

os.unlink(tmp.name)
