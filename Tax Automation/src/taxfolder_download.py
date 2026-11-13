# =============================================================================
# taxfolder_download.py — Menu 9: Download TaxFolder client list
# Logs into TaxFolder, searches for tax year in "In Progress" tab,
# downloads CSV page by page, merges into taxfolder_export.csv in data/
# =============================================================================

import os
import json
import re
import time
import shutil
import tempfile
import base64
import hashlib
import socket
import concurrent.futures

import config

from ai_rename import load_api_key, prepare_parts, RateLimiter

# ── Signature folder candidates (checked in order) ───────────────────────────
SIGN_FOLDER_CANDIDATES = [
    "3. Siged and efile confirmation",    # historical typo
    "3. Signed and efile confirmation",   # correct spelling
]
SIGN_FILE_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}

TAXFOLDER_LOGIN_URL = "https://taxfolder.com/login"
DOWNLOAD_FILENAME   = "dashboard.csv"
OUTPUT_FILENAME     = "taxfolder_export.csv"
CREDS_FILE          = os.path.join(config.SCRIPTS_KEYS_PATH, "taxfolder_creds.json")

TABLE_ID   = "inprogress-client-table"
TABLE_ROW  = f"#{TABLE_ID} tbody tr"
TABLE_PROC = f"#{TABLE_ID}_processing"
SEARCH_CSS = f"input[aria-controls='{TABLE_ID}']"


# ── Credentials ───────────────────────────────────────────────────────────────

def _load_or_prompt_credentials():
    if os.path.exists(CREDS_FILE):
        with open(CREDS_FILE, encoding="utf-8") as f:
            c = json.load(f)
        email    = c.get("email", "")
        password = c.get("password", "")
        if email and password:
            print(f"  Using saved credentials: {email}")
            return email, password

    print("\n  TaxFolder credentials not found.")
    email    = input("  Email   : ").strip()
    password = input("  Password: ").strip()

    if not email or not password:
        return "", ""

    ans = input("  Save credentials to disk? (Y / N): ").strip().upper()
    if ans == "Y":
        with open(CREDS_FILE, "w", encoding="utf-8") as f:
            json.dump({"email": email, "password": password}, f, indent=2)
        print(f"  Saved → {CREDS_FILE}")

    return email, password


# ── Chrome setup ──────────────────────────────────────────────────────────────

def _setup_driver(download_dir: str):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    opts = Options()
    opts.add_experimental_option("prefs", {
        "download.default_directory":   download_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade":   True,
        "safebrowsing.enabled":         True,
    })
    return webdriver.Chrome(options=opts)


# ── Wait helpers ──────────────────────────────────────────────────────────────

def _wait_for_table_ready(driver, slow_wait, By, EC):
    """Wait for DataTables processing to finish and rows to be present."""
    try:
        slow_wait.until(EC.invisibility_of_element_located(
            (By.CSS_SELECTOR, TABLE_PROC)
        ))
    except Exception:
        pass
    slow_wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, TABLE_ROW)))
    time.sleep(3)   # extra buffer — server is slow, avoid anti-bot triggers


