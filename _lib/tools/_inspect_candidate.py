import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import msoffcrypto, openpyxl
import config.settings as settings

path = r"\\file-server\Shared Folder\4. General - Scan-Notes-Form-PPT\4. Accounting client list\#Business Master Client List_UPDATED_candidate.xlsx"

with open(path, "rb") as f:
    of = msoffcrypto.OfficeFile(f)
    enc = of.is_encrypted()
    print("Encrypted:", enc)
    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    if enc:
        of.load_key(password=settings.CLIENT_MASTER_PASSWORD)
        of.decrypt(tmp); tmp.close()
    else:
        import shutil; tmp.close(); shutil.copyfile(path, tmp.name)

wb = openpyxl.load_workbook(tmp.name, read_only=True, data_only=True)
print("Sheets:", wb.sheetnames)
for sn in wb.sheetnames:
    if sn.strip().lower() in ("master", "Alex's", "mk"):
        ws = wb[sn]
        hdr = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
        print(f"\n[{sn}] headers:", [h for h in hdr if h])
