"""
Fix #Payroll 2026.xlsx: restore dropdown in column I and add dropdown to column J.
Uses list-based data validation referencing the Payroll Schedule sheet.
"""
import msoffcrypto, openpyxl, tempfile, os, sys, io
from openpyxl.worksheet.datavalidation import DataValidation
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

TARGET = '//file-server/Shared Folder/4. General - Scan-Notes-Form-PPT/4. Accounting client list/#Payroll 2026.xlsx'
PASSWORD = '0000000000'

# Decrypt
with open(TARGET, 'rb') as f:
    of = msoffcrypto.OfficeFile(f)
    of.load_key(password=PASSWORD)
    tmp = tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False)
    of.decrypt(tmp)
    tmp.close()

wb = openpyxl.load_workbook(tmp.name)
ws = wb['Master']

# Get dropdown options from Payroll Schedule sheet
ws_ps = wb['Payroll Schedule']
options = []
for row in range(1, ws_ps.max_row + 1):
    val = ws_ps.cell(row=row, column=2).value
    if val:
        options.append(val.strip())
print(f'Dropdown options: {options}')

# Create data validation using list formula referencing Payroll Schedule sheet
# Formula: 'Payroll Schedule'!$B$1:$B$5
dv_formula = "'Payroll Schedule'!$B$1:$B$5"

# Column I: restore dropdown (rows 2-222)
dv_i = DataValidation(
    type='list',
    formula1=f"'Payroll Schedule'!$B$1:$B$5",
    allow_blank=True,
    showDropDown=False,
)
dv_i.error = 'Please select a valid Payroll Schedule'
dv_i.errorTitle = 'Invalid Entry'
dv_i.sqref = 'I2:I222'
ws.add_data_validation(dv_i)

# Column J: add same dropdown
dv_j = DataValidation(
    type='list',
    formula1=f"'Payroll Schedule'!$B$1:$B$5",
    allow_blank=True,
    showDropDown=False,
)
dv_j.error = 'Please select a valid Payroll Schedule'
dv_j.errorTitle = 'Invalid Entry'
dv_j.sqref = 'J2:J222'
ws.add_data_validation(dv_j)

print(f'Added dropdown validation for I2:I222 and J2:J222')

# Save
wb.save(tmp.name)
wb.close()

# Re-encrypt
with open(tmp.name, 'rb') as decrypted:
    with open(TARGET, 'wb') as encrypted:
        of2 = msoffcrypto.OfficeFile(decrypted)
        of2.load_key(password=PASSWORD)
        of2.encrypt(PASSWORD, encrypted)

os.unlink(tmp.name)
print('Done! Dropdowns restored for columns I and J.')
