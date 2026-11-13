import openpyxl
from pathlib import Path
from collections import Counter
report_dir = Path(__file__).resolve().parents[2] / "_reports"
path = max(report_dir.glob("master_vs_nocodb_audit_*.xlsx"), key=lambda p: p.stat().st_mtime)
wb = openpyxl.load_workbook(path, read_only=True, data_only=True)

ws = wb['Field Discrepancies']
rows = list(ws.iter_rows(values_only=True))
data = rows[1:]

# migration-gap rows: which sheet do they come from?
gap = [r for r in data if r[5] and 'migration gap' in r[5]]
print("=== migration-gap rows by (Field, Master Sheet) ===")
for (fld, sheet), c in Counter((r[2], r[1]) for r in gap).most_common():
    print(f"  {c:4d}  {fld:<16} from sheet '{sheet}'")

# Show the GST/PST gap company names + their master value
print("\n=== GST/PST Frequency gaps: company + Master value (NocoDB blank) ===")
for r in gap:
    if r[2] in ("GST Frequency", "PST Frequency"):
        print(f"  [{r[1]:<8}] {str(r[0])[:36]:<38} {r[2]:<16} Master={r[3]}")
