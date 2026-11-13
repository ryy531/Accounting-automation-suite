import openpyxl
from pathlib import Path
from collections import Counter
report_dir = Path(__file__).resolve().parents[2] / "_reports"
path = max(report_dir.glob("master_vs_nocodb_audit_*.xlsx"), key=lambda p: p.stat().st_mtime)
wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
data = list(wb['Field Discrepancies'].iter_rows(min_row=2, values_only=True))

gap = [r for r in data if r[5] and 'migration gap' in r[5]]
# Classify Master value: is it a "junk" placeholder (NA/N/A/-/etc) or a real value?
JUNK = {"na", "n/a", "n.a.", "-", "--", "none", "nil", "x", "0", ""}
junk = [r for r in gap if str(r[3]).strip().lower() in JUNK]
real = [r for r in gap if str(r[3]).strip().lower() not in JUNK]

print(f"Total 'migration gap' rows: {len(gap)}")
print(f"  Master value is junk placeholder (NA/-/none/etc): {len(junk)}")
print(f"  Master value is a REAL value NocoDB is missing:   {len(real)}")
print("\n=== The genuinely-missing ones (real Master value, NocoDB blank) ===")
for r in sorted(real, key=lambda x: x[2]):
    print(f"  {str(r[0])[:38]:<40} {r[2]:<14} Master={r[3]}")

print("\n=== Distinct Master placeholder values seen in the junk group ===")
print(Counter(str(r[3]).strip() for r in junk))
