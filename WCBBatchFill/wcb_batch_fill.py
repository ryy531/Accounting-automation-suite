"""
WCB Authorization Letter - Batch Fill Script
=============================================
Reads company info from multiple sources, fills the WCB PDF form template,
and saves one PDF per company.

Data Sources:
1. WCB tracking Sheet3 -> company list, owner name, policy (firm) number, email
2. Company Info Sheets in Business Clients folder -> address, phone

Template: Sample - 2.pdf (XFA + AcroForm hybrid PDF)

Usage: python wcb_batch_fill.py
"""

import os
import sys
import re
import datetime
import traceback

import openpyxl
import pikepdf

sys.stdout.reconfigure(encoding='utf-8')

# ============================================================
# CONFIG
# ============================================================
WCB_TRACKING_PATH = (
    r"//file-server/Shared Folder/4. General - Scan-Notes-Form-PPT"
    r"/3. Task/2026/WCB/WCB tracking 2025.03 (03.17.2026) SS.xlsx"
)
TEMPLATE_PDF_PATH = (
    r"//file-server/Shared Folder/4. General - Scan-Notes-Form-PPT"
    r"/WCB/Sample - 2.pdf"
)
OUTPUT_DIR = (
    r"//file-server/Shared Folder/4. General - Scan-Notes-Form-PPT"
    r"/WCB"
)
BUSINESS_CLIENTS_BASE = (
    r"//file-server/Shared Folder/1. Accounting - Business Clients"
)

# ExampleCo (our) info - stays constant across all forms
ExampleCo_PHONE_PLAIN = "555-000-0000"

# Today's date for Section F
TODAY_STR = datetime.date.today().strftime("%m/%d/%Y")


# ============================================================
# STEP 1: Read company list from WCB tracking
# ============================================================
def read_wcb_tracking():
    """Read Sheet3 of WCB tracking file. Returns list of dicts."""
    print("Reading WCB tracking file...")
    wb = openpyxl.load_workbook(WCB_TRACKING_PATH, read_only=True, data_only=True)
    ws = wb["Sheet3"]

    companies = []
    for row in ws.iter_rows(min_row=2, max_row=200, max_col=15, values_only=True):
        name = row[1] if len(row) > 1 else None       # B: ISC Certified Name
        contract = row[12] if len(row) > 12 else None  # M: Contract Services
        if not name or not contract:
            continue
        name = str(name).strip()
        contract = str(contract).strip()
        if not contract:
            continue

        companies.append({
            "name": name,
            "operating_name": str(row[2]).strip() if row[2] else "",
            "status": str(row[3]).strip() if row[3] else "",
            "policy_number": str(row[5]).strip() if row[5] else "",
            "owner_name": str(row[8]).strip() if row[8] else "",
            "owner_phone": str(row[9]).strip() if row[9] else "",
            "owner_email": str(row[10]).strip() if row[10] else "",
            "contract": contract,
        })

    wb.close()
    print(f"  Found {len(companies)} companies with contract services")
    return companies


# ============================================================
# STEP 2: Find Company Info Sheet for each company
# ============================================================
def find_company_info_folder(company_name):
    """Search Business Clients folder for a matching Company Info Sheet."""
    letter = company_name[0].upper()
    if letter.isdigit():
        letter_folders = ["#Number Companies"]
    else:
        letter_folders = [letter]

    for sub in letter_folders:
        folder = os.path.join(BUSINESS_CLIENTS_BASE, sub)
        if not os.path.isdir(folder):
            continue

        search_key = company_name.upper()[:8]
        candidates = [d for d in os.listdir(folder) if search_key in d.upper()]
        if not candidates:
            search_key = company_name.upper()[:5]
            candidates = [d for d in os.listdir(folder) if search_key in d.upper()]

        for candidate in candidates:
            base_path = os.path.join(folder, candidate)
            for info_dir_name in ["Company info", "Company Information"]:
                info_dir = os.path.join(base_path, info_dir_name)
                if os.path.isdir(info_dir):
                    for f in os.listdir(info_dir):
                        if ("info sheet" in f.lower() or "company info" in f.lower()) and f.endswith((".xlsx", ".xls")):
                            return os.path.join(info_dir, f)
            for f in os.listdir(base_path):
                if "info sheet" in f.lower() and f.endswith((".xlsx", ".xls")):
                    return os.path.join(base_path, f)
    return None


