import openpyxl
from pathlib import Path
from collections import Counter

report_dir = Path(__file__).resolve().parents[2] / "_reports"
files = sorted(report_dir.glob("master_vs_nocodb_audit_*.xlsx"), key=lambda p: p.stat().st_mtime)
old_path, new_path = files[-2], files[-1]   # 230944 = old master, 232331 = candidate

JUNK = {"na", "n/a", "n.a.", "-", "--", "none", "nil", "x", ""}

def load(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    summ = {r[0]: r[1] for r in wb['Summary'].iter_rows(min_row=2, values_only=True) if r[0]}
    disc = list(wb['Field Discrepancies'].iter_rows(min_row=2, values_only=True))
    no_only = [r[0] for r in wb['NocoDB-only companies'].iter_rows(min_row=2, values_only=True) if r[0]]
    m_only = [r[0] for r in wb['Master-only companies'].iter_rows(min_row=2, values_only=True) if r[0]]
    return summ, disc, no_only, m_only

for label, path in (("OLD master", old_path), ("UPDATED candidate", new_path)):
    summ, disc, no_only, m_only = load(path)
    print(f"\n========== {label}  ({path.name}) ==========")
    for k, v in summ.items():
        print(f"  {v:>5}  {k}")
    # real gaps (non-junk) and conflicts within the discrepancy sheet
    gaps = [r for r in disc if r[5] and 'migration gap' in r[5]]
    conf = [r for r in disc if r[5] and 'differ' in r[5]]
    print(f"  --> Field Discrepancies sheet rows: {len(disc)}  (real gaps={len(gaps)}, conflicts={len(conf)})")

# Detailed diff of real gaps + conflicts for the candidate
print("\n\n########## UPDATED candidate: real gaps (Master has value, NocoDB blank) ##########")
_, disc, no_only, m_only = load(new_path)
for r in [x for x in disc if x[5] and 'migration gap' in x[5]]:
    print(f"  {str(r[0])[:38]:<40} {r[2]:<14} Master={r[3]}")
print("\n########## UPDATED candidate: conflicts (both differ) ##########")
for r in [x for x in disc if x[5] and 'differ' in x[5]]:
    print(f"  {str(r[0])[:34]:<36} {r[2]:<22} M={str(r[3])[:20]:<20} N={str(r[4])[:20]}")
print(f"\n########## Master-only companies (in candidate, not NocoDB): {len(m_only)} ##########")
for n in m_only:
    print("  ", n)
print(f"\n########## NocoDB-only (in NocoDB, not candidate): {len(no_only)} ##########")
for n in no_only:
    print("  ", n)
