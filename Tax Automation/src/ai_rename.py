# =============================================================================
# ai_rename.py — AI-Powered File Renamer  (Menu 3)
#
# What it does:
#   1. User inputs a folder path
#   2. Recursively scans for supported files (jpg, png, pdf, docx, xlsx)
#   3. Sends each file to Google Gemini AI for analysis
#   4. AI extracts: date, vendor/source, amount, and confidence score
#   5. Shows preview table — user confirms before any changes are made
#   6. Renames files: [confidence] YYYY-MM-DD Vendor Amount.ext
#   7. Unidentifiable files: [UNREAD] original_name.ext
#   8. Duplicate names get _1, _2 ... suffix
#
# API Key: encrypted with machine-specific key, stored in data/gemini_key.enc
# =============================================================================

import base64
import collections
import hashlib
import io
import json
import os
import re
import socket
import time

import config

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf", ".docx", ".xlsx"}

MIME_MAP = {
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".pdf":  "application/pdf",
}

RENAME_PROMPT = """\
You are a document classifier for a Canadian accounting firm.
Analyze the provided document and extract the following four fields for file naming purposes:

1. date
   - The most relevant date on the document (e.g. invoice date, statement date, tax year-end).
   - Format: YYYY-MM-DD
   - If only month and year are visible, use YYYY-MM-01.
   - If the date cannot be determined, use "unknown".

2. vendor
   - The vendor, seller, or issuing party named on the document —
     i.e. the entity the client dealt with or received the document from.
   - Examples: "Shoppers", "Costco", "TD Bank", "CRA", "Sun Life", "City of Calgary".
   - Use the most recognizable short form. Max 30 characters.
   - If unknown, use "unknown".

3. amount
   - The single most important dollar figure on the document
     (e.g. total owing, net refund, invoice total, benefit amount).
   - Format: include the $ sign and use commas for thousands (e.g. "$1,234.56").
   - If multiple amounts are present, choose the final total or net amount.
   - If no monetary amount is present, use "nil".

4. confidence
   - An integer from 0 to 100 representing how confident you are in the above three fields combined.
   - 90–100: all three fields are clearly and unambiguously readable.
   - 60–89:  most fields are readable but one may be uncertain or estimated.
   - 30–59:  document is partially legible or fields are ambiguous.
   - 0–29:   document is unreadable, blank, or completely unrelated to finance.

Respond ONLY with a valid JSON object. No explanation, no markdown, no extra text:
{"date": "...", "vendor": "...", "amount": "...", "confidence": 85}
"""


# ── API Key management ────────────────────────────────────────────────────────

def _machine_fernet():
    """Derive a Fernet key from the machine hostname."""
    from cryptography.fernet import Fernet
    raw = hashlib.sha256(socket.gethostname().encode()).digest()
    return Fernet(base64.urlsafe_b64encode(raw))


def load_api_key():
    """Load and decrypt the Gemini API key. Returns None if not set up."""
    if not os.path.exists(config.GEMINI_KEY_FILE):
        return None
    try:
        f = _machine_fernet()
        with open(config.GEMINI_KEY_FILE, "rb") as fh:
            return f.decrypt(fh.read()).decode()
    except Exception:
        return None


def save_api_key(key: str):
    """Encrypt and save the Gemini API key."""
    os.makedirs(config.SCRIPTS_DATA_PATH, exist_ok=True)
    f = _machine_fernet()
    with open(config.GEMINI_KEY_FILE, "wb") as fh:
        fh.write(f.encrypt(key.encode()))


def setup_api_key():
    """Prompt for API key if not stored. Returns key or None if user cancels."""
    key = load_api_key()
    if key:
        return key

    print("\n  Gemini API key not found.")
    print("  Enter your API key (or press Enter to cancel):")
    key = input("  API Key: ").strip()
    if not key:
        return None
    save_api_key(key)
    print("  API key saved (encrypted).")
    return key


# ── File collection ───────────────────────────────────────────────────────────