def read_company_info_sheet(filepath):
    """Read a Company Info Sheet and extract address, phone, etc."""
    try:
        wb = openpyxl.load_workbook(filepath, data_only=True)
        ws = wb.active
    except Exception as e:
        print(f"    Error opening {filepath}: {e}")
        return None

    info = {
        "address": "",
        "city": "",
        "province": "SK",
        "postal_code": "",
        "phone": "",
        "wcb_firm_number": "",
    }

    for row in ws.iter_rows(min_row=1, max_row=35, values_only=False):
        vals = {cell.column_letter: cell.value for cell in row if cell.value is not None}
        if not vals:
            continue
        a_val = str(vals.get("A", "")).strip().lower()
        b_val = vals.get("B", "")

        if "physical address" in a_val and b_val:
            addr = str(b_val).strip()
            parts = [p.strip() for p in addr.split(",")]
            if len(parts) >= 1:
                info["address"] = parts[0]
            if len(parts) >= 2:
                info["city"] = parts[1]
            if len(parts) >= 3:
                rest = parts[2].strip()
                # Try to split province and postal from the same part
                postal_match = re.search(r"[A-Z]\d[A-Z]\s*\d[A-Z]\d", rest.upper())
                if postal_match:
                    info["postal_code"] = postal_match.group()
                    prov_part = rest[:postal_match.start()].strip().upper()
                    prov_part = prov_part.replace("SASKATCHEWAN", "SK").strip()
                    if prov_part:
                        info["province"] = prov_part
                else:
                    info["province"] = rest.upper().replace("SASKATCHEWAN", "SK").strip()
            postal_match = re.search(r"[A-Z]\d[A-Z]\s*\d[A-Z]\d", addr.upper())
            if postal_match and not info["postal_code"]:
                info["postal_code"] = postal_match.group()

        elif a_val == "address" and b_val and not info["address"]:
            info["address"] = str(b_val).strip()

        elif a_val == "city" and b_val:
            if not info["city"]:
                info["city"] = str(b_val).strip()

        elif a_val == "province" and b_val:
            info["province"] = str(b_val).strip()

        elif "post code" in a_val or "postal" in a_val:
            if b_val and not info["postal_code"]:
                info["postal_code"] = str(b_val).strip()

        elif "mailing address" in a_val and b_val and not info["address"]:
            addr = str(b_val).strip()
            parts = [p.strip() for p in addr.split(",")]
            if len(parts) >= 1:
                info["address"] = parts[0]
            if len(parts) >= 2:
                info["city"] = parts[1]
            postal_match = re.search(r"[A-Z]\d[A-Z]\s*\d[A-Z]\d", addr.upper())
            if postal_match:
                info["postal_code"] = postal_match.group()

        elif "owner phone" in a_val and b_val:
            info["phone"] = str(b_val).strip()

        elif "wcb" in a_val and "firm" in a_val and b_val:
            info["wcb_firm_number"] = str(b_val).strip()

    wb.close()
    return info


