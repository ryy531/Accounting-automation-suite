"""
ISC Corporate Registry - Accessibility Check
For each SK entity in the spreadsheet, checks whether the firm's access code
is accepted for "Change of Directors/Officers" (or fallback form).
Results (Yes / No / Skip) are written back to the Access Code column.

Account.txt format:
    username=xxx
    password=xxx
    accesscode=xxx
    file_password=xxx   (optional – only if Excel is password-protected)
"""

import asyncio
import io
import json
import os
import sys

import warnings

import msoffcrypto
import openpyxl
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

# ── Paths ─────────────────────────────────────────────────────────────────────
ACCOUNT_FILE    = r"\\file-server\Shared Folder\Scripts\AccessibilityCheck\Account.txt"
EXCEL_FILE      = r"\\file-server\Shared Folder\4. General - Scan-Notes-Form-PPT\4. Accounting client list\Annual return 20260401.xlsx"
CHECKPOINT_FILE = r"\\file-server\Shared Folder\Scripts\AccessibilityCheck\checkpoint.json"
SITE_URL        = "https://corporateregistry.isc.ca"

# ── Column header keywords (case-insensitive partial match) ───────────────────
ENTITY_HEADER       = "entity number"
ACCESS_HEADER       = "access code"
JURISDICTION_HEADER = "incorporation jurisdiction"
JURISDICTION_FILTER = "SK"

# ── Form options in priority order ────────────────────────────────────────────
FORM_OPTIONS = [
    "CHANGE OF DIRECTORS / OFFICERS",  # primary
    "CHANGE NATURE OF BUSINESS",        # fallback
]


# ── Config helpers ────────────────────────────────────────────────────────────

