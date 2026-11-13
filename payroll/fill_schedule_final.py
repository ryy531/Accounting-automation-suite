"""
Fill Payroll Schedule in #Payroll 2026.xlsx from 5. Payroll 2026.04.xlsx.
- Multiple schedules: I = primary, J = secondary
- No match: I = NA
"""
import msoffcrypto, openpyxl, tempfile, os, sys, io, re
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

TARGET = r'\\file-server\Shared Folder\4. General - Scan-Notes-Form-PPT\4. Accounting client list\#Payroll 2026.xlsx'
SOURCE = r'\\file-server\Shared Folder\4. General - Scan-Notes-Form-PPT\3. Task\2026\2026.04\5. Payroll 2026.04.xlsx'
PASSWORD = '0000000000'
VALID_OPTIONS = {'Monthly', 'Semi-Monthly', 'By-Weekly', 'Annually', 'NA'}

SHEET_TO_DROPDOWN = {
    'Biweekly': 'By-Weekly', 'Monthly': 'Monthly',
    'Semi-Monthly': 'Semi-Monthly', 'Quarterly': 'NA',
    'Yearly': 'Annually',
}

# ── Normalize helpers ──────────────────────────────────────────
def normalize_schedule(raw):
    s = raw.lower().strip()
    if 'yearly' in s or 'annual' in s:
        return 'Annually'
    if 'semi-monthly' in s or 'semi monthly' in s:
        return 'Semi-Monthly'
    if 'bi-weekly' in s or 'biweekly' in s or 'by-weekly' in s:
        return 'By-Weekly'
    if 'monthly' in s:
        return 'Monthly'
    if '15th' in s:
        return 'Monthly'
    return None

def extract_sk_num(text):
    if not text:
        return ''
    m = re.search(r'\b(\d{6,9})\b', str(text))
    return m.group(1) if m else ''

def fuzzy_match(target_legal, target_bn, target_operating, src_name):
    tl = (target_legal or '').lower().strip()
    tb = str(target_bn or '').strip()
    to_ = (target_operating or '').lower().strip()
    sn = src_name.lower().strip()
    score = 0
    if tb and tb in sn:
        score += 100
    sk_num = extract_sk_num(tl)
    if sk_num and len(sk_num) >= 6 and sk_num in sn:
        score += 90
    src_sk = extract_sk_num(sn)
    if src_sk and len(src_sk) >= 6 and src_sk in tl:
        score += 90
    if tl and len(tl) > 8:
        tl_clean = re.sub(r'\b(saskatchewan|ltd|inc|corp|limited|corporation|co|sk)\b\.?\s*', '', tl).strip()
        sn_clean = re.sub(r'\b(saskatchewan|ltd|inc|corp|limited|corporation|co|sk)\b\.?\s*', '', sn).strip()
        if tl_clean and tl_clean in sn_clean:
            score += 60
        if sn_clean and sn_clean in tl_clean:
            score += 60
        tl_tokens = set(tl_clean.split()) - {'', 'the', 'and', '&', 'of'}
        sn_tokens = set(sn_clean.split()) - {'', 'the', 'and', '&', 'of'}
        if tl_tokens and sn_tokens:
            overlap = len(tl_tokens & sn_tokens)
            if overlap >= 2:
                score += overlap * 15
    if to_ and len(to_) > 3:
        to_clean = re.sub(r'\b(saskatchewan|ltd|inc|corp|limited|corporation|co)\b\.?\s*', '', to_).strip()
        sn_clean2 = re.sub(r'\b(saskatchewan|ltd|inc|corp|limited|corporation|co)\b\.?\s*', '', sn).strip()
        if to_clean in sn_clean2 or sn_clean2 in to_clean:
            score += 50
    return score

# ── Read source data ───────────────────────────────────────────
wb_src = openpyxl.load_workbook(SOURCE, data_only=True)
ws_src = wb_src['Master']

# Build: source name -> set of schedule types (from all sheets)
company_all_schedules = defaultdict(set)  # lower(name) -> set of dropdown values

# Master sheet
for row in range(2, ws_src.max_row + 1):
    name = ws_src.cell(row=row, column=1).value
    sched = ws_src.cell(row=row, column=2).value
    if name and sched:
        raw = sched.strip().lower()
        key = name.strip().lower()
        if 'bi-weekly' in raw and 'monthly' in raw:
            company_all_schedules[key].add('By-Weekly')
            company_all_schedules[key].add('Monthly')
        else:
            nv = normalize_schedule(raw)
            if nv:
                company_all_schedules[key].add(nv)

# Schedule sheets
for sname in ['Biweekly', 'Monthly', 'Semi-Monthly', 'Quarterly', 'Yearly']:
    ws2 = wb_src[sname]
    dropdown = SHEET_TO_DROPDOWN.get(sname, 'NA')
    for row in ws2.iter_rows(min_row=3, max_row=ws2.max_row, values_only=False):
        name = row[1].value
        if name and isinstance(name, str) and name.strip():
            company_all_schedules[name.strip().lower()].add(dropdown)

# Also build simple name->sched for fuzzy matching (use Master data preferentially)
source_map = {}
for row in range(2, ws_src.max_row + 1):
    name = ws_src.cell(row=row, column=1).value
    sched = ws_src.cell(row=row, column=2).value
    if name and sched:
        key = name.strip().lower()
        if key not in source_map:
            source_map[key] = sched.strip()

for sname in ['Biweekly', 'Monthly', 'Semi-Monthly', 'Quarterly', 'Yearly']:
    ws2 = wb_src[sname]
    for row in ws2.iter_rows(min_row=3, max_row=ws2.max_row, values_only=False):
        name = row[1].value
        if name and isinstance(name, str) and name.strip():
            key = name.strip().lower()
            if key not in source_map:
                source_map[key] = sname