# ============================================================
# STEP 3: Fill PDF form
# ============================================================
def fill_pdf(company_info, output_path):
    """Fill the Sample-2.pdf template with company data and save."""
    pdf = pikepdf.open(TEMPLATE_PDF_PATH)
    root = pdf.Root
    af = root["/AcroForm"]
    fields = af["/Fields"]
    main_field = fields[0]

    # Collect all leaf fields with full path
    field_list = []

    def collect_leaf_fields(obj, path=""):
        if isinstance(obj, pikepdf.Dictionary):
            name = str(obj.get("/T", ""))
            ftype = str(obj.get("/FT", ""))
            kids = obj.get("/Kids")
            current = f"{path}/{name}"
            if ftype and ftype in ("/Tx", "/Btn"):
                field_list.append((current, obj))
            if kids:
                for kid in kids:
                    collect_leaf_fields(kid, current)

    collect_leaf_fields(main_field)

    # Update AcroForm fields based on path position
    # Helper: extract #subform[N] index from the part after a section name
    def get_subform_index(path, section_name):
        """Get the index number from #subform[N] immediately after the section."""
        part = path.split(section_name)[1] if section_name in path else ""
        m = re.search(r'#subform\[(\d+)\]', part)
        return int(m.group(1)) if m else -1

    for path, field in field_list:
        clean = re.sub(r'\[\d+\]', '', path)

        # Section A-1: company name (subform[0]) + firm number (subform[1])
        if "SectionA-1" in clean and "TextField3" in clean:
            sf_idx = get_subform_index(path, "SectionA-1")
            if sf_idx == 0:
                field["/V"] = pikepdf.String(company_info.get("name", ""))
            elif sf_idx == 1:
                field["/V"] = pikepdf.String(company_info.get("policy_number", ""))

        # Section A-2: street (subform[0]), city (subform[1]), province (subform[2]), postal (subform[3])
        elif "SectionA-2" in clean and "Street" in clean:
            field["/V"] = pikepdf.String(company_info.get("address", ""))

        elif "SectionA-2" in clean and "TextField3" in clean:
            sf_idx = get_subform_index(path, "SectionA-2")
            if sf_idx == 1:
                field["/V"] = pikepdf.String(company_info.get("city", ""))
            elif sf_idx == 2:
                field["/V"] = pikepdf.String(company_info.get("province", "SK"))
            elif sf_idx == 3:
                field["/V"] = pikepdf.String(company_info.get("postal_code", ""))

        # Section A-3: phone (subform[0]) + email (subform[4])
        elif "SectionA-3" in clean and "EmpPhone" in clean:
            field["/V"] = pikepdf.String(company_info.get("phone", ""))

        elif "SectionA-3" in clean and "TextField3" in clean:
            field["/V"] = pikepdf.String(company_info.get("email", ""))

        # Section D-1: admin email
        elif "SectionD-1" in clean and "TextField3" in clean:
            field["/V"] = pikepdf.String("infosk@example-firm.com ")

        # Section D-1: Yes checkbox
        elif "SectionD-1" in clean and "CheckBox1" in clean:
            if "[0]" in path:
                field["/V"] = pikepdf.Name("/true")
                field["/AS"] = pikepdf.Name("/true")

        # Section F-1: signer name (subform[0]) + title (subform[1])
        elif "SectionF-1" in clean and "SubFullName" in clean:
            field["/V"] = pikepdf.String(company_info.get("owner_name", "").upper())

        elif "SectionF-1" in clean and "TextField3" in clean:
            field["/V"] = pikepdf.String("Director")

        # Section F-2: phone + date
        elif "SectionF-2" in clean and "TextField3" in clean:
            field["/V"] = pikepdf.String(ExampleCo_PHONE_PLAIN)

        elif "SectionF-2" in clean and "DateField1" in clean:
            field["/V"] = pikepdf.String(TODAY_STR)

    # ---- Update XFA datasets ----
    xfa = af["/XFA"]
    for i in range(0, len(xfa), 2):
        key = str(xfa[i])
        val = xfa[i + 1]
        if isinstance(val, pikepdf.Stream) and key == "datasets":
            xml_data = val.read_bytes().decode("utf-8")
            xml_data = _update_xfa_data(xml_data, company_info)
            # Replace stream with new one (pikepdf 10.x API)
            new_stream = pikepdf.Stream(pdf, xml_data.encode("utf-8"))
            xfa[i + 1] = new_stream

    pdf.save(output_path)
    pdf.close()