def collect_files(folder: str) -> list:
    """Recursively collect all supported files under folder."""
    files = []
    for root, _, filenames in os.walk(folder):
        for name in filenames:
            ext = os.path.splitext(name)[1].lower()
            if ext in SUPPORTED_EXTENSIONS:
                files.append(os.path.join(root, name))
    return sorted(files)


# ── File preparation for Gemini ───────────────────────────────────────────────

def _prepare_image(path: str) -> list:
    ext  = os.path.splitext(path)[1].lower()
    mime = MIME_MAP.get(ext, "image/jpeg")
    with open(path, "rb") as f:
        data = f.read()
    return [{"mime_type": mime, "data": data}]


def _prepare_pdf(path: str) -> list:
    with open(path, "rb") as f:
        data = f.read()
    return [{"mime_type": "application/pdf", "data": data}]


def _prepare_docx(path: str) -> list:
    """Extract text and any embedded images from a docx file."""
    from docx import Document
    parts = []

    doc = Document(path)

    # Plain text from paragraphs and tables
    lines = []
    for para in doc.paragraphs:
        if para.text.strip():
            lines.append(para.text)
    for table in doc.tables:
        for row in table.rows:
            row_text = "  |  ".join(c.text.strip() for c in row.cells if c.text.strip())
            if row_text:
                lines.append(row_text)
    if lines:
        parts.append("\n".join(lines))

    # Embedded images
    for rel in doc.part.rels.values():
        if "image" in rel.reltype:
            try:
                img_data = rel.target_part.blob
                ct = rel.target_part.content_type
                if ct in ("image/jpeg", "image/png", "image/gif", "image/webp"):
                    parts.append({"mime_type": ct, "data": img_data})
            except Exception:
                pass

    return parts if parts else ["[Could not extract content from this docx file]"]


def _prepare_xlsx(path: str) -> list:
    """Convert first 3 sheets (up to 50 rows each) to CSV text."""
    import pandas as pd
    try:
        xl   = pd.ExcelFile(path, engine="openpyxl")
        lines = []
        for sheet in xl.sheet_names[:3]:
            df = xl.parse(sheet, nrows=50)
            lines.append(f"=== Sheet: {sheet} ===")
            lines.append(df.to_csv(index=False))
        return ["\n".join(lines)]
    except Exception as e:
        return [f"[Could not read xlsx: {e}]"]


def prepare_parts(path: str) -> list:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".jpg", ".jpeg", ".png"):
        return _prepare_image(path)
    elif ext == ".pdf":
        return _prepare_pdf(path)
    elif ext == ".docx":
        return _prepare_docx(path)
    elif ext == ".xlsx":
        return _prepare_xlsx(path)
    return []


# ── Rate limiter ──────────────────────────────────────────────────────────────

class RateLimiter:
    """
    Sliding-window rate limiter.
    Blocks before each request to stay within max_per_minute.
    """
    def __init__(self, max_per_minute: int):
        self.max_per_minute = max_per_minute
        self.timestamps     = collections.deque()

    def wait(self):
        while True:
            now = time.time()
            # Drop timestamps older than 60 s
            while self.timestamps and now - self.timestamps[0] > 60:
                self.timestamps.popleft()

            if len(self.timestamps) < self.max_per_minute:
                self.timestamps.append(now)
                return

            # Must wait until the oldest slot expires
            wait_sec = 61 - (now - self.timestamps[0])
            print(f"\n  [Rate limit] Waiting {wait_sec:.0f}s for next slot...", end="", flush=True)
            time.sleep(wait_sec)


# ── Gemini analysis ───────────────────────────────────────────────────────────

def _build_content(path: str):
    """Prepare file parts + prompt. Returns content list or None on error."""
    from google.genai import types
    try:
        parts = prepare_parts(path)
        if not parts:
            return None
        content = []
        for p in parts:
            if isinstance(p, str):
                content.append(p)
            elif isinstance(p, dict):
                content.append(
                    types.Part.from_bytes(data=p["data"], mime_type=p["mime_type"])
                )
        content.append(RENAME_PROMPT)
        return content
    except Exception:
        return None