source_list = [(k, v) for k, v in source_map.items()]

# ── Manual overrides: row -> list of schedule dropdown values ──
MANUAL_OVERRIDES = {
    46: ['Monthly'],                                    # Client A
    55: ['Semi-Monthly'],                               # Client B
    57: ['Monthly'],                                    # Client C
    99: ['Monthly'],                                    # Client D
    101: ['Monthly'],                                   # Client E
    102: ['Monthly'],                                   # Client F
    103: ['Monthly'],                                   # Client G
    144: ['Semi-Monthly'],                              # Client H
    36: ['Monthly'],                                    # Client I
    75: ['Monthly'],                                    # Client J
    76: ['Monthly'],                                    # Client K (dup)
    45: ['Monthly'],                                    # Client L
    56: ['Semi-Monthly', 'Monthly'],                    # Client M (also Monthly)
    98: ['By-Weekly'],                                  # Client N
}

# ── Read target ────────────────────────────────────────────────
with open(TARGET, 'rb') as f:
    of = msoffcrypto.OfficeFile(f)
    of.load_key(password=PASSWORD)
    tmp = tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False)
    of.decrypt(tmp)
    tmp.close()

wb = openpyxl.load_workbook(tmp.name)
ws = wb['Master']

# Check if column J exists and what header to use
j_header = ws.cell(row=1, column=10).value

# ── Match each target client ───────────────────────────────────
updates = []   # (row, no, name, sched_list, score, reason)

for row in range(2, ws.max_row + 1):
    no = ws.cell(row=row, column=1).value
    if not no or no == 0:
        continue

    legal = ws.cell(row=row, column=2).value or ''
    bn = ws.cell(row=row, column=3).value
    operating = ws.cell(row=row, column=4).value or ''

    # Check manual overrides
    if row in MANUAL_OVERRIDES:
        sched_list = MANUAL_OVERRIDES[row]
        updates.append((row, no, legal[:40], sched_list, 999, 'manual'))
        continue

    # Auto-match: find best source entry
    best_score = 0
    best_src_key = ''

    for src_key, src_sched in source_list:
        score = fuzzy_match(legal, bn, operating, src_key)
        if score > best_score:
            best_score = score
            best_src_key = src_key

    if best_score >= 50 and best_src_key:
        # Get ALL schedules for this company
        all_scheds = company_all_schedules.get(best_src_key, set())
        if all_scheds:
            sched_list = sorted(all_scheds)  # deterministic order
        else:
            # Fallback: normalize the single match
            nv = normalize_schedule(source_map.get(best_src_key, ''))
            sched_list = [nv] if nv else ['NA']
        updates.append((row, no, legal[:40], sched_list, best_score, f'matched: {best_src_key[:30]}'))
    else:
        updates.append((row, no, legal[:40], ['NA'], best_score, 'no match'))

# ── Print summary ──────────────────────────────────────────────
print(f'\nColumn J header: {j_header}')
print(f'If J is None, will add header "Payroll Schedule 2"\n')

single = [u for u in updates if len(u[3]) <= 1]
multi = [u for u in updates if len(u[3]) > 1]
na_count = sum(1 for u in updates if u[3] == ['NA'])

print(f'=== SINGLE SCHEDULE (I only): {len(single) - na_count} ===')
for r, no, name, slist, score, reason in sorted(single, key=lambda x: x[0]):
    if slist != ['NA']:
        print(f'  Row {r:3d} (#{no:>3}) [{score:>3}]: {name[:35]:35s} -> I={slist[0]}')

print(f'\n=== MULTIPLE SCHEDULES (I + J): {len(multi)} ===')
for r, no, name, slist, score, reason in sorted(multi, key=lambda x: x[0]):
    print(f'  Row {r:3d} (#{no:>3}) [{score:>3}]: {name[:35]:35s} -> I={slist[0]}, J={slist[1]}')

print(f'\n=== NO MATCH (I=NA): {na_count} ===')
for r, no, name, slist, score, reason in sorted([u for u in updates if u[3] == ['NA']], key=lambda x: x[0]):
    print(f'  Row {r:3d} (#{no:>3}): {name[:35]:35s} | {reason}')

# ── Apply ──────────────────────────────────────────────────────
confirm = input('\n>>> Apply these updates? (yes/no): ')
if confirm.lower() != 'yes':
    print('Aborted.')
    os.unlink(tmp.name)
    sys.exit(0)

# Add J header if needed
if j_header is None:
    ws.cell(row=1, column=10).value = 'Payroll Schedule 2'

for r, no, name, slist, score, reason in updates:
    ws.cell(row=r, column=9).value = slist[0]
    if len(slist) > 1:
        ws.cell(row=r, column=10).value = slist[1]
    elif ws.cell(row=r, column=10).value is not None:
        # Clear J if previously had value but now single schedule
        pass  # don't clear existing data

wb.save(tmp.name)
wb.close()

# Re-encrypt
with open(tmp.name, 'rb') as decrypted:
    with open(TARGET, 'wb') as encrypted:
        of2 = msoffcrypto.OfficeFile(decrypted)
        of2.load_key(password=PASSWORD)
        of2.encrypt(PASSWORD, encrypted)

os.unlink(tmp.name)
print(f'\nDone! Updated {len(updates)} rows.')
print(f'  Single schedule: {len(single) - na_count}')
print(f'  Multiple (I+J):  {len(multi)}')
print(f'  NA (no match):   {na_count}')