def _update_xfa_data(xml_data, company_info):
    """Update the XFA datasets XML with company info."""
    name = company_info.get("name", "")
    firm_number = company_info.get("policy_number", "")
    address = company_info.get("address", "")
    city = company_info.get("city", "")
    province = company_info.get("province", "SK")
    postal = company_info.get("postal_code", "")
    phone = company_info.get("phone", "")
    email = company_info.get("email", "")
    owner = company_info.get("owner_name", "")

    # Parse phone
    phone_clean = re.sub(r'[^\d]', '', phone)
    if len(phone_clean) >= 10:
        area = phone_clean[:3]
        number = phone_clean[3:]
    else:
        area = ""
        number = phone

    # Employer Address (within template:EmployerAddress context)
    xml_data = re.sub(
        r'(<template:EmployerAddress>.*?<doctemp:StreetAddress>)(.*?)(</doctemp:StreetAddress>.*?</template:EmployerAddress>)',
        rf'\g<1>{address}\g<3>', xml_data, flags=re.DOTALL)
    xml_data = re.sub(
        r'(<template:EmployerAddress>.*?<doctemp:City>)(.*?)(</doctemp:City>.*?</template:EmployerAddress>)',
        rf'\g<1>{city}\g<3>', xml_data, flags=re.DOTALL)
    xml_data = re.sub(
        r'(<template:EmployerAddress>.*?<doctemp:Province>)(.*?)(</doctemp:Province>.*?</template:EmployerAddress>)',
        rf'\g<1>{province}\g<3>', xml_data, flags=re.DOTALL)
    xml_data = re.sub(
        r'(<template:EmployerAddress>.*?<doctemp:PostalCode>)(.*?)(</doctemp:PostalCode>.*?</template:EmployerAddress>)',
        rf'\g<1>{postal}\g<3>', xml_data, flags=re.DOTALL)

    # Employer phone
    xml_data = re.sub(
        r'(<template:EmployerPhone>.*?<cdt:AreaCode>)(.*?)(</cdt:AreaCode>.*?</template:EmployerPhone>)',
        rf'\g<1>{area}\g<3>', xml_data, flags=re.DOTALL)
    xml_data = re.sub(
        r'(<template:EmployerPhone>.*?<cdt:Number>)(.*?)(</cdt:Number>.*?</template:EmployerPhone>)',
        rf'\g<1>{number}\g<3>', xml_data, flags=re.DOTALL)

    # Employer fax (clear)
    xml_data = re.sub(
        r'(<template:EmployerFax>.*?<cdt:AreaCode>)(.*?)(</cdt:AreaCode>.*?</template:EmployerFax>)',
        r'\g<1>\g<3>', xml_data, flags=re.DOTALL)
    xml_data = re.sub(
        r'(<template:EmployerFax>.*?<cdt:Number>)(.*?)(</cdt:Number>.*?</template:EmployerFax>)',
        r'\g<1>\g<3>', xml_data, flags=re.DOTALL)

    # Email
    xml_data = re.sub(
        r'(<template:ClientEmailAddress>)(.*?)(</template:ClientEmailAddress>)',
        rf'\g<1>{email}\g<3>', xml_data)

    # Firm name and number
    xml_data = re.sub(
        r'(<template:FirmName>)(.*?)(</template:FirmName>)',
        rf'\g<1>{name}\g<3>', xml_data)
    xml_data = re.sub(
        r'(<template:FirmNumber>)(.*?)(</template:FirmNumber>)',
        rf'\g<1>{firm_number}\g<3>', xml_data)

    # Declaration info (Section F) - owner name
    owner_parts = owner.split()
    first_name = " ".join(owner_parts[:-1]) if len(owner_parts) > 1 else owner
    last_name = owner_parts[-1] if len(owner_parts) > 1 else ""

    xml_data = re.sub(
        r'(<template:DeclarationInfo>.*?<cdt:FirstName>)(.*?)(</cdt:FirstName>.*?</template:DeclarationInfo>)',
        rf'\g<1>{first_name}\g<3>', xml_data, flags=re.DOTALL)
    xml_data = re.sub(
        r'(<template:DeclarationInfo>.*?<cdt:LastName>)(.*?)(</cdt:LastName>.*?</template:DeclarationInfo>)',
        rf'\g<1>{last_name}\g<3>', xml_data, flags=re.DOTALL)
    xml_data = re.sub(
        r'(<template:DeclarationInfo>.*?<cdt:Title>)(.*?)(</cdt:Title>.*?</template:DeclarationInfo>)',
        r'\g<1>Director\g<3>', xml_data, flags=re.DOTALL)

    # Declaration phone
    adv_phone = re.sub(r'[^\d]', '', "(306) 952-0922")
    decl_area = adv_phone[:3]
    decl_number = adv_phone[3:]
    xml_data = re.sub(
        r'(<template:DeclarationPhone>.*?<cdt:AreaCode>)(.*?)(</cdt:AreaCode>.*?</template:DeclarationPhone>)',
        rf'\g<1>{decl_area}\g<3>', xml_data, flags=re.DOTALL)
    xml_data = re.sub(
        r'(<template:DeclarationPhone>.*?<cdt:Number>)(.*?)(</cdt:Number>.*?</template:DeclarationPhone>)',
        rf'\g<1>{decl_number}\g<3>', xml_data, flags=re.DOTALL)

    # Signature
    xml_data = re.sub(
        r'(<doctemp:Signature>.*?<doctemp:FullName>)(.*?)(</doctemp:FullName>.*?</doctemp:Signature>)',
        rf'\g<1>{owner}\g<3>', xml_data, flags=re.DOTALL)
    xml_data = re.sub(
        r'(<doctemp:Signature>.*?<doctemp:PositionTitle>)(.*?)(</doctemp:PositionTitle>.*?</doctemp:Signature>)',
        r'\g<1>Director\g<3>', xml_data, flags=re.DOTALL)

    # Date
    xml_data = re.sub(
        r'(<doctemp:LetterRequestDate>)(.*?)(</doctemp:LetterRequestDate>)',
        rf'\g<1>{TODAY_STR}\g<3>', xml_data)

    return xml_data