def _parse_response(text: str) -> dict:
    """Parse Gemini JSON response. Raises json.JSONDecodeError on bad output."""
    text = text.strip()
    text = re.sub(r"^```[a-z]*\s*", "", text)
    text = re.sub(r"\s*```$",        "", text)
    result = json.loads(text)
    result["confidence"] = int(result.get("confidence", 0))
    return result


def _is_quota_error(exc: Exception) -> bool:
    s = str(exc).lower()
    return any(k in s for k in ("429", "resource_exhausted", "quota", "rate limit", "too many"))


def _is_fatal_error(exc: Exception) -> bool:
    """Errors that should NOT be retried (wrong model, bad key, permission, etc.)"""
    s = str(exc).lower()
    return any(k in s for k in (
        "not found", "invalid", "permission", "unauthenticated",
        "api_key", "does not exist", "unknown model", "not supported",
    ))


def analyze_file(client, path: str, rate_limiter: RateLimiter) -> dict:
    """
    Analyze one file with rate limiting and retry on quota / transient errors.
    Never raises — always returns a dict with at least confidence=0.
    """
    empty = {"date": "", "vendor": "", "amount": "", "confidence": 0}

    content = _build_content(path)
    if content is None:
        return empty

    for attempt in range(config.GEMINI_MAX_RETRIES):
        try:
            rate_limiter.wait()
            response = client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=content,
            )
            return _parse_response(response.text)

        except json.JSONDecodeError:
            # Gemini returned non-JSON — no point retrying
            return empty

        except Exception as exc:
            is_last = (attempt == config.GEMINI_MAX_RETRIES - 1)

            if _is_fatal_error(exc):
                print(f"\n  [ERROR] Fatal — no retry: {exc}")
                return empty

            if _is_quota_error(exc):
                wait_sec = 60 * (attempt + 1)   # 60 s, 120 s, 180 s …
                print(
                    f"\n  [Quota] Waiting {wait_sec}s "
                    f"(retry {attempt + 1}/{config.GEMINI_MAX_RETRIES})...",
                    end="", flush=True,
                )
                time.sleep(wait_sec)
            elif not is_last:
                print(f"\n  [WARN] {exc} — retrying ({attempt + 1}/{config.GEMINI_MAX_RETRIES})...")
                time.sleep(10 * (attempt + 1))
            else:
                print(f"\n  [ERROR] {exc}")
                return empty

    return empty


# ── Filename building ─────────────────────────────────────────────────────────

def sanitize(s: str, max_len: int = 30) -> str:
    """Strip filesystem-unsafe characters and trim to max_len."""
    s = re.sub(r'[\\/:*?"<>|\n\r\t]', "", str(s)).strip()
    return s[:max_len]


def build_new_name(result: dict, original_path: str, used_names: set) -> str:
    """Construct the new filename from AI result, resolving duplicates."""
    ext        = os.path.splitext(original_path)[1].lower()
    confidence = int(result.get("confidence", 0))

    date   = sanitize(result.get("date",   "unknown"), 10)
    amount = sanitize(result.get("amount", "nil"),     15)

    vendor = sanitize(result.get("vendor", "unknown"), 30)

    all_fields_empty = (date   in ("", "unknown") and
                        vendor in ("", "unknown") and
                        amount in ("", "nil"))

    if confidence < 20 or all_fields_empty:
        orig_stem = os.path.splitext(os.path.basename(original_path))[0]
        stem = f"[UNREAD] {sanitize(orig_stem, 50)}"
    else:
        stem = f"[{confidence:03d}] {date} {vendor} {amount}"

    # Resolve duplicate filenames
    candidate = stem + ext
    if candidate not in used_names:
        used_names.add(candidate)
        return candidate

    i = 1
    while True:
        candidate = f"{stem}_{i}{ext}"
        if candidate not in used_names:
            used_names.add(candidate)
            return candidate
        i += 1


