import openpyxl
from pathlib import Path
from collections import Counter
report_dir = Path(__file__).resolve().parents[2] / "_reports"
path = max(report_dir.glob("master_vs_nocodb_audit_*.xlsx"), key=lambda p: p.stat().st_mtime)
wb = openpyxl.load_workbook(path, read_only=True, data_only=True)

ws = wb['Field Discrepancies']
rows = list(ws.iter_rows(values_only=True))
header = rows[0]
data = rows[1:]
print("Total discrepancy rows:", len(data))

gap = [r for r in data if r[5] and 'migration gap' in r[5]]
print("\n=== 'Master has value, NocoDB blank' (migration gap) by FIELD ===")
for f, c in Counter(r[2] for r in gap).most_common():
    print(f"  {c:4d}  {f}")

upd = [r for r in data if r[5] and 'updated after migration' in r[5]]
print("\n=== 'NocoDB has value, Master blank' (updated after) by FIELD ===")
for f, c in Counter(r[2] for r in upd).most_common():
    print(f"  {c:4d}  {f}")

conf = [r for r in data if r[5] and 'differ' in r[5]]
print("\n=== 'Both differ -> needs review' (all) ===")
for r in conf:
    print(f"  {str(r[0])[:34]:<35} | {str(r[2]):<22} | M={str(r[3])[:20]:<20} | N={str(r[4])[:20]}")

# NocoDB-only companies
ws2 = wb['NocoDB-only companies']
no_only = [r[0] for r in ws2.iter_rows(min_row=2, values_only=True) if r[0]]
print(f"\n=== NocoDB-only companies ({len(no_only)}) ===")
for n in no_only:
    print("  ", n)