# ============================================================
# MAIN
# ============================================================
def main():
    print("=" * 60)
    print("WCB Authorization Letter - Batch Fill")
    print("=" * 60)

    # Step 1: Read company list
    companies = read_wcb_tracking()
    print()

    # Step 2: Enrich with company info from Business Clients folders
    print("Looking up company info sheets...")
    skipped = []
    enriched = []

    for co in companies:
        name = co["name"]
        print(f"  {name}...", end=" ")

        info_path = find_company_info_folder(name)
        if info_path:
            info = read_company_info_sheet(info_path)
            if info:
                merged = {**co}
                merged["address"] = info["address"] or ""
                merged["city"] = info["city"] or ""
                merged["province"] = info["province"] or "SK"
                merged["postal_code"] = info["postal_code"] or ""
                merged["phone"] = co.get("owner_phone") or info["phone"] or ""
                merged["email"] = co.get("owner_email") or ""

                if not merged["policy_number"] and info.get("wcb_firm_number"):
                    merged["policy_number"] = info["wcb_firm_number"]

                enriched.append(merged)
                print(f"OK (addr: {merged['address'][:30]}...)")
            else:
                print("SKIP (could not read info)")
                skipped.append(name)
        else:
            print("SKIP (no info folder found)")
            skipped.append(name)

    print(f"\n  Ready to fill: {len(enriched)} companies")
    print(f"  Skipped: {len(skipped)} companies")
    if skipped:
        for s in skipped:
            print(f"    - {s}")
    print()

    # Step 3: Fill PDFs
    print("Filling PDF forms...")
    success = 0
    errors = []

    for co in enriched:
        name = co["name"]
        safe_name = re.sub(r'[<>:"/\\|?*]', '', name).strip()
        output_path = os.path.join(OUTPUT_DIR, f"{safe_name} - WCB.pdf")

        try:
            fill_pdf(co, output_path)
            print(f"  OK: {safe_name}")
            success += 1
        except Exception as e:
            print(f"  ERROR: {safe_name} - {e}")
            traceback.print_exc()
            errors.append((name, str(e)))

    # Summary
    print()
    print("=" * 60)
    print(f"Done! {success} PDFs created in:\n  {OUTPUT_DIR}")
    if errors:
        print(f"\nErrors ({len(errors)}):")
        for name, err in errors:
            print(f"  {name}: {err}")
    if skipped:
        print(f"\nSkipped ({len(skipped)}):")
        for s in skipped:
            print(f"  {s}")


if __name__ == "__main__":
    main()