# ── Main ──────────────────────────────────────────────────────────────────────

def ai_rename():
    print("\n" + "=" * 60)
    print("  AI File Renamer")
    print("=" * 60)

    # API key
    api_key = setup_api_key()
    if not api_key:
        print("\n  No API key — cancelled.")
        return

    # Folder input
    print("\n  Enter the folder path to process:")
    folder = input("  Folder: ").strip().strip('"')
    if not folder or not os.path.isdir(folder):
        print(f"\n  [ERROR] Folder not found: {folder}")
        return

    # Collect files
    files = collect_files(folder)
    if not files:
        print(f"\n  No supported files found.")
        print(f"  Supported formats: {', '.join(sorted(SUPPORTED_EXTENSIONS))}")
        return

    max_mb    = config.GEMINI_MAX_FILE_MB
    oversized = [f for f in files if os.path.getsize(f) > max_mb * 1024 * 1024]
    if oversized:
        print(f"\n  [WARN] {len(oversized)} file(s) exceed {max_mb} MB and will be skipped:")
        for f in oversized:
            print(f"    {os.path.relpath(f, folder)}")
        files = [f for f in files if f not in oversized]

    if not files:
        print("\n  No files remaining to process.")
        return

    print(f"\n  Found {len(files)} file(s). Analysing with Gemini...\n")

    # Init Gemini
    from google import genai
    client = genai.Client(api_key=api_key)

    # Connection test — catch bad model name / key before processing files
    print(f"\n  Testing connection to {config.GEMINI_MODEL}...", end="", flush=True)
    try:
        client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=["Reply with the single word: OK"],
        )
        print(" OK")
    except Exception as exc:
        print(f"\n  [ERROR] Connection test failed: {exc}")
        print("  Check GEMINI_MODEL and API key in config.py.")
        return

    rate_limiter = RateLimiter(config.GEMINI_RPM)

    # Analyse each file
    plan       = []   # list of (original_path, new_name, confidence)
    used_names = set()
    width      = len(str(len(files)))

    for i, path in enumerate(files, 1):
        rel = os.path.relpath(path, folder)
        print(f"  [{i:{width}}/{len(files)}] {rel[:55]:<55}", end="", flush=True)

        result     = analyze_file(client, path, rate_limiter)
        new_name   = build_new_name(result, path, used_names)
        confidence = result.get("confidence", 0)

        plan.append((path, new_name, confidence))
        print(f"  →  {new_name}")

    # Preview table
    print(f"\n  {'─'*80}")
    print(f"  {'Conf':<6}  {'Original':<38}  New Name")
    print(f"  {'─'*6}  {'─'*38}  {'─'*38}")
    for orig, new, conf in plan:
        orig_name = os.path.basename(orig)[:38]
        conf_str  = "—" if new.startswith("[UNREAD]") else str(conf)
        print(f"  {conf_str:<6}  {orig_name:<38}  {new}")

    unread = sum(1 for _, n, _ in plan if n.startswith("[UNREAD]"))
    print(f"\n  Total: {len(plan)}  |  Flagged [UNREAD]: {unread}")
    print()

    confirm = input("  Rename all listed files? (Y / N): ").strip().upper()
    if confirm != "Y":
        print("\n  Cancelled — no files were changed.")
        return

    # Execute renames
    renamed = failed = 0
    for orig, new_name, _ in plan:
        new_path = os.path.join(os.path.dirname(orig), new_name)
        try:
            os.rename(orig, new_path)
            renamed += 1
        except Exception as e:
            print(f"  [ERROR] {os.path.basename(orig)}: {e}")
            failed += 1

    print(f"\n  Done — Renamed: {renamed}  |  Failed: {failed}")


if __name__ == "__main__":
    ai_rename()
    input("\nPress Enter to exit...")