def _wait_for_download(download_dir: str, before: set, timeout: int = 90) -> str:
    """
    Wait for a new completed file to appear in download_dir.
    Returns the filename (not path) of the new file, or "" on timeout.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        current  = set(os.listdir(download_dir))
        new_files = current - before
        completed = [f for f in new_files if not f.endswith(".crdownload") and not f.endswith(".tmp")]
        if completed:
            time.sleep(1.5)   # buffer for file write to finish
            return completed[0]
        time.sleep(1)
    return ""


# ── Main ───────────────────────────────────────────────────────────────────────

def download_taxfolder():
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    import pandas as pd

    tax_year = config.TAX_YEAR
    print(f"\n  TaxFolder Downloader — Tax Year: {tax_year}")
    print(f"  Output: {os.path.join(config.SCRIPTS_DATA_PATH, OUTPUT_FILENAME)}")

    email, password = _load_or_prompt_credentials()
    if not email:
        print("  [ERROR] No credentials — aborting.")
        return

    download_dir = tempfile.mkdtemp(prefix="taxfolder_dl_")
    driver       = _setup_driver(download_dir)
    wait         = WebDriverWait(driver, 40)
    slow_wait    = WebDriverWait(driver, 120)

    try:
        # ── Login ──────────────────────────────────────────────────────────────
        print("\n  Opening TaxFolder...")
        driver.get(TAXFOLDER_LOGIN_URL)

        wait.until(EC.presence_of_element_located(
            (By.CSS_SELECTOR, "input[type='email'], input[name='email']")
        ))
        time.sleep(1)
        driver.find_element(
            By.CSS_SELECTOR, "input[type='email'], input[name='email']"
        ).send_keys(email)
        time.sleep(0.5)
        driver.find_element(By.CSS_SELECTOR, "input[type='password']").send_keys(password)
        time.sleep(0.5)
        driver.find_element(By.CSS_SELECTOR, "input[type='password']").send_keys(Keys.RETURN)

        print("  Logging in...")
        print("  (If 2FA appears, complete it manually in the browser window.)")
        wait.until(EC.presence_of_element_located((By.ID, "in-progress-tab")))
        time.sleep(4)   # let dashboard fully settle
        print("  Login OK.")

        # ── In Progress tab ────────────────────────────────────────────────────
        tab = wait.until(EC.element_to_be_clickable((By.ID, "in-progress-tab")))
        driver.execute_script("arguments[0].scrollIntoView({block:'center'})", tab)
        time.sleep(1)
        tab.click()
        print("  Clicked In Progress tab — waiting for table data...")
        time.sleep(3)
        _wait_for_table_ready(driver, slow_wait, By, EC)
        print("  Table loaded.")

        # ── Filter by tax year ─────────────────────────────────────────────────
        # Find the visible search box (element_to_be_clickable ensures it's usable)
        search = slow_wait.until(EC.element_to_be_clickable(
            (By.CSS_SELECTOR, SEARCH_CSS)
        ))
        driver.execute_script("arguments[0].scrollIntoView({block:'center'})", search)
        time.sleep(1)

        # Use ActionChains for a human-like click + type
        actions = ActionChains(driver)
        actions.click(search).pause(0.5).send_keys(tax_year).perform()
        time.sleep(1)

        # Dispatch keyup so DataTables debounce fires
        driver.execute_script(
            "arguments[0].dispatchEvent(new KeyboardEvent('keyup', {bubbles:true}))",
            search
        )
        print(f"  Filtering by '{tax_year}' — waiting for table to update...")
        time.sleep(4)   # DataTables debounce + re-render
        _wait_for_table_ready(driver, slow_wait, By, EC)
        print("  Filter applied.")

        # ── Set page size to 100 ───────────────────────────────────────────────
        from selenium.webdriver.support.select import Select
        try:
            size_sel = wait.until(EC.presence_of_element_located(
                (By.CSS_SELECTOR, "select[aria-controls='inprogress-client-table']")
            ))
            driver.execute_script("arguments[0].scrollIntoView({block:'center'})", size_sel)
            time.sleep(1)
            Select(size_sel).select_by_value("100")
            print("  Page size set to 100.")
            time.sleep(3)
            _wait_for_table_ready(driver, slow_wait, By, EC)
        except Exception as e:
            print(f"  [WARN] Could not set page size: {e}")

        # ── Download loop ──────────────────────────────────────────────────────
        page_files = []
        page_num   = 1

        while True:
            print(f"\n  ── Page {page_num} ──")
            time.sleep(2)   # pause before each action — avoid anti-bot triggers

            # Snapshot existing files before download
            before = set(os.listdir(download_dir))

            # Scroll export button into view and click with ActionChains
            print("  Looking for export button...")
            export_btn = wait.until(EC.presence_of_element_located(
                (By.CSS_SELECTOR, "button[aria-controls='inprogress-client-table']")
            ))
            driver.execute_script("arguments[0].scrollIntoView({block:'center'})", export_btn)
            time.sleep(1)
            driver.execute_script("arguments[0].click()", export_btn)
            print("  Export dropdown clicked — waiting for menu...")
            time.sleep(3)   # wait for dropdown to fully open

            # Click CSV Export item
            print("  Looking for CSV Export option...")
            csv_btn = wait.until(EC.element_to_be_clickable(
                (By.XPATH, "//span[contains(normalize-space(.), 'CSV Export')]")
            ))
            print("  CSV Export found — clicking...")
            ActionChains(driver).move_to_element(csv_btn).pause(0.5).click().perform()
            print("  Downloading...")

            new_file = _wait_for_download(download_dir, before)
            if not new_file:
                print(f"  [ERROR] Download timed out on page {page_num}.")
                break

            dest = os.path.join(download_dir, f"dashboard_p{page_num}.csv")
            shutil.move(os.path.join(download_dir, new_file), dest)
            page_files.append(dest)
            print(f"  Saved: dashboard_p{page_num}.csv")

            # ── Next page ──────────────────────────────────────────────────────
            try:
                print("  Checking Next button...")
                next_link = driver.find_element(
                    By.CSS_SELECTOR,
                    "a[aria-controls='inprogress-client-table'][data-dt-idx='next']"
                )
                next_li  = next_link.find_element(By.XPATH, "..")
                li_class = next_li.get_attribute("class") or ""
                print(f"  Next <li> class: '{li_class}'")
                if "disabled" in li_class:
                    print("  Last page reached.")
                    break
                print("  Clicking Next...")
                driver.execute_script("arguments[0].scrollIntoView({block:'center'})", next_link)
                time.sleep(1)
                driver.execute_script("arguments[0].click()", next_link)
                page_num += 1
                print(f"  → Going to page {page_num}... waiting for table...")
                time.sleep(4)
                print("  Waiting for table rows...")
                _wait_for_table_ready(driver, slow_wait, By, EC)
                print("  Page ready.")
            except Exception as ex:
                print(f"  Next button error: {ex} — treating as last page.")
                break

        # ── Merge ──────────────────────────────────────────────────────────────
        if not page_files:
            print("\n  [ERROR] Nothing downloaded.")
            return

        print(f"\n  Merging {len(page_files)} page(s)...")
        dfs    = [pd.read_csv(f) for f in page_files]
        merged = pd.concat(dfs, ignore_index=True)

        output_path = os.path.join(config.SCRIPTS_DATA_PATH, OUTPUT_FILENAME)
        merged.to_csv(output_path, index=False)
        print(f"  {len(merged)} rows → {output_path}")

        # ── Generate Sign & Pay tracker ────────────────────────────────────────
        generate_sign_pay_tracker(output_path)
        print("\n  Done.")

    except Exception as e:
        import traceback
        print(f"\n  [ERROR] {type(e).__name__}: {e}")
        traceback.print_exc()

    finally:
        driver.quit()
        shutil.rmtree(download_dir, ignore_errors=True)


# =============================================================================
# Sign & Pay Tracker generator
# =============================================================================

def _parse_folder_persons(folder_name: str) -> list:
    """
    Parse a Sent folder name into individual full names (FirstName LastName format).

    Strategy: split on comma FIRST (comma is the authoritative last/first separator),
    then handle the no-comma fallback.

    Supports:
      - "LastName,FirstName"       → "FirstName LastName"
      - "LastName FirstName,FN"    → "FN LastName FirstName"  (compound last name)
      - "FirstName LastName"       → "FirstName LastName"     (no comma, best guess)
      - Full-width ， / 、 normalized to comma
      - "&" separator for couples

    Examples:
      "Smith,Anna"             → ["Anna Smith"]
      "Lee Wong,Kaiming"       → ["Kaiming Lee Wong"]
      "Laura Mendes"           → ["Laura Mendes"]
      "Thomas Paul & Mark Joseph" → ["Paul Thomas", "Mark Joseph"]
    """
    # Normalize full-width/comma variants
    normalized = folder_name.replace("，", ",").replace("、", ",")
    parts = [p.strip() for p in normalized.split("&")]
    persons = []
    inherited_last = None

    for i, part in enumerate(parts):
        part = part.strip()
        if not part:
            continue

        if "," in part:
            # Comma is the authoritative separator between last name and first name.
            # Everything before comma = last name (may contain spaces for compound names)
            # Everything after comma = first name (may contain spaces)
            comma_idx = part.index(",")
            lname = part[:comma_idx].strip()
            fname = part[comma_idx + 1:].strip()
            if i == 0:
                inherited_last = lname
            persons.append(f"{fname} {lname}")
        else:
            # No comma — "FirstName LastName" format (best guess).
            # Last token is treated as last name.
            tokens = part.split()
            if len(tokens) == 1:
                # Single name — inherit last name from first person if available
                full = f"{tokens[0]} {inherited_last}".strip() if inherited_last else tokens[0]
                persons.append(full)
            else:
                lname = tokens[-1]
                fname = " ".join(tokens[:-1])
                if i == 0:
                    inherited_last = lname
                persons.append(f"{fname} {lname}")

    return persons


# ── Local signature check ────────────────────────────────────────────────────

def _check_local_signature(sent_path: str, folder_name: str) -> dict:
    """
    Scan the signature confirmation subfolder inside a client's Sent folder.

    Looks for a subfolder matching SIGN_FOLDER_CANDIDATES in order, then
    falls back to any subfolder whose name contains both "sign" and "efile"
    (case-insensitive).  Collects PDF/image files from the matched folder.

    Returns:
        {"has_files": bool, "files": [path, ...], "folder_matched": str}
    """
    client_dir = os.path.join(sent_path, folder_name)
    if not os.path.isdir(client_dir):
        return {"has_files": False, "files": [], "folder_matched": ""}

    # 1. Try known candidate names in priority order
    matched_folder = ""
    for candidate in SIGN_FOLDER_CANDIDATES:
        if os.path.isdir(os.path.join(client_dir, candidate)):
            matched_folder = candidate
            break

    # 2. Fallback: find any subfolder with "sign" AND "efile" in name
    if not matched_folder:
        for entry in os.listdir(client_dir):
            full = os.path.join(client_dir, entry)
            if not os.path.isdir(full):
                continue
            lower = entry.lower()
            if "sign" in lower and "efile" in lower:
                matched_folder = entry
                break

    if not matched_folder:
        return {"has_files": False, "files": [], "folder_matched": ""}

    # 3. Collect files
    sign_dir = os.path.join(client_dir, matched_folder)
    files = []
    for fname in os.listdir(sign_dir):
        ext = os.path.splitext(fname)[1].lower()
        if ext in SIGN_FILE_EXTENSIONS:
            files.append(os.path.join(sign_dir, fname))

    return {
        "has_files": len(files) > 0,
        "files": sorted(files),
        "folder_matched": matched_folder,
    }


def _run_with_timeout(fn, timeout_sec, *args, **kwargs):
    """
    Run fn(*args, **kwargs) in a thread with a timeout.
    Returns the result, or None if it exceeds timeout_sec.
    Works on Windows (unlike signal.alarm).
    """
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = pool.submit(fn, *args, **kwargs)
    try:
        return future.result(timeout=timeout_sec)
    except concurrent.futures.TimeoutError:
        return None
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _gemini_call(api_key, model, parts, prompt):
    """Blocking Gemini call — run inside _run_with_timeout."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    content = []
    for p in parts:
        if isinstance(p, str):
            content.append(p)
        elif isinstance(p, dict):
            content.append(
                types.Part.from_bytes(data=p["data"], mime_type=p["mime_type"])
            )
    content.append(prompt)

    response = client.models.generate_content(
        model=model,
        contents=content,
    )
    text = response.text.strip()
    # Strip markdown code fences
    text = re.sub(r"^```[a-z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    # Extract first JSON object from response (model may add preamble)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        return json.loads(m.group(0))
    return json.loads(text)


def _openai_call(api_key, parts, prompt):
    """Blocking OpenAI call — run inside _run_with_timeout."""
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    msg_content = []
    for p in parts:
        if isinstance(p, str):
            msg_content.append({"type": "text", "text": p})
        elif isinstance(p, dict):
            mime = p["mime_type"]
            if mime.startswith("image/"):
                b64 = base64.b64encode(p["data"]).decode()
                msg_content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                })
            elif mime == "application/pdf":
                # OpenAI gpt-4o doesn't accept PDF inline
                pass
    msg_content.append({"type": "text", "text": prompt})

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": msg_content}],
        max_tokens=300,
        timeout=60,
    )
    text = response.choices[0].message.content.strip()
    text = re.sub(r"^```[a-z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    # Extract first JSON object from response
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        return json.loads(m.group(0))
    return json.loads(text)


AI_CALL_TIMEOUT = 30   # seconds — per-file AI vision call limit

def _call_ai_vision(file_path: str, prompt: str) -> dict:
    """
    Send one file to Gemini (or OpenAI fallback) with a text prompt.
    Returns the parsed JSON dict from the model, or {} on failure.
    Each provider call is wrapped in a timeout to prevent hanging.
    """
    # ── Gemini (primary) ────────────────────────────────────────────────────
    api_key = load_api_key()
    if api_key:
        try:
            parts = prepare_parts(file_path)
            if not parts:
                return {}

            result = _run_with_timeout(
                _gemini_call, AI_CALL_TIMEOUT,
                api_key, config.GEMINI_MODEL, parts, prompt,
            )
            if result is None:
                print(f"  [WARN] Gemini timed out after {AI_CALL_TIMEOUT}s — trying OpenAI fallback...")
            else:
                return result

        except json.JSONDecodeError as exc:
            print(f"  [WARN] Gemini returned non-JSON ({exc}) — trying OpenAI fallback...")
        except Exception as exc:
            print(f"  [WARN] Gemini failed ({exc}) — trying OpenAI fallback...")
    else:
        print("  [INFO] Gemini API key not configured.")

    # ── OpenAI (fallback) ───────────────────────────────────────────────────
    openai_key_file = config.OPENAI_KEY_FILE
    if os.path.exists(openai_key_file):
        try:
            from cryptography.fernet import Fernet
            fernet = Fernet(base64.urlsafe_b64encode(
                hashlib.sha256(socket.gethostname().encode()).digest()
            ))
            with open(openai_key_file, "rb") as fh:
                openai_key = fernet.decrypt(fh.read()).decode()
        except Exception:
            openai_key = None

        if openai_key:
            try:
                parts = prepare_parts(file_path)
                if not parts:
                    return {}

                result = _run_with_timeout(
                    _openai_call, AI_CALL_TIMEOUT,
                    openai_key, parts, prompt,
                )
                if result is None:
                    print(f"  [WARN] OpenAI also timed out after {AI_CALL_TIMEOUT}s")
                else:
                    return result

            except json.JSONDecodeError as exc:
                print(f"  [WARN] OpenAI returned non-JSON ({exc})")
            except Exception as exc:
                print(f"  [WARN] OpenAI fallback also failed: {exc}")
    else:
        print("  [INFO] OpenAI API key file not found. "
              "AI signature verification cannot run without at least one API key.")

    return {}


def _ai_verify_signature(file_paths: list, limiter: RateLimiter) -> str:
    """
    Check files for evidence of client signature using AI.

    Early-exits on the first file that returns "Signed".
    Returns one of: "Signed", "Not Signed", "Uncertain".
    """
    prompt = (
        "You are analyzing a file from a Canadian tax preparation workflow. "
        "This file is from a 'Signed and efile confirmation' folder.\n\n"
        "Does this file contain clear evidence that the client has signed "
        "the tax return? Look for:\n"
        "- A visible handwritten or electronic signature\n"
        "- A signed T183 form (or T183SOA)\n"
        "- A 'signed' stamp or watermark\n"
        "- Any other clear signing confirmation\n\n"
        "Respond ONLY with valid JSON:\n"
        '{"signed": true/false, "confidence": "high"/"low", '
        '"reason": "brief explanation"}'
    )

    for fpath in file_paths:
        limiter.wait()
        result = _call_ai_vision(fpath, prompt)
        if not result:
            continue

        signed     = result.get("signed", False)
        confidence = result.get("confidence", "low")

        if signed and confidence == "high":
            return "Signed"
        elif not signed and confidence == "high":
            continue   # check next file
        else:
            # low confidence or ambiguous — uncertain
            return "Uncertain"

    # All files checked, none confirmed signed
    return "Not Signed"


# ── Member (B/B+) detection ──────────────────────────────────────────────────

_MEMBER_CACHE = os.path.join(config.SCRIPTS_WORKING_PATH, "member_names_cache.json")


def _load_member_names() -> list:
    """
    Read #Business Master Client List.xlsx and return a deduplicated list of
    member names (rows where column O 'B/B+ Charge' is not empty).

    Names are extracted from column F (Owner Name) and column AK (2nd Owner Name).
    Joint names with +, /, or & separators are split into individual names.

    Results are cached to _MEMBER_CACHE; re-reads only when the source file
    modification time changes.

    Returns [] if the file doesn't exist or can't be decrypted.
    """
    master_path = config.CLIENT_MASTER_FILE
    if not os.path.exists(master_path):
        print(f"  [INFO] Master client list not found: {master_path}")
        return []

    master_mtime = os.path.getmtime(master_path)

    # Return cache if still valid
    if os.path.exists(_MEMBER_CACHE):
        try:
            with open(_MEMBER_CACHE, "r", encoding="utf-8") as f:
                cache = json.load(f)
            if cache.get("mtime") == master_mtime:
                names = cache.get("names", [])
                print(f"  Member names loaded from cache ({len(names)} names)")
                return names
        except Exception:
            pass

    # Decrypt and read the source file
    import msoffcrypto
    from io import BytesIO

    try:
        with open(master_path, "rb") as f:
            file = msoffcrypto.OfficeFile(f)
            buf = BytesIO()
            file.load_key(password=config.CLIENT_MASTER_PASSWORD)
            file.decrypt(buf)

        buf.seek(0)
        import openpyxl
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            wb = openpyxl.load_workbook(buf, data_only=True)
    except Exception as e:
        print(f"  [WARN] Cannot read Master client list ({e})")
        return []

    ws = wb["Master"]
    raw_names = set()

    for row in ws.iter_rows(min_row=2, max_row=500):
        f_val = o_val = ak_val = None
        for cell in row:
            if cell.column == 6:
                f_val = cell.value
            elif cell.column == 15:
                o_val = cell.value
            elif cell.column == 37:
                ak_val = cell.value
        if o_val is None or str(o_val).strip() == "":
            continue
        for val in (f_val, ak_val):
            if val and str(val).strip():
                raw_names.add(str(val).strip())

    wb.close()

    member_names = set()
    for raw in raw_names:
        normalized = raw.replace(" + ", " & ").replace("+", " & ")
        normalized = normalized.replace(" / ", " & ").replace("/", " & ")
        parts = [p.strip() for p in normalized.split("&")]
        for part in parts:
            part = part.strip()
            if part and part.upper() != "NA":
                member_names.add(part)

    result = sorted(member_names)

    # Save cache
    try:
        with open(_MEMBER_CACHE, "w", encoding="utf-8") as f:
            json.dump({"mtime": master_mtime, "names": result}, f, ensure_ascii=False)
    except Exception:
        pass

    return result


def _match_members_to_folders(member_names: list, folder_persons: dict,
                              threshold: int) -> set:
    """
    Match member names to Sent folder person names using fuzzy matching.

    Returns a set of folder_name where at least one person is a member.
    """
    from rapidfuzz import fuzz

    member_folders = set()

    for mname in member_names:
        best_folder, best_score = None, 0
        for folder, persons in folder_persons.items():
            for person in persons:
                score = fuzz.token_sort_ratio(mname.lower(), person.lower())
                if score > best_score:
                    best_score, best_folder = score, folder
            # Also try raw folder name
            folder_score = fuzz.token_sort_ratio(mname.lower(), folder.lower())
            if folder_score > best_score:
                best_score, best_folder = folder_score, folder

        if best_score >= threshold and best_folder:
            member_folders.add(best_folder)

    return member_folders


# ── QB Payment matching ──────────────────────────────────────────────────────

def _load_paid_export() -> list:
    """
    Read the QB PAID export file and return a list of dicts:
      [{"name": str, "amount": float, "paid": str}, ...]
    Only Invoice rows with real amounts (> 0) are included.
    Returns empty list if the file doesn't exist or can't be read.
    """
    import pandas as pd

    paid_path = config.PAID_EXPORT
    if not os.path.exists(paid_path):
        print(f"  [INFO] QB PAID export not found: {paid_path}")
        print("         Payment Status will not be updated from QB.")
        return []

    try:
        df = pd.read_excel(paid_path, sheet_name="Sheet1")
    except Exception as e:
        print(f"  [WARN] Cannot read PAID export ({e}) — skipping payment update.")
        return []

    invoices = df[df["Type"] == "Invoice"].copy()
    skipped_zero = 0
    results = []
    for _, row in invoices.iterrows():
        name = str(row.get("Name", "") or "").strip()
        if not name:
            continue
        credit = row.get("Credit", 0)
        amount = float(credit) if pd.notna(credit) else 0.0
        paid   = str(row.get("Paid", "") or "").strip()
        if amount > 0:
            results.append({"name": name, "amount": amount, "paid": paid})
        else:
            skipped_zero += 1

    print(f"  Loaded {len(results)} invoice(s) from QB PAID export "
          f"(skipped {skipped_zero} zero-amount).")
    for inv in results:
        print(f"    {inv['name']}: ${inv['amount']:.2f} ({inv['paid']})")
    return results


def _match_paid_to_folders(paid_invoices: list, folder_persons: dict,
                           threshold: int) -> dict:
    """
    Match QB invoice names to Sent folder person names using rapidfuzz.

    Args:
        paid_invoices:  list of {"name", "amount", "paid"} from QB
        folder_persons: {folder_name: [parsed_person_name, ...]}
        threshold:      min fuzzy score to accept a match

    Returns:
        {folder_name: {"status": "Paid"|"Unpaid"|"Partially Paid",
                       "invoices": [{"name", "amount", "paid", "score"}, ...]}}
    """
    from rapidfuzz import fuzz
    from collections import defaultdict

    folder_invoices = defaultdict(list)   # folder → matched invoices

    for inv in paid_invoices:
        inv_name = inv["name"]
        best_folder, best_score = None, 0

        for folder, persons in folder_persons.items():
            # Match against each parsed person name in the folder
            for person in persons:
                score = fuzz.token_sort_ratio(inv_name.lower(), person.lower())
                if score > best_score:
                    best_score, best_folder = score, folder

            # Also try matching against the raw folder name (for joint invoices
            # like "Wang, Jing & Guo, Yue" → folder "Wang,Jing & Guo,Yue")
            folder_score = fuzz.token_sort_ratio(inv_name.lower(), folder.lower())
            if folder_score > best_score:
                best_score, best_folder = folder_score, folder

        if best_score >= threshold and best_folder:
            folder_invoices[best_folder].append({
                **inv,
                "score": best_score,
            })

    # Log matching results
    matched_names = set()
    for inv in paid_invoices:
        matched = False
        for folder, invs in folder_invoices.items():
            for i in invs:
                if i["name"] == inv["name"]:
                    matched = True
                    break
            if matched:
                break
        if not matched:
            print(f"    [QB unmatched] {inv['name']}: ${inv['amount']:.2f} ({inv['paid']})")

    for folder, invs in sorted(folder_invoices.items()):
        for i in invs:
            print(f"    [QB match] {i['name']} -> {folder} ({i['score']}%)")

    # Determine payment status per folder
    result = {}
    for folder, invs in folder_invoices.items():
        all_paid = all(i["paid"] == "Paid" for i in invs)
        any_paid = any(i["paid"] == "Paid" for i in invs)

        if all_paid:
            status = "Paid"
        elif any_paid:
            status = "Partially Paid"
        else:
            status = "Unpaid"

        result[folder] = {"status": status, "invoices": invs}

    return result


def generate_sign_pay_tracker(csv_path: str):
    """
    Read taxfolder_export.csv + scan 6.1 Sent + QB PAID export
    → generate/update sign_pay_tracker.xlsx.

    Logic:
    - Source of truth = folders currently in 6.1 Sent
    - Each TaxFolder client is fuzzy-matched to its best Sent folder
    - QB PAID invoices are fuzzy-matched to folder person names for payment status
    - One Excel row per (folder, person); folders with no TaxFolder match get one placeholder row
    - Incremental: Payment Status from previous run is preserved if QB has no update
    - Signing Status is always refreshed from TaxFolder
    """
    import pandas as pd
    from collections import defaultdict
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import PatternFill, Font, Alignment
    from openpyxl.worksheet.datavalidation import DataValidation
    from rapidfuzz import fuzz

    tracker_path = config.SIGN_PAY_TRACKER
    sent_path    = config.SENT_PATH
    threshold    = config.TAXFOLDER_MATCH_THRESHOLD

    print("\n  Generating Sign & Pay tracker...")

    # ── 1. Read TaxFolder CSV ──────────────────────────────────────────────────
    try:
        tf_df = pd.read_csv(csv_path)
    except Exception as e:
        print(f"  [ERROR] Cannot read {csv_path}: {e}")
        return

    # Use Label column for matching — TaxFolder CSV copies the account
    # holder's First/Last to all linked people; Label has each person's
    # real name.  Fall back to First+Last when Label is missing/"nan".
    _label = tf_df["Label"].fillna("").astype(str).str.strip()
    _fallback = (
        tf_df["First Name"].fillna("").str.strip()
        + " "
        + tf_df["Last Name"].fillna("").str.strip()
    ).str.strip()
    tf_df["_full_name"] = _label.where(
        (_label != "") & (_label.str.lower() != "nan"),
        _fallback,
    )

    # ── 2. Scan 6.1 Sent ──────────────────────────────────────────────────────
    if not os.path.isdir(sent_path):
        print(f"  [WARN] Sent folder not found: {sent_path}")
        return

    sent_folders = sorted(
        [f for f in os.listdir(sent_path)
         if os.path.isdir(os.path.join(sent_path, f)) and not f.startswith(".")],
        key=str.lower,
    )
    if not sent_folders:
        print("  [WARN] No client folders found in 6.1 Sent.")
        return
    print(f"  Found {len(sent_folders)} folder(s) in 6.1 Sent.")

    # ── 3. Parse folder names → individual person names, then match ───────────
    folder_persons = {f: _parse_folder_persons(f) for f in sent_folders}
    folder_clients = defaultdict(list)   # folder_name → [client_dict, ...]

    for _, row in tf_df.iterrows():
        full_name = row["_full_name"]
        if not full_name:
            continue

        # Normalize: strip hyphenated suffix from last name
        # e.g. "Amir Rahimi-Tehrani" -> "Amir Rahimi"
        name_tokens = full_name.split()
        if len(name_tokens) >= 2 and "-" in name_tokens[-1]:
            simplified = " ".join(name_tokens[:-1] + [name_tokens[-1].split("-")[0]])
        else:
            simplified = full_name

        best_folder, best_score = None, 0
        for folder, persons in folder_persons.items():
            for person in persons:
                for candidate in (full_name, simplified):
                    if candidate == full_name and simplified != full_name:
                        pass  # always try full_name
                    score = fuzz.token_sort_ratio(candidate.lower(), person.lower())
                    if score > best_score:
                        # For borderline matches, verify first names are also similar
                        # to prevent cross-family false positives (e.g. "Ying Chen" != "Yong Chen")
                        if score < 90:
                            t_a = candidate.lower().split()
                            t_b = person.lower().split()
                            if len(t_a) >= 2 and len(t_b) >= 2:
                                first_score = fuzz.ratio(t_a[0], t_b[0])
                                if first_score < 80:
                                    continue
                        best_score, best_folder = score, folder

        if best_score >= threshold and best_folder:
            sign_raw    = str(row.get("Document approval status", "") or "").strip()
            sign_status = "Completed" if sign_raw == "Completed" else "Waiting for Client Response"
            folder_clients[best_folder].append({
                "client_name":    full_name,
                "taxfolder_name": full_name,
                "match_score":    best_score,
                "signing_status": sign_status,
                "signing_source": "TaxFolder",
            })

    # ── 3.1 Deduplicate TaxFolder entries ─────────────────────────────────────
    # Same person may appear multiple times (e.g. both Completed and Waiting).
    # Keep the best status (Completed wins over Waiting).
    for folder in list(folder_clients.keys()):
        seen = {}
        for c in folder_clients[folder]:
            key = c["client_name"].lower()
            if key not in seen:
                seen[key] = c
            elif c["signing_status"] == "Completed":
                seen[key] = c
        folder_clients[folder] = list(seen.values())

    # ── 3.2 EFILEReceipt detection ────────────────────────────────────────────
    # If Taxcycle folder contains EFILEReceipt → client signed AND efiled.
    print("\n  Checking EFILEReceipt in Taxcycle folders...")
    efile_verified = set()
    for folder in sent_folders:
        tc_dir = os.path.join(sent_path, folder, "2. Taxcycle")
        if not os.path.isdir(tc_dir):
            tc_dir = os.path.join(sent_path, folder, "2.Taxcycle")
        if not os.path.isdir(tc_dir):
            continue
        for fname in os.listdir(tc_dir):
            if "efilereceipt" in fname.lower():
                efile_verified.add(folder)
                break

    for folder in efile_verified:
        if folder in folder_clients:
            for c in folder_clients[folder]:
                c["signing_status"] = "Completed"
                c["signing_source"] = "EFILEReceipt (Taxcycle)"
        else:
            folder_clients[folder] = [{
                "client_name":    folder,
                "taxfolder_name": "",
                "match_score":    0,
                "signing_status": "Completed",
                "signing_source": "EFILEReceipt (Taxcycle)",
            }]
    if efile_verified:
        print(f"  EFILEReceipt: {len(efile_verified)} folder(s) verified as signed+efiled")
        for f in sorted(efile_verified):
            print(f"    {f}")

    # ── 3.5 Local signature verification (AI) ──────────────────────────────────
    # Only run for folders with actual files in signature subfolder.
    # AI verification is a secondary check — EFILEReceipt and TaxFolder are primary.
    folders_need_check = set()
    for folder, clients in folder_clients.items():
        for c in clients:
            if c["signing_status"] != "Completed":
                folders_need_check.add(folder)
                break
    for folder in sent_folders:
        if folder not in folder_clients:
            folders_need_check.add(folder)

    if folders_need_check:
        ai_limiter = RateLimiter(config.GEMINI_RPM)
        ai_verified_count = 0
        ai_uncertain_count = 0
        ai_skipped = 0
        print(f"\n  Checking local signature files (AI verification) — "
              f"{len(folders_need_check)} folder(s) to check...")
        for folder in sorted(folders_need_check):
            local = _check_local_signature(sent_path, folder)
            if not local["has_files"]:
                ai_skipped += 1
                continue

            print(f"    {folder} — {len(local['files'])} file(s) in "
                  f"'{local['folder_matched']}', asking AI...")
            verdict = _ai_verify_signature(local["files"], ai_limiter)
            print(f"      -> {verdict}")

            if verdict == "Signed":
                ai_verified_count += 1
                if folder in folder_clients:
                    for c in folder_clients[folder]:
                        c["signing_status"] = "Completed"
                        c["signing_source"] = f"Local AI Verified ({local['folder_matched']})"
            elif verdict == "Uncertain":
                ai_uncertain_count += 1
                if folder in folder_clients:
                    for c in folder_clients[folder]:
                        c["signing_status"] = "AI Uncertain — Manual Check Needed"
                        c["signing_source"] = f"AI Uncertain ({local['folder_matched']})"

        print(f"  AI result: {ai_verified_count} Signed, {ai_uncertain_count} Uncertain, "
              f"{ai_skipped} no files, "
              f"{len(folders_need_check) - ai_verified_count - ai_uncertain_count - ai_skipped} "
              f"AI failed")

    # ── 3.6 QB Payment matching ───────────────────────────────────────────────
    paid_invoices  = _load_paid_export()
    folder_payment = _match_paid_to_folders(paid_invoices, folder_persons, threshold)

    if folder_payment:
        matched_count = len(folder_payment)
        paid_count    = sum(1 for v in folder_payment.values() if v["status"] == "Paid")
        unpaid_count  = sum(1 for v in folder_payment.values() if v["status"] == "Unpaid")
        print(f"  QB payment matched: {matched_count} folder(s) "
              f"({paid_count} Paid, {unpaid_count} Unpaid)")

    # ── 3.7 Member (B/B+) detection ──────────────────────────────────────────
    # Members with B/B+ Charge have their personal tax included in business package.
    member_names = _load_member_names()
    member_folders = set()
    if member_names:
        member_folders = _match_members_to_folders(member_names, folder_persons, threshold)
        print(f"  Master client list: {len(member_names)} unique member name(s), "
              f"{len(member_folders)} matched to Sent folder(s)")
        for f in sorted(member_folders):
            print(f"    [Member] {f}")
    else:
        print("  [INFO] No member data loaded from Master client list.")

    # ── 4. Build ordered row list (sorted by folder, families grouped) ─────────
    new_rows = []
    for folder in sent_folders:
        clients = folder_clients.get(folder)

        # Determine payment status for this folder from QB
        qb_pay = folder_payment.get(folder, {})
        qb_status = qb_pay.get("status")         # "Paid" / "Unpaid" / "Partially Paid" / None
        qb_detail = ""
        if qb_pay.get("invoices"):
            qb_detail = "; ".join(
                f"{inv['name']}: ${inv['amount']:.2f} ({inv['paid']})"
                for inv in qb_pay["invoices"]
            )

        if clients:
            for c in clients:
                # Member (B/B+) override: member personal tax is included in business package
                pay_status = qb_status or "Unpaid"
                pay_detail = qb_detail
                if folder in member_folders:
                    pay_status = "Paid"
                    pay_detail = (f"Member (B/B+); {pay_detail}" if pay_detail
                                  else "Member (B/B+)")
                new_rows.append({
                    "folder_name":     folder,
                    "payment_status":  pay_status,
                    "payment_detail":  pay_detail,
                    "signing_source":  c.get("signing_source", "TaxFolder"),
                    **c,
                })
        else:
            pay_status = qb_status or "Unpaid"
            pay_detail = qb_detail
            if folder in member_folders:
                pay_status = "Paid"
                pay_detail = (f"Member (B/B+); {pay_detail}" if pay_detail
                              else "Member (B/B+)")
            new_rows.append({
                "folder_name":    folder,
                "client_name":    folder,
                "taxfolder_name": "",
                "match_score":    0,
                "signing_status": "Waiting for Client Response",
                "signing_source": "",
                "payment_status": pay_status,
                "payment_detail": pay_detail,
            })

    # ── 5. Preserve manual edits from previous run ──────────────────────────────
    # Only preserve Payment Status if QB didn't provide a status for this folder.
    # Always preserve Email, Reminder Status, Reminder Count, Last Reminder Date.
    existing_payment = {}   # (folder_name, client_name) → payment_status
    existing_reminder = {}  # (folder_name, client_name) → {"email", "reminder_status", "reminder_count", "last_reminder_date"}
    if os.path.exists(tracker_path):
        try:
            wb_old  = load_workbook(tracker_path)
            ws_old  = wb_old.active
            hdrs    = [c.value for c in ws_old[1]]
            fi      = hdrs.index("Folder Name")
            ci      = hdrs.index("Client Name")
            pi      = hdrs.index("Payment Status")
            # Safely get column indices for new reminder columns (may not exist in old tracker)
            ei    = hdrs.index("Email") if "Email" in hdrs else None
            rsi   = hdrs.index("Reminder Status") if "Reminder Status" in hdrs else None
            rci   = hdrs.index("Reminder Count") if "Reminder Count" in hdrs else None
            lrdi  = hdrs.index("Last Reminder Date") if "Last Reminder Date" in hdrs else None
            for r in ws_old.iter_rows(min_row=2, values_only=True):
                if r[fi] and r[ci]:
                    key = (r[fi], r[ci])
                    existing_payment[key] = r[pi] or "Unpaid"
                    existing_reminder[key] = {
                        "email":             r[ei] or "" if ei is not None else "",
                        "reminder_status":   r[rsi] or "" if rsi is not None else "",
                        "reminder_count":    r[rci] if rci is not None and r[rci] else None,
                        "last_reminder_date": r[lrdi] or "" if lrdi is not None else "",
                    }
            print(f"  Loaded {len(existing_payment)} existing payment status entry(s).")
        except Exception as e:
            print(f"  [WARN] Could not read existing tracker ({e}) — starting fresh.")

    for m in new_rows:
        key = (m["folder_name"], m["client_name"])
        # Only fall back to previous manual status if QB has no data for this folder
        if m["folder_name"] not in folder_payment and key in existing_payment:
            m["payment_status"] = existing_payment[key]
        # Always preserve reminder tracking columns from previous run
        if key in existing_reminder:
            rem = existing_reminder[key]
            m["email"]             = rem["email"]
            m["reminder_status"]   = rem["reminder_status"]
            m["reminder_count"]    = rem["reminder_count"]
            m["last_reminder_date"] = rem["last_reminder_date"]

    # ── 6. Write Excel ─────────────────────────────────────────────────────────
    wb = Workbook()
    ws = wb.active
    ws.title = "Sign & Pay Tracker"
    ws.freeze_panes = "A2"

    HEADERS    = ["Folder Name", "Client Name", "TaxFolder Name",
                  "Match Score", "Signing Status", "Signing Source",
                  "Payment Status", "QB Invoice Detail",
                  "Email", "Reminder Status", "Reminder Count", "Last Reminder Date"]
    COL_WIDTHS = [32, 26, 26, 13, 30, 30, 18, 45, 30, 18, 14, 18]

    # Header row
    hdr_fill = PatternFill("solid", fgColor="2F5597")
    hdr_font = Font(bold=True, color="FFFFFF")
    for col, (hdr, width) in enumerate(zip(HEADERS, COL_WIDTHS), 1):
        cell = ws.cell(row=1, column=col, value=hdr)
        cell.fill      = hdr_fill
        cell.font      = hdr_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.column_dimensions[cell.column_letter].width = width

    # Colour palette: alternating per family group
    BAND_COLORS = ["FFFFFF", "DCE6F1"]   # white / light blue
    SCORE_WARN  = PatternFill("solid", fgColor="FFF2CC")   # yellow: score 70-84
    SCORE_MISS  = PatternFill("solid", fgColor="FCE4D6")   # orange: no match
    PAID_FILL   = PatternFill("solid", fgColor="C6EFCE")   # green: paid
    PAID_FONT   = Font(color="006100")
    UNCERTAIN_FILL = PatternFill("solid", fgColor="FFF2CC")  # yellow: AI uncertain

    current_folder = None
    band_idx       = 0

    for row_num, m in enumerate(new_rows, 2):
        if m["folder_name"] != current_folder:
            current_folder = m["folder_name"]
            band_idx       = 1 - band_idx

        base_fill = PatternFill("solid", fgColor=BAND_COLORS[band_idx])
        score     = m["match_score"]

        values = [
            m["folder_name"],
            m["client_name"],
            m["taxfolder_name"],
            score if score > 0 else None,
            m["signing_status"],
            m.get("signing_source", ""),
            m["payment_status"],
            m.get("payment_detail", ""),
            m.get("email", ""),
            m.get("reminder_status", ""),
            m.get("reminder_count", None),
            m.get("last_reminder_date", ""),
        ]

        is_uncertain = m["signing_status"] == "AI Uncertain — Manual Check Needed"

        for col, val in enumerate(values, 1):
            cell           = ws.cell(row=row_num, column=col, value=val)
            cell.alignment = Alignment(vertical="center")

            if col == 4:
                # Score column: warning colour when confidence is low
                if score == 0:
                    cell.fill = SCORE_MISS
                elif score < 85:
                    cell.fill = SCORE_WARN
                else:
                    cell.fill = base_fill
            elif col == 5:
                # Signing Status: yellow highlight if AI Uncertain
                cell.alignment = Alignment(horizontal="center", vertical="center")
                if is_uncertain:
                    cell.fill = UNCERTAIN_FILL
                else:
                    cell.fill = base_fill
            elif col == 6:
                # Signing Source column
                cell.alignment = Alignment(horizontal="center", vertical="center")
                if is_uncertain:
                    cell.fill = UNCERTAIN_FILL
                else:
                    cell.fill = base_fill
            elif col == 7:
                # Payment Status column: green highlight for Paid
                cell.alignment = Alignment(horizontal="center", vertical="center")
                if m["payment_status"] == "Paid":
                    cell.fill = PAID_FILL
                    cell.font = PAID_FONT
                else:
                    cell.fill = base_fill
            elif col == 8:
                # QB detail column: smaller font, muted
                cell.fill = base_fill
                cell.font = Font(size=9, color="666666")
            else:
                cell.fill = base_fill

    # Dropdowns (columns E=Signing Status, G=Payment Status)
    last_row = len(new_rows) + 1
    for col_letter, options in [
        ("E", "Waiting for Client Response,Completed,AI Uncertain - Manual Check Needed"),
        ("G", "Unpaid,Paid,Partially Paid"),
        ("J", ",Sent,Completed"),
    ]:
        dv = DataValidation(type="list", formula1=f'"{options}"', allow_blank=True)
        dv.error = None
        dv.errorTitle = None
        dv.prompt = None
        dv.promptTitle = None
        ws.add_data_validation(dv)
        dv.sqref = f"{col_letter}2:{col_letter}{last_row}"

    wb.save(tracker_path)
    print(f"  Tracker saved -> {tracker_path}  ({len(new_rows)} row(s))")

    # Open in Excel for review
    try:
        os.startfile(tracker_path)
    except Exception:
        pass


def move_ready_to_efile():
    """
    Read sign_pay_tracker.xlsx → find folders where ALL persons have
    Signing Status = 'Completed' AND Payment Status = 'Paid' →
    move those folders from 6.1 Sent to 7. To efile.

    A folder only moves when every row under it is fully ready,
    so couples are handled correctly (both must be Completed + Paid).
    """
    from openpyxl import load_workbook
    from collections import defaultdict

    tracker_path = config.SIGN_PAY_TRACKER
    sent_path    = config.SENT_PATH
    efile_path   = config.TO_EFILE_PATH

    # ── 1. Read tracker ───────────────────────────────────────────────────────
    if not os.path.exists(tracker_path):
        print(f"\n  [ERROR] Tracker not found: {tracker_path}")
        print("  Please run option 1 or 2 first to generate the tracker.")
        return

    try:
        wb   = load_workbook(tracker_path)
        ws   = wb.active
        hdrs = [c.value for c in ws[1]]
        fi   = hdrs.index("Folder Name")
        si   = hdrs.index("Signing Status")
        pi   = hdrs.index("Payment Status")
    except Exception as e:
        print(f"\n  [ERROR] Cannot read tracker: {e}")
        return

    # ── 2. Group rows by folder, check if ALL persons are ready ───────────────
    folder_rows = defaultdict(list)   # folder → [(signing, payment), ...]
    for row in ws.iter_rows(min_row=2, values_only=True):
        folder = row[fi]
        if not folder:
            continue
        folder_rows[folder].append((
            str(row[si] or "").strip(),
            str(row[pi] or "").strip(),
        ))

    ready = [
        folder for folder, persons in folder_rows.items()
        if persons and all(
            s == "Completed" and p == "Paid"
            for s, p in persons
        )
    ]

    if not ready:
        print("\n  No folders are fully ready to efile")
        print("  (all persons must be Completed + Paid).")
        return

    # ── 3. Confirm with user ──────────────────────────────────────────────────
    print(f"\n  {len(ready)} folder(s) ready to move → 7. To efile:\n")
    for folder in ready:
        persons = folder_rows[folder]
        print(f"    • {folder}  ({len(persons)} person(s))")
    print()

    ans = input("  Move these folders now? (Y / N): ").strip().upper()
    if ans != "Y":
        print("  Cancelled.")
        return

    # ── 4. Move folders ───────────────────────────────────────────────────────
    if not os.path.isdir(efile_path):
        print(f"\n  [ERROR] Destination folder not found:\n  {efile_path}")
        return

    moved, failed = [], []
    for folder in ready:
        src = os.path.join(sent_path, folder)
        dst = os.path.join(efile_path, folder)

        if not os.path.isdir(src):
            print(f"  [WARN] Source folder missing (already moved?): {folder}")
            failed.append(folder)
            continue

        # Handle destination already exists
        if os.path.exists(dst):
            dst = dst + "_moved"

        try:
            shutil.move(src, dst)
            moved.append(folder)
            print(f"  ✓ Moved: {folder}")
        except Exception as e:
            print(f"  [ERROR] Could not move '{folder}': {e}")
            failed.append(folder)

    print(f"\n  Done — {len(moved)} moved, {len(failed)} failed.")
    if moved:
        print("  Tip: re-run option 2 to refresh the tracker.")