def read_account() -> dict:
    """Read key=value pairs from Account.txt (keys lowercased)."""
    creds = {}
    with open(ACCOUNT_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                key, _, val = line.partition("=")
                creds[key.strip().lower()] = val.strip()
    return creds


# ── Excel helpers ─────────────────────────────────────────────────────────────

def open_workbook(path: str, file_password: str | None):
    """Open workbook, decrypting with msoffcrypto if password-protected."""
    with open(path, "rb") as f:
        raw = f.read()
    try:
        office_file = msoffcrypto.OfficeFile(io.BytesIO(raw))
        if office_file.is_encrypted():
            if not file_password:
                sys.exit(
                    "ERROR: Excel file is encrypted.\n"
                    "Add  file_password=YOUR_PASSWORD  to Account.txt"
                )
            decrypted = io.BytesIO()
            office_file.load_key(password=file_password)
            office_file.decrypt(decrypted)
            decrypted.seek(0)
            return openpyxl.load_workbook(decrypted), True
    except Exception:
        pass
    return openpyxl.load_workbook(path), False


def find_column(ws, keyword: str) -> int:
    """Return 1-based column index whose header contains keyword."""
    kw = keyword.lower()
    for cell in ws[1]:
        if cell.value and kw in str(cell.value).lower():
            return cell.column
    raise ValueError(f"Cannot find column with header containing '{keyword}'")


def collect_entities(ws, entity_col: int, jurisdiction_col: int) -> list[dict]:
    """Return rows where Incorporation Jurisdiction == SK and entity number is set."""
    rows = []
    for row_idx in range(2, ws.max_row + 1):
        jurisdiction = ws.cell(row=row_idx, column=jurisdiction_col).value
        entity_num   = ws.cell(row=row_idx, column=entity_col).value
        if (
            jurisdiction
            and str(jurisdiction).strip().upper() == JURISDICTION_FILTER
            and entity_num
        ):
            # Normalise entity number: strip whitespace, take first line only,
            # and drop trailing .0 if Excel stored it as a float (e.g. 101234569.0)
            entity_str = str(entity_num).split("\n")[0].strip()
            if entity_str.endswith(".0") and entity_str[:-2].isdigit():
                entity_str = entity_str[:-2]
            if not entity_str or entity_str.upper() in ("NA", "N/A", "N.A.", "-", "NONE"):
                continue
            rows.append({"row": row_idx, "entity": entity_str})
    return rows


def save_workbook(wb, path: str, was_encrypted: bool, file_password: str | None):
    if was_encrypted and file_password:
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        enc_buf = io.BytesIO()
        msoffcrypto.OfficeFile(buf).encrypt(file_password, enc_buf)
        with open(path, "wb") as f:
            f.write(enc_buf.getvalue())
    else:
        wb.save(path)


# ── Checkpoint helpers ────────────────────────────────────────────────────────

def load_checkpoint() -> int | None:
    """Return the index (0-based) of the last completed entity, or None."""
    if not os.path.exists(CHECKPOINT_FILE):
        return None
    try:
        with open(CHECKPOINT_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("excel_file") == EXCEL_FILE:
            return data.get("last_index")
    except Exception:
        pass
    return None

def save_checkpoint(index: int):
    """Save the 0-based index of the last completed entity."""
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump({"excel_file": EXCEL_FILE, "last_index": index}, f)

def clear_checkpoint():
    if os.path.exists(CHECKPOINT_FILE):
        os.remove(CHECKPOINT_FILE)


# ── Browser helpers ───────────────────────────────────────────────────────────

async def login(page, creds: dict):
    """Log in with username + password."""
    await page.goto(SITE_URL, wait_until="domcontentloaded")

    inputs = await page.locator("input[type='text'], input[type='password']").all()
    if len(inputs) >= 2:
        await inputs[0].fill(creds.get("username", ""))
        await inputs[1].fill(creds.get("password", ""))
    elif len(inputs) == 1:
        # Single-field login (access code only)
        await inputs[0].fill(creds.get("username", ""))

    await page.get_by_role("button", name="login", exact=False).first.click()
    await page.wait_for_load_state("domcontentloaded")


async def go_home(page):
    """Return to the main dashboard."""
    try:
        await page.get_by_role("link", name="home", exact=False).first.click(timeout=5000)
        await page.wait_for_load_state("domcontentloaded")
    except PlaywrightTimeoutError:
        await page.goto(SITE_URL, wait_until="domcontentloaded")


async def enter_entity_number(page, entity_number: str) -> bool:
    """
    Type entity number into the Update Entity field, then click the
    single dropdown option that appears.
    Returns True on success.
    """
    # Locate the Update Entity input by its known id
    try:
        field = page.locator("#EntityNumber")
        await field.wait_for(state="visible", timeout=5000)
        await field.fill("")   # force-clear any leftover value
        await field.click()
        await field.press_sequentially(entity_number, delay=80)  # fires keyboard events → triggers jQuery UI autocomplete
    except PlaywrightTimeoutError:
        print(f"\n  [!] Could not find Update Entity input (#EntityNumber)")
        return False

    # Wait for the jQuery UI autocomplete dropdown and click the first option
    try:
        await page.wait_for_timeout(1500)
        dropdown_option = page.locator(".ui-autocomplete li").first
        await dropdown_option.wait_for(state="visible", timeout=5000)
        await dropdown_option.click()
    except PlaywrightTimeoutError:
        await field.press("Enter")

    # Wait until the entity's File Form page is actually loaded
    try:
        await page.wait_for_selector("text=File Form", timeout=10000)
    except PlaywrightTimeoutError:
        print(f"\n  [!] Entity page did not load for {entity_number}")
        return False

    return True


async def click_form_option(page) -> str | None:
    """
    Try to click CHANGE OF DIRECTORS / OFFICERS, then CHANGE NATURE OF BUSINESS.
    Returns the form name clicked, or None if neither is available.
    """
    for form_text in FORM_OPTIONS:
        try:
            link = page.get_by_text(form_text, exact=False).first
            if await link.is_visible(timeout=3000):
                await link.click()
                await page.wait_for_load_state("domcontentloaded")
                return form_text
        except PlaywrightTimeoutError:
            continue
    return None


async def handle_access_code_dialog(page, access_code: str) -> str:
    """
    When 'Check Access Code' dialog is present:
      - Enter the firm's access code and click Continue.
      - 'Wrong Access Code' banner  → return 'No'  + click Exit
      - Navigated away (success)    → return 'Yes' + click Home
    """
    # Fill in the access code — find the input inside the dialog
    try:
        dialog = page.locator("div[role='dialog']").first
        code_input = dialog.locator("input[type='text'], input[type='password']").first
        await code_input.wait_for(state="visible", timeout=5000)
        await code_input.fill(access_code, timeout=3000)
    except PlaywrightTimeoutError:
        pass

    # Click Continue
    try:
        await page.get_by_role("button", name="Continue", exact=False).click(timeout=5000)
        await page.wait_for_timeout(1500)
    except PlaywrightTimeoutError:
        pass

    # Check for Wrong Access Code error
    wrong_banner = page.get_by_text("Wrong Access Code", exact=False)
    if await wrong_banner.count():
        # Click Exit to go back
        try:
            await page.get_by_role("button", name="Exit", exact=False).click(timeout=5000)
            await page.wait_for_load_state("domcontentloaded")
        except PlaywrightTimeoutError:
            await go_home(page)
        return "No"

    # No error → access granted
    await go_home(page)
    return "Yes"


async def check_entity(page, entity_number: str, access_code: str) -> str:
    """
    Full check flow for one entity.
    Returns: 'Yes' | 'No' | 'Skip' | 'Error'
    """
    # Step 1: Enter entity number + click dropdown option
    ok = await enter_entity_number(page, entity_number)
    if not ok:
        return "Error"

    # Step 2: Click the appropriate form option
    print(f"\n  [DEBUG] URL after enter: {page.url}")
    form_clicked = await click_form_option(page)
    if form_clicked is None:
        # Neither form is available
        await go_home(page)
        return "Skip"

    # Step 3: Wait to see what page appeared
    await page.wait_for_timeout(1000)

    # Case A: "Check Access Code" dialog → enter code → judge
    if await page.get_by_text("Check Access Code", exact=False).count():
        return await handle_access_code_dialog(page, access_code)

    # Case B: No access code dialog appeared — we cannot verify the code
    await go_home(page)
    return "Skip"


# ── Progress display ──────────────────────────────────────────────────────────

RESULT_LABEL = {"Yes": "Yes  ", "No": "No   ", "Skip": "Skip ", "Error": "ERROR"}
RESULT_COLOR = {"Yes": "\033[92m", "No": "\033[91m", "Skip": "\033[93m", "Error": "\033[91m"}
RESET = "\033[0m"
BOLD  = "\033[1m"


def print_progress(i: int, total: int, entity: str, result: str | None = None):
    """Print a single progress line with bar and result."""
    bar_width = 30
    filled = int(bar_width * i / total)
    bar = "█" * filled + "░" * (bar_width - filled)
    pct = int(100 * i / total)

    if result is None:
        label = "checking…"
        color = ""
    else:
        label = RESULT_COLOR.get(result, "") + RESULT_LABEL.get(result, result) + RESET

    # \r rewrites the same line; \n on completion
    end_char = "\n" if result is not None else ""
    print(
        f"\r  [{bar}] {pct:3d}%  {i:>{len(str(total))}}/{total}"
        f"  Entity {entity:<12}  {label}",
        end=end_char,
        flush=True,
    )


def print_summary(results: list[str]):
    total = len(results)
    yes   = results.count("Yes")
    no    = results.count("No")
    skip  = results.count("Skip")
    error = results.count("Error")

    print(f"\n{'─' * 55}")
    print(f"  Total checked : {total}")
    print(f"  {RESULT_COLOR['Yes']}Yes   : {yes}{RESET}")
    print(f"  {RESULT_COLOR['No']}No    : {no}{RESET}")
    if skip:
        print(f"  {RESULT_COLOR['Skip']}Skip  : {skip}{RESET}")
    if error:
        print(f"  {RESULT_COLOR['Error']}Error : {error}{RESET}")
    print(f"{'─' * 55}")


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    # 1. Credentials
    if not os.path.exists(ACCOUNT_FILE):
        sys.exit(f"ERROR: Account.txt not found:\n  {ACCOUNT_FILE}")
    creds = read_account()
    access_code = creds.get("accesscode", "")
    if not access_code:
        sys.exit("ERROR: 'accesscode' not found in Account.txt")

    # 2. Workbook — check file is not locked before doing anything
    print(f"\n{BOLD}Step 1/3  Loading workbook…{RESET}")
    try:
        with open(EXCEL_FILE, "r+b"):
            pass
    except PermissionError:
        sys.exit(
            f"\n  ERROR: The Excel file is currently open in another program.\n"
            f"  Please close it and run again:\n  {EXCEL_FILE}"
        )
    wb, was_encrypted = open_workbook(EXCEL_FILE, creds.get("file_password"))
    ws = wb.active

    entity_col       = find_column(ws, ENTITY_HEADER)
    access_col       = find_column(ws, ACCESS_HEADER)
    jurisdiction_col = find_column(ws, JURISDICTION_HEADER)

    entities = collect_entities(ws, entity_col, jurisdiction_col)
    print(f"  Found {len(entities)} SK entities to check.")

    if not entities:
        print("  Nothing to process.")
        return

    # ── Resume prompt ──────────────────────────────────────────────────────────
    start_index = 0
    checkpoint = load_checkpoint()
    if checkpoint is not None and checkpoint + 1 < len(entities):
        resume_entity = entities[checkpoint + 1]["entity"]
        print(f"\n  Checkpoint found: last completed index {checkpoint} "
              f"({entities[checkpoint]['entity']}).")
        answer = input(f"  Resume from next entity ({resume_entity})? [Y/n]: ").strip().lower()
        if answer in ("", "y", "yes"):
            start_index = checkpoint + 1
            print(f"  Resuming from index {start_index}.\n")
        else:
            clear_checkpoint()
            print("  Starting from the beginning.\n")

    # 3. Browser
    print(f"\n{BOLD}Step 2/3  Browser automation…{RESET}")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page    = await browser.new_page()

        print("  Logging in…", end="", flush=True)
        await login(page, creds)
        print("  done.\n")
        print(f"  {BOLD}Tip: press Ctrl+C at any time to pause and save progress.{RESET}\n")

        total   = len(entities)
        results = []
        interrupted = False

        try:
            for i, item in enumerate(entities):
                if i < start_index:
                    results.append(None)   # placeholder for skipped-by-resume rows
                    continue

                entity = item["entity"]
                display_i = i + 1
                print_progress(display_i, total, entity)

                result = await check_entity(page, entity, access_code)
                results.append(result)
                print_progress(display_i, total, entity, result)

                ws.cell(row=item["row"], column=access_col).value = result
                save_checkpoint(i)
                await page.wait_for_timeout(3000)   # 3 s between entities to avoid rate-limiting

        except KeyboardInterrupt:
            interrupted = True
            print(f"\n\n  {BOLD}Paused by user.{RESET}")

        await browser.close()

    # Only summarise rows that were actually processed this session
    processed = [r for r in results if r is not None]
    if processed:
        print_summary(processed)

    # 4. Save
    print(f"\n{BOLD}Step 3/3  Saving workbook…{RESET}", end="", flush=True)
    save_workbook(wb, EXCEL_FILE, was_encrypted, creds.get("file_password"))
    print("  done.\n")

    if interrupted:
        print(f"  Progress saved. Run the script again to resume.\n")
    else:
        clear_checkpoint()
        print(f"  All entities processed. Checkpoint cleared.\n")


if __name__ == "__main__":
    asyncio.run(main())
