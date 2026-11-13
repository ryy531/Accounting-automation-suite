import openpyxl, glob, os
from pathlib import Path
report_dir = Path(__file__).resolve().parents[2] / "_reports"
files = list(report_dir.glob("master_vs_nocodb_audit_*.xlsx"))
path = max(files, key=lambda p: p.stat().st_mtime)
print('FILE:', path.name)
wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
print('SHEETS:', wb.sheetnames)
print('\n=== Summary ===')
for row in wb['Summary'].iter_rows(values_only=True):
    print(row)
