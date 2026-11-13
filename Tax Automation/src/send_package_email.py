# =============================================================================
# send_package_email.py — Send Client Package Emails  (Menu 5)
#
# Flow per client folder (6. Sign & Pay\[client]):
#   1. Parse person name(s) from folder name (handles couples)
#   2. Scan 2. Taxcycle\ for PDFs, group by person
#   3. AI reads each person's T1 PDF → extract email + refund/balance
#   4. Fill email template with dynamic content (file list, sig docs, amount)
#   5. Open Notepad for preview / editing
#   6. Confirm / edit recipient email
#   7. Send (with all PDFs as attachments) or skip
#   8. If sent: offer to move client folder to 6.1 Sent
# =============================================================================

import base64
import html as html_mod
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from email import encoders
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import concurrent.futures
import threading
import time

import config

_SCRIPTS_ROOT = Path(__file__).resolve().parents[2]
if str(_SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_ROOT))

from _lib.config import settings as shared_settings
from _lib.connectors.email_client import EmailClient

# Docs that require client signature
SIGNATURE_REQUIRED = {"T183", "T1135", "UHT", "Authorization of Representative"}

# Gemini prompt for T1 extraction
T1_EXTRACT_PROMPT = """\
You are analyzing a Canadian T1 personal income tax return PDF.
Extract these three fields:

1. email      — The taxpayer's email address on the return. Use "unknown" if absent.
2. amount_type — Exactly one of:
                 "owing"  — taxpayer owes money to CRA (balance due)
                 "refund" — CRA owes a refund to taxpayer
                 "zero"   — no balance and no refund
3. amount     — The dollar amount as a plain number with 2 decimal places (e.g. "1234.56").
                Use "0.00" if zero or not found.

Respond ONLY with valid JSON, no explanation:
{"email": "...", "amount_type": "refund", "amount": "1234.56"}
"""


# ── Person name parsing ───────────────────────────────────────────────────────

def parse_person_names(folder_name: str) -> list:
    """
    Extract full names from a client folder name.
    'Li,Wei & Han,Mei' → ['Wei Li', 'Mei Han']
    'Brown,Aaron & Morgan'    → ['Aaron Brown', 'Morgan Brown']
    'Smith,John'              → ['John Smith']
    """
    folder_name = folder_name.strip().replace("，", ",")   # normalise fullwidth comma
    folder_name = re.sub(r'\b(19|20)\d{2}\b', '', folder_name).strip(" -_")  # strip year (e.g. 2023)

    def _valid(s):
        return bool(s) and s.lower() not in ("nan", "")

    if "&" not in folder_name:
        if "," in folder_name:
            last, first = folder_name.split(",", 1)
            last, first = last.strip(), first.strip()
            return [f"{first} {last}"] if _valid(first) and _valid(last) else []
        return [folder_name] if _valid(folder_name) else []

    left, right = [s.strip() for s in folder_name.split("&", 1)]
    names = []

    if "," in left:
        last, first = [s.strip() for s in left.split(",", 1)]
        if _valid(first) and _valid(last):
            names.append(f"{first} {last}")

        if "," in right:
            w_last, w_first = [s.strip() for s in right.split(",", 1)]
            if _valid(w_first) and _valid(w_last):
                names.append(f"{w_first} {w_last}")
        else:
            if _valid(right):
                names.append(f"{right} {last}")
    else:
        # Space-separated: "FirstName LastName & FirstName LastName"
        left_parts = left.split()
        if len(left_parts) >= 2:
            if _valid(left):
                names.append(left)
            last = left_parts[-1]
        else:
            last = ""

        if "," in right:
            w_last, w_first = [s.strip() for s in right.split(",", 1)]
            if _valid(w_first) and _valid(w_last):
                names.append(f"{w_first} {w_last}")
        elif _valid(right):
            right_parts = right.split()
            if len(right_parts) == 1 and last:
                names.append(f"{right} {last}")
            elif right_parts:
                names.append(right)

    return names


# ── PDF management ────────────────────────────────────────────────────────────

def scan_taxcycle_pdfs(client_folder: str) -> list:
    """Return all PDFs inside the client's Taxcycle subfolder."""
    tc = os.path.join(client_folder, config.TAXCYCLE_SUBFOLDER)
    if not os.path.isdir(tc):
        return []
    return sorted(
        os.path.join(tc, f) for f in os.listdir(tc) if f.lower().endswith(".pdf")
    )


def _norm(s: str) -> str:
    """Strip non-alphanumeric chars (apostrophes, hyphens) for name matching."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def group_pdfs_by_person(pdfs: list, persons: list) -> tuple:
    """
    Group PDFs by person name (matched against filename).

    Rules:
    - PDF whose filename contains person A's name only  → A only
    - PDF whose filename contains both A's and B's name → both (joint doc)
    - PDF whose filename contains no person's name      → unmatched list

    Returns: ({person_name: [pdf_paths]}, [unmatched_paths])
    """
    groups    = {p: [] for p in persons}
    unmatched = []

    def _strict(person: str, fname: str) -> bool:
        nf = _norm(fname)
        return all(_norm(part) in nf for part in person.split())

    def _loose(person: str, fname: str) -> bool:
        """Last name + at least one given name part — handles l/I and similar typos."""
        parts = [_norm(p) for p in person.split()]
        nf = _norm(fname)
        if len(parts) == 1:
            return parts[0] in nf
        if parts[-1] not in nf:
            return False
        return any(p in nf for p in parts[:-1])

    for path in pdfs:
        fname = os.path.basename(path).lower()

        # Try strict match first; fall back to loose only if nothing matches strictly
        matched_persons = [p for p in persons if _strict(p, fname)]
        if not matched_persons:
            matched_persons = [p for p in persons if _loose(p, fname)]

        if matched_persons:
            for person in matched_persons:
                groups[person].append(path)
        else:
            unmatched.append(path)

    return groups, unmatched


# Keywords used to strip non-name parts from a PDF filename
_DOC_KEYWORDS = re.compile(
    r'\b(t1135|t183|t1|cletter|cinvoice|office\s+copy|sent|copy|'
    r'20\d{2}[-/]\d{2}[-/]\d{2}|20\d{2})\b',
    re.IGNORECASE,
)

def _extract_name_from_filename(filename: str) -> str:
    """
    Heuristically extract a person name from a TaxCycle PDF filename.
    e.g. 'Lin Zhao 2025 T1 Office copy 2026-04-03.pdf' → 'Lin Zhao'
    """
    stem = os.path.splitext(filename)[0]
    cleaned = _DOC_KEYWORDS.sub("", stem)
    # Remove stray digits and punctuation, collapse spaces
    cleaned = re.sub(r'[-_]+', ' ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    # Take up to the first 3 words (covers "First Last" or "First Middle Last")
    words = [w for w in cleaned.split() if not w.isdigit() and len(w) > 1]
    return " ".join(words[:3]).title()


def group_unmatched_by_child(unmatched: list) -> dict:
    """
    Group unmatched PDFs by apparent child name extracted from filenames.
    Returns: {child_name: [pdf_paths]}
    """
    child_groups: dict = {}
    for path in unmatched:
        name = _extract_name_from_filename(os.path.basename(path))
        if name:
            child_groups.setdefault(name, []).append(path)
    return child_groups


def _reunite_renamed_persons(persons, groups, unmatched):
    """
    When a person's PDFs are all unmatched (e.g., folder has married name
    'Parviz' but PDFs use maiden name 'Reddekopp'), reassign unmatched PDFs
    based on first-name matching.

    Returns the updated unmatched list.
    """
    if not unmatched or not persons:
        return unmatched

    child_groups = group_unmatched_by_child(unmatched)
    reassigned = set()

    for person in persons:
        if groups.get(person):
            continue
        p_first = _norm(person.split()[0]) if person.split() else ""
        if not p_first:
            continue
        for child_name, child_pdfs in child_groups.items():
            c_first = _norm(child_name.split()[0]) if child_name.split() else ""
            if c_first == p_first:
                groups[person] = list(child_pdfs)
                reassigned.update(child_pdfs)
                print(f"  [INFO] '{person}' matched to PDFs under '{child_name}'"
                      f" (same first name, different last name)")
                break

    return [p for p in unmatched if p not in reassigned]


def map_filename(filename: str) -> str:
    """Map a PDF filename to its friendly display name using FILENAME_MAP."""
    lower = filename.lower()
    for keyword, display in config.FILENAME_MAP:
        if keyword.lower() in lower:
            return display
    return os.path.splitext(filename)[0]   # fallback: strip extension


def get_signature_docs(mapped_names: list) -> list:
    """Return signature-required docs that are present (preserving order, deduplicated)."""
    seen, result = set(), []
    for name in mapped_names:
        if name in SIGNATURE_REQUIRED and name not in seen:
            seen.add(name)
            result.append(name)
    return result


def find_t1_pdf(pdfs: list, person_name: str = "", all_pdfs: list = None) -> str:
    """
    Find the T1 General / Office copy PDF for a person. Returns path or empty string.
    Only matches the main T1 return — excludes T183, T1135, T1159, and any other T1xxx forms.

    Searches `pdfs` first (person's pre-grouped list); if not found and `all_pdfs` is
    provided, falls back to searching the full taxcycle folder (in case the file's name
    didn't include the family name and was mis-classified as unmatched).
    """
    # \bt1\b matches "T1" as a whole word — works for "T1 Office copy", "T1.pdf", etc.
    # \bt1\d excludes T183, T1135, T1159, T1161… (T1 immediately followed by a digit)
    T1_POSITIVE = re.compile(r'\bt1\b', re.IGNORECASE)
    T1_VARIANT  = re.compile(r'\bt1\d', re.IGNORECASE)

    parts = [_norm(p) for p in person_name.split()] if person_name else []

    def _is_t1(path: str) -> bool:
        basename = os.path.basename(path)
        return T1_POSITIVE.search(basename) and not T1_VARIANT.search(basename)

    def _name_matches(path: str) -> bool:
        """Strict: all name parts must appear in filename (normalized)."""
        if not parts:
            return True
        nf = _norm(os.path.basename(path))
        return all(p in nf for p in parts)

    def _name_matches_loose(path: str) -> bool:
        """
        Loose: last name (family name) + at least one other part must match.
        Handles l/I confusion, apostrophes, and similar spelling differences
        between folder names and TaxCycle export filenames.
        Protects couples: person A won't match person B's PDFs as long as
        their given names differ.
        """
        if not parts:
            return True
        nf = _norm(os.path.basename(path))
        if len(parts) == 1:
            return parts[0] in nf
        if parts[-1] not in nf:          # family name must be present
            return False
        return any(p in nf for p in parts[:-1])   # at least one given-name part

    # Primary: search the pre-grouped list (strict match)
    for path in pdfs:
        if _is_t1(path) and _name_matches(path):
            return path

    # Fallback 0: any T1 in the pre-grouped list (handles folder/PDF name mismatch)
    for path in pdfs:
        if _is_t1(path):
            return path

    # Fallback 1: all PDFs, strict match
    if all_pdfs:
        for path in all_pdfs:
            if path not in pdfs and _is_t1(path) and _name_matches(path):
                return path

    # Fallback 2: all PDFs, loose match (catches l/I and similar typos)
    if all_pdfs:
        for path in all_pdfs:
            if _is_t1(path) and _name_matches_loose(path):
                return path

    return ""


# ── Gemini API key — delegates to ai_rename.py so key is shared & saved ──────

def _load_gemini_key() -> str:
    """Load key via ai_rename.setup_api_key(): prompts once if not saved, then persists."""
    try:
        import ai_rename
        return ai_rename.setup_api_key() or ""
    except Exception as exc:
        print(f"\n  [ERROR] Could not load Gemini API key: {exc}")
        return ""


# ── OpenAI API key (same encryption scheme as Gemini) ────────────────────────

def _machine_fernet():
    import base64, hashlib, socket
    from cryptography.fernet import Fernet
    raw = hashlib.sha256(socket.gethostname().encode()).digest()
    return Fernet(base64.urlsafe_b64encode(raw))

def _load_openai_key() -> str:
    if not os.path.exists(config.OPENAI_KEY_FILE):
        return ""
    try:
        return _machine_fernet().decrypt(open(config.OPENAI_KEY_FILE, "rb").read()).decode()
    except Exception:
        return ""

def _save_openai_key(key: str):
    os.makedirs(config.SCRIPTS_DATA_PATH, exist_ok=True)
    open(config.OPENAI_KEY_FILE, "wb").write(_machine_fernet().encrypt(key.encode()))

def _setup_openai_key() -> str:
    key = _load_openai_key()
    if key:
        return key
    print("\n  OpenAI API key not found (used as fallback when Gemini is unavailable).")
    print("  Enter your OpenAI API key (or press Enter to skip):")
    key = input("  API Key: ").strip()
    if not key:
        return ""
    _save_openai_key(key)
    print("  OpenAI API key saved.")
    return key


# ── AI: extract T1 info ───────────────────────────────────────────────────────

_gemini_available: "bool | None" = None   # None=unchecked, True=ok, False=down


def extract_t1_info(gemini_client, t1_path: str) -> dict:
    """
    Ask Gemini to read the T1 PDF and return email + amount info.
    Falls back to safe defaults on any error.
    """
    from google.genai import types
    default = {"email": "unknown", "amount_type": "zero", "amount": "0.00"}
    fname = os.path.basename(t1_path)

    # ── Step 1: read file ──────────────────────────────────────────────────
    print(f"  [AI] Reading file: {fname}", flush=True)
    size_mb = os.path.getsize(t1_path) / (1024 * 1024)
    print(f"  [AI] File size: {size_mb:.1f} MB", flush=True)
    if size_mb > config.GEMINI_MAX_FILE_MB:
        print(f"  [WARN] File exceeds {config.GEMINI_MAX_FILE_MB} MB limit — skipping AI read.")
        return default

    # Read file with timeout — network share reads can hang
    print("  [AI] Loading file data…", flush=True)
    _file_result = [None]
    _file_exc    = [None]
    def _do_read():
        try:
            with open(t1_path, "rb") as f:
                _file_result[0] = f.read()
        except Exception as e:
            _file_exc[0] = e
    t_read = threading.Thread(target=_do_read, daemon=True)
    t_read.start()
    t_read.join(timeout=30)
    if _file_result[0] is None:
        if _file_exc[0]:
            print(f"  [WARN] Could not read file: {_file_exc[0]}")
        else:
            print("  [WARN] File read timed out (30s) — skipping AI read.", flush=True)
        return default
    data = _file_result[0]

    # ── Step 2: preflight check — cached per run ──────────────────────────
    global _gemini_available
    if _gemini_available is None:
        print("  [AI] Checking Gemini availability…", flush=True)
        _ping_ok = [False]
        def _ping():
            try:
                gemini_client.models.generate_content(model=config.GEMINI_MODEL, contents=["ping"])
                _ping_ok[0] = True
            except Exception:
                pass
        t = threading.Thread(target=_ping, daemon=True)
        t.start()
        t.join(timeout=10)
        _gemini_available = _ping_ok[0]
        if _gemini_available:
            print("  [AI] Gemini OK.", flush=True)
        else:
            print("  [AI] Gemini not responding — will use OpenAI for all clients.", flush=True)
    if not _gemini_available:
        return _extract_t1_info_openai(t1_path, data)

    # ── Step 3: send to Gemini (with retry) ───────────────────────────────
    contents = [
        types.Part.from_bytes(data=data, mime_type="application/pdf"),
        T1_EXTRACT_PROMPT,
    ]
    for attempt in range(config.GEMINI_MAX_RETRIES):
        try:
            print(
                f"  [AI] Sending to Gemini ({config.GEMINI_MODEL})…"
                + (f" [retry {attempt+1}/{config.GEMINI_MAX_RETRIES}]" if attempt else ""),
                flush=True,
            )

            _gem_result = [None]
            _gem_exc    = [None]
            def _do_gemini():
                try:
                    _gem_result[0] = gemini_client.models.generate_content(
                        model=config.GEMINI_MODEL,
                        contents=contents,
                    )
                except Exception as e:
                    _gem_exc[0] = e
            t_gem = threading.Thread(target=_do_gemini, daemon=True)
            t_gem.start()
            t_gem.join(timeout=config.GEMINI_TIMEOUT_SEC)

            if _gem_result[0] is None:
                if _gem_exc[0]:
                    raise _gem_exc[0]
                raise TimeoutError(f"No response after {config.GEMINI_TIMEOUT_SEC}s")

            # ── parse response ─────────────────────────────────────────────
            print("  [AI] Response received — parsing…", flush=True)
            raw = _gem_result[0].text.strip()
            raw = re.sub(r"^```[a-z]*\s*", "", raw)
            raw = re.sub(r"\s*```$",        "", raw)
            result = json.loads(raw)
            print("  [AI] Done.", flush=True)
            return result

        except json.JSONDecodeError:
            print("\n  [WARN] AI returned non-JSON response.")
            return default

        except Exception as exc:
            err = str(exc)
            is_last = (attempt == config.GEMINI_MAX_RETRIES - 1)

            # Timeout — Gemini is unresponsive, no point retrying
            if isinstance(exc, TimeoutError) or "no response after" in err.lower():
                print(f"\n  [WARN] Gemini timed out — switching to OpenAI.", flush=True)
                return _extract_t1_info_openai(t1_path, data)

            # Fatal errors — don't retry
            if any(k in err.lower() for k in ("api_key", "401", "403", "unauthenticated",
                                                "not found", "unknown model", "permission")):
                print(f"\n  [ERROR] Fatal Gemini error: {exc}")
                return default

            # Quota / rate limit — wait longer
            if any(k in err.lower() for k in ("429", "quota", "resource_exhausted", "rate limit")):
                wait = 60 * (attempt + 1)
                print(f"\n  [WARN] Quota limit — waiting {wait}s…", flush=True)
                time.sleep(wait)
            # Transient (503, unavailable) — short wait then retry
            elif not is_last:
                wait = 15 * (attempt + 1)
                print(f"\n  [WARN] Transient error ({exc}) — retrying in {wait}s…", flush=True)
                time.sleep(wait)
            else:
                print(f"\n  [WARN] Gemini failed after {config.GEMINI_MAX_RETRIES} attempts: {exc}")
                return _extract_t1_info_openai(t1_path, data)

    return _extract_t1_info_openai(t1_path, data)


def _extract_t1_info_openai(t1_path: str, data: bytes) -> dict:
    """Fallback: extract T1 info using OpenAI Responses API."""
    default = {"email": "unknown", "amount_type": "zero", "amount": "0.00"}
    openai_key = _load_openai_key()
    if not openai_key:
        print("\n  [WARN] OpenAI API key not set — skipping fallback.")
        return default
    try:
        from openai import OpenAI
        print("  [AI] Gemini unavailable — trying OpenAI fallback…", flush=True)
        client = OpenAI(api_key=openai_key)
        b64 = base64.b64encode(data).decode()

        _oai_result = [None]
        _oai_exc    = [None]
        def _do_oai():
            try:
                _oai_result[0] = client.responses.create(
                    model=config.OPENAI_MODEL,
                    input=[{
                        "role": "user",
                        "content": [
                            {
                                "type": "input_file",
                                "filename": os.path.basename(t1_path),
                                "file_data": f"data:application/pdf;base64,{b64}",
                            },
                            {
                                "type": "input_text",
                                "text": T1_EXTRACT_PROMPT,
                            },
                        ],
                    }],
                )
            except Exception as e:
                _oai_exc[0] = e
        t_oai = threading.Thread(target=_do_oai, daemon=True)
        t_oai.start()
        t_oai.join(timeout=120)   # 2-minute ceiling for OpenAI

        if _oai_result[0] is None:
            if _oai_exc[0]:
                raise _oai_exc[0]
            print("  [WARN] OpenAI request timed out (120s) — skipping.", flush=True)
            return default

        print("  [AI] OpenAI response received — parsing…", flush=True)
        raw = _oai_result[0].output_text.strip()
        raw = re.sub(r"^```[a-z]*\s*", "", raw)
        raw = re.sub(r"\s*```$",        "", raw)
        result = json.loads(raw)
        print("  [AI] Done (OpenAI).", flush=True)
        return result
    except Exception as exc:
        print(f"\n  [WARN] OpenAI fallback failed: {exc}")
        return default


# ── Template ──────────────────────────────────────────────────────────────────

def read_template() -> str:
    """Convert docx template to HTML using mammoth (preserves formatting & images)."""
    import mammoth
    with open(config.EMAIL_TEMPLATE, "rb") as f:
        result = mammoth.convert_to_html(f)
    return result.value


def extract_subject(html: str) -> tuple:
    """Extract and remove Subject line from template HTML.

    Looks for: <p><strong>Subject:</strong> ...</p>
    Returns (cleaned_html, subject_text_or_None).
    """
    m = re.search(
        r'<p>\s*<strong>\s*Subject:\s*</strong>\s*(.*?)\s*</p>',
        html, re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return html, None
    subject = m.group(1).strip()
    cleaned = html[:m.start()] + html[m.end():]
    return cleaned, subject


def extract_cid_images(html_str: str) -> tuple:
    """
    Find base64 data-URI images in HTML, replace with cid: references.
    Returns (modified_html, [(cid, subtype, bytes), ...])
    Images are embedded inline (CID) — most compatible with email clients.
    """
    images = []

    def replacer(match):
        mime    = match.group(1)           # e.g. "image/png"
        b64data = match.group(2)
        cid     = f"img{len(images) + 1}@example-firm.com"
        subtype = mime.split("/")[1]
        images.append((cid, subtype, base64.b64decode(b64data)))
        return f'src="cid:{cid}"'

    modified = re.sub(
        r'src="data:(image/[^;]+);base64,([^"]*)"',
        replacer,
        html_str,
    )
    return modified, images


def build_review_text(person_name: str, fields: dict) -> str:
    """
    Generate a structured Notepad file for reviewing / editing email content.
    Editing rules are embedded next to each field.
    """
    SEP  = "=" * 72
    DIV  = "-" * 72

    RULES = {
        "CLIENT_NAME": (
            "Client's display name used in the email greeting.\n"
            "  Edit if the name needs correction."
        ),
        "FILE_LIST": (
            "One document name per line.\n"
            "  Add, remove, or rename lines freely.\n"
            "  Do NOT use bullet points — plain text only."
        ),
        "SIGNATURE_DOCS": (
            "Documents that require the client's signature.\n"
            "  Format:  Doc1, Doc2 and Doc3\n"
            "  Use 'N/A' if no signature is required."
        ),
        "AMOUNT": (
            "Full sentence describing the refund / balance. Choose one:\n"
            "  Owing : Your tax owing amount is $X,XXX.XX. Please ensure payment\n"
            "          is made to the CRA no later than [DUE_DATE].\n"
            "  Refund: Your refund amount is $X,XXX.XX.\n"
            "  Zero  : Your federal T1 return shows no refund and no balance."
        ),
        "DUE_DATE": (
            "CRA payment deadline. Format: Month DD, YYYY\n"
            "  Example: April 30, 2026"
        ),
        "CONTACT_DATE": (
            "Date to contact client (15 days before DUE_DATE). Format: Month DD, YYYY\n"
            "  Example: April 15, 2026"
        ),
    }

    lines = [
        SEP,
        f"  EMAIL CONTENT REVIEW  \u2014  {person_name}",
        SEP,
        "",
        "  HOW TO USE THIS FILE",
        "  \u2022 Edit the text below each [FIELD] label.",
        "  \u2022 Do NOT modify or delete the [FIELD] labels.",
        "  \u2022 Do NOT edit anything above or below the === lines.",
        "  \u2022 Save and close Notepad when finished.",
        "",
        SEP,
        "",
    ]

    for key, value in fields.items():
        rule = RULES.get(key, "Edit the value below.")
        lines += [
            f"[{key}]",
            f"  Rule: {rule}",
            DIV,
            value,
            "",
        ]

    lines.append(SEP)
    return "\n".join(lines)


def parse_review_text(text: str, keys: list) -> dict:
    """
    Parse the edited Notepad content back into a {key: value} dict.
    Each value is the text between the divider (---) and the next [FIELD] or === line.
    """
    result = {}
    for key in keys:
        pattern = (
            rf'\[{re.escape(key)}\].*?'    # field label + rule text
            rf'-{{3,}}\n'                   # divider line (--- or more)
            rf'(.*?)'                       # VALUE (captured, non-greedy)
            rf'(?=\n\s*\[|\n\s*={{3,}}|\Z)' # stop at next [FIELD], ===, or end
        )
        match = re.search(pattern, text, re.DOTALL)
        if match:
            value = match.group(1)
            # Safety: strip any trailing separator lines captured by accident
            value = re.sub(r'[\n\r\s]*={3,}.*$', '', value, flags=re.DOTALL)
            result[key] = value.strip()
        else:
            result[key] = ""
    return result


def build_amount_sentence(amount_type: str, amount: str) -> str:
    try:
        amt = float(amount)
    except (ValueError, TypeError):
        amt = 0.0

    if amt == 0.0 or amount_type == "zero":
        return "Your federal T1 return shows no refund and no balance."
    if amount_type == "owing":
        return (
            f"Your tax owing amount is ${amt:,.2f}. "
            f"Please ensure payment is made to the CRA no later than {config.T1_DUE_DATE}."
        )
    # refund
    return f"Your refund amount is ${amt:,.2f}."


def _sig_docs_str(sig_docs: list) -> str:
    if not sig_docs:
        return "N/A"
    if len(sig_docs) == 1:
        return sig_docs[0]
    return ", ".join(sig_docs[:-1]) + f" and {sig_docs[-1]}"


def fill_template(
    template_html:   str,
    person_name:     str,
    file_list:       list,
    sig_docs:        list,
    amount_sentence: str,
) -> str:
    """Replace all {{PLACEHOLDER}} markers in the HTML template."""
    # Deduplicate file list while preserving order
    seen, unique_list = set(), []
    for name in file_list:
        if name not in seen:
            seen.add(name)
            unique_list.append(name)

    # HTML bullet list
    file_list_html = "".join(f"&bull; {name}<br>" for name in unique_list)

    text = template_html
    text = text.replace("{{CLIENT_NAME}}",    person_name)
    text = text.replace("{{FILE_LIST}}",      file_list_html)
    text = text.replace("{{SIGNATURE_DOCS}}", _sig_docs_str(sig_docs))
    text = text.replace("{{AMOUNT}}",         amount_sentence)
    text = text.replace("{{DUE_DATE}}",       config.T1_DUE_DATE)
    text = text.replace("{{CONTACT_DATE}}",   config.T1_CONTACT_DATE)
    return text


# ── Notepad editing ───────────────────────────────────────────────────────────

def edit_in_notepad(text: str) -> str:
    """Write text to a temp file, open Notepad, return edited content after close."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as f:
        f.write(text)
        tmp = f.name
    subprocess.run(["notepad.exe", tmp])   # blocks until Notepad is closed
    with open(tmp, encoding="utf-8") as f:
        result = f.read()
    try:
        os.unlink(tmp)
    except Exception:
        pass
    return result


# ── Credentials & sending ─────────────────────────────────────────────────────

def load_credentials():
    if shared_settings.EMAIL_AUTH_MODE == "gmail_api":
        sender_name = "ExampleCo Group"
        if os.path.exists(config.CREDENTIALS_FILE):
            with open(config.CREDENTIALS_FILE, encoding="utf-8") as f:
                sender_name = json.load(f).get("sender_name", sender_name)
        return {
            "auth_mode": "gmail_api",
            "sender_name": sender_name,
            "sender_email": shared_settings.EMAIL_ADDRESS,
            "oauth_client_secret_path": (
                shared_settings.GMAIL_OAUTH_CLIENT_SECRET_PATH
            ),
            "oauth_refresh_token_path": (
                shared_settings.GMAIL_OAUTH_REFRESH_TOKEN_PATH
            ),
        }
    if not os.path.exists(config.CREDENTIALS_FILE):
        print(f"\n  [ERROR] credentials.json not found: {config.CREDENTIALS_FILE}")
        return None
    with open(config.CREDENTIALS_FILE, encoding="utf-8") as f:
        return json.load(f)


def build_message(
    person_name:  str,
    recipient:    str,
    body:         str,
    inline_images: list,   # [(cid, subtype, bytes), ...]
    is_html:      bool,
    attachments:  list,
    creds:        dict,
    cc:           str = "",
    subject_override: str = "",
) -> MIMEMultipart:
    outer = MIMEMultipart("mixed")
    subject = subject_override or config.EMAIL_SUBJECT
    outer["Subject"] = subject.format(client_name=person_name) if "{client_name}" in subject else subject
    outer["From"]    = f"{creds['sender_name']} <{creds['sender_email']}>"
    outer["To"]      = recipient
    if cc:
        outer["Cc"]  = cc

    if is_html:
        if inline_images:
            # multipart/related wraps HTML + inline images
            related = MIMEMultipart("related")
            related.attach(MIMEText(body, "html", "utf-8"))
            for cid, subtype, data in inline_images:
                img = MIMEImage(data, _subtype=subtype)
                img.add_header("Content-ID", f"<{cid}>")
                img.add_header("Content-Disposition", "inline")
                related.attach(img)
            outer.attach(related)
        else:
            outer.attach(MIMEText(body, "html", "utf-8"))
    else:
        outer.attach(MIMEText(body, "plain", "utf-8"))

    for path in attachments:
        try:
            with open(path, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header(
                "Content-Disposition",
                f'attachment; filename="{os.path.basename(path)}"',
            )
            outer.attach(part)
        except Exception as exc:
            print(f"  [WARN] Could not attach {os.path.basename(path)}: {exc}")

    return outer


def send_one(msg: MIMEMultipart, recipients: "str | list", creds: dict) -> tuple:
    """Send a single email and save a copy to IMAP Sent. Returns (success: bool, error: str)."""
    if isinstance(recipients, str):
        recipients = [recipients]
    try:
        raw = msg.as_bytes()
        client = EmailClient(credentials=creds)
        try:
            client.connect_imap()
        except Exception:
            if creds.get("auth_mode") == "gmail_api":
                raise
            print("  [WARN] IMAP unavailable; sending without a Sent-folder copy.")
        try:
            client.send_raw(raw, recipients)
        finally:
            client.quit()
        return True, ""
    except Exception as exc:
        return False, str(exc)


# ── Folder moving ─────────────────────────────────────────────────────────────

def move_to_sent(client_folder: str):
    """Move the entire client folder to 6.1 Sent."""
    folder_name = os.path.basename(client_folder)
    dest        = os.path.join(config.SENT_PATH, folder_name)
    os.makedirs(config.SENT_PATH, exist_ok=True)

    if os.path.exists(dest):
        print(f"  [WARN] Already exists at destination — please move manually.")
        print(f"         {dest}")
        return

    try:
        shutil.move(client_folder, dest)
        print(f"  Moved  →  {dest}")
    except Exception as exc:
        print(f"  [ERROR] Move failed: {exc}")
        return

    # shutil.move on network shares sometimes leaves an empty zombie folder behind
    if os.path.exists(client_folder):
        try:
            shutil.rmtree(client_folder)
        except Exception:
            pass   # non-fatal — zombie is harmless but tidy to remove


# ── Per-person processor ──────────────────────────────────────────────────────

def process_person(
    person_name:   str,
    pdfs:          list,
    gemini_client,
    creds:         dict,
    template_text: str,
    fallback_email: str = "",
    cc_email:       str = "",
    all_pdfs:       list = None,
    template_subject: str = "",
) -> tuple:
    """Process one person end-to-end. Returns (sent: bool, extracted_email: str)."""
    extracted_email = ""   # returned so caller can use as parent fallback without re-calling AI

    print(f"\n  {'─' * 56}")
    print(f"  Person      : {person_name}")
    print(f"  PDFs        : {len(pdfs)}")

    # Map filenames
    mapped    = [map_filename(os.path.basename(p)) for p in pdfs]
    sig_docs  = get_signature_docs(mapped)

    print(f"  File list   : {', '.join(dict.fromkeys(mapped))}")   # deduplicated preview
    if sig_docs:
        print(f"  Needs sign  : {', '.join(sig_docs)}")

    # AI: read T1
    t1_path = find_t1_pdf(pdfs, person_name, all_pdfs=all_pdfs)
    if t1_path:
        print(f"\n  Reading T1 PDF with AI...")
        t1_info = extract_t1_info(gemini_client, t1_path)
        extracted_email = t1_info.get("email", "")
        if extracted_email == "unknown":
            extracted_email = ""
        print(f"  Extracted   : email={t1_info.get('email')}  "
              f"type={t1_info.get('amount_type')}  "
              f"amount={t1_info.get('amount')}")
    else:
        print(f"\n  {'!' * 56}")
        print(f"  WARNING: No T1 PDF found for {person_name}.")
        print(f"  Email address and amount must be filled in manually.")
        print(f"  {'!' * 56}")
        cont = input("  Continue processing this person? (Y / N): ").strip().upper()
        if cont != "Y":
            print("  Skipped.")
            return False, extracted_email
        t1_info = {"email": "unknown", "amount_type": "zero", "amount": "0.00"}

    amount_sentence = build_amount_sentence(
        t1_info.get("amount_type", "zero"),
        t1_info.get("amount", "0.00"),
    )

    # Build deduplicated file list
    seen, unique_mapped = set(), []
    for name in mapped:
        if name not in seen:
            seen.add(name)
            unique_mapped.append(name)

    # Assemble editable fields
    fields = {
        "CLIENT_NAME":    person_name,
        "FILE_LIST":      "\n".join(unique_mapped),
        "SIGNATURE_DOCS": _sig_docs_str(sig_docs),
        "AMOUNT":         amount_sentence,
        "DUE_DATE":       config.T1_DUE_DATE,
        "CONTACT_DATE":   config.T1_CONTACT_DATE,
    }

    # Notepad review
    print("\n  Opening Notepad for content review. Close Notepad when done.")
    input("  Press Enter to open...")
    edited_text   = edit_in_notepad(build_review_text(person_name, fields))
    edited_fields = parse_review_text(edited_text, list(fields.keys()))

    # Merge: keep edited values where available, fall back to originals
    for key in fields:
        if edited_fields.get(key, "").strip():
            fields[key] = edited_fields[key].strip()

    # Convert FILE_LIST (plain text lines) back to HTML bullets
    file_list_lines = [l.strip() for l in fields["FILE_LIST"].split("\n") if l.strip()]
    file_list_html  = "".join(f"&bull; {name}<br>" for name in file_list_lines)

    # Fill HTML template with final (possibly edited) values
    html_body = template_text
    html_body = html_body.replace("{{CLIENT_NAME}}",    fields["CLIENT_NAME"])
    html_body = html_body.replace("{{FILE_LIST}}",      file_list_html)
    html_body = html_body.replace("{{SIGNATURE_DOCS}}", fields["SIGNATURE_DOCS"])
    html_body = html_body.replace("{{AMOUNT}}",         fields["AMOUNT"])
    html_body = html_body.replace("{{DUE_DATE}}",       fields["DUE_DATE"])
    html_body = html_body.replace("{{CONTACT_DATE}}",   fields["CONTACT_DATE"])

    send_body, cid_images = extract_cid_images(html_body)
    is_html = True

    # Confirm / edit recipient — use fallback if AI couldn't find an email
    recipient = t1_info.get("email", "unknown")
    if (not recipient or recipient == "unknown") and fallback_email:
        recipient = fallback_email
        print(f"\n  Recipient   : {recipient}  (fallback from parent/guardian)")
    else:
        print(f"\n  Recipient   : {recipient}")
    override = input("  Press Enter to confirm, or type a new email: ").strip()
    if override:
        recipient = override

    if "@" not in recipient:
        print("  [ERROR] Invalid email address — skipping.")
        return False, extracted_email

    # ── Attachment review ──────────────────────────────────────────────────
    final_pdfs = list(pdfs)
    while True:
        print(f"\n  Attachments ({len(final_pdfs)} file(s)):")
        for i, p in enumerate(final_pdfs):
            print(f"    [{i + 1}] {os.path.basename(p)}")
        print("  Enter number(s) to remove (e.g. 2  or  1,3), or press Enter to confirm:")
        remove_input = input("  > ").strip()
        if not remove_input:
            break
        to_remove = set()
        for part in re.split(r"[,\s]+", remove_input):
            if part.isdigit():
                idx = int(part) - 1
                if 0 <= idx < len(final_pdfs):
                    to_remove.add(idx)
        if to_remove:
            final_pdfs = [p for i, p in enumerate(final_pdfs) if i not in to_remove]
            print(f"  Removed. {len(final_pdfs)} file(s) remaining.")
        if not final_pdfs:
            print("  No attachments left — skipping.")
            return False, extracted_email

    # Send or skip
    print(f"\n  To          : {recipient}")
    if cc_email and cc_email != recipient:
        print(f"  CC          : {cc_email}")
    print(f"  Format      : {'HTML' if is_html else 'Plain text'}")
    print(f"  Attachments : {len(final_pdfs)} PDF(s)")
    choice = input("  Send email? (Y / N): ").strip().upper()

    if choice != "Y":
        print("  Skipped.")
        return False, extracted_email

    effective_cc = cc_email if (cc_email and cc_email != recipient) else ""
    msg = build_message(person_name, recipient, send_body, cid_images, is_html, final_pdfs, creds,
                        cc=effective_cc, subject_override=template_subject)
    all_recipients = [recipient] + ([effective_cc] if effective_cc else [])
    success, err = send_one(msg, all_recipients, creds)

    if success:
        print(f"  [OK] Sent to {recipient}")
        return True, extracted_email
    else:
        print(f"  [FAIL] {err}")
        return False, extracted_email


# ── Main ──────────────────────────────────────────────────────────────────────

def send_package_email():
    print("\n" + "=" * 60)
    print("  Send Client Package Emails")
    print("=" * 60)

    # Validate paths
    required_paths = [
        ("Sign & Pay folder", config.SIGN_PAY_PATH),
        ("Email template",    config.EMAIL_TEMPLATE),
    ]
    if shared_settings.EMAIL_AUTH_MODE == "password":
        required_paths.append(("Credentials", config.CREDENTIALS_FILE))
    for label, path in required_paths:
        if not os.path.exists(path):
            print(f"\n  [ERROR] {label} not found:\n  {path}")
            return

    # Load credentials
    creds = load_credentials()
    if not creds:
        return

    # Load template
    try:
        template_text = read_template()
        template_text, template_subject = extract_subject(template_text)
        if template_subject:
            print(f"  Template subject: {template_subject}")
    except Exception as exc:
        print(f"\n  [ERROR] Could not read template: {exc}")
        return

    # Init Gemini
    api_key = _load_gemini_key()
    if not api_key:
        print("\n  [ERROR] Gemini API key not found. Run Menu 3 first to set it up.")
        return
    from google import genai
    gemini_client = genai.Client(api_key=api_key)

    # Init OpenAI key (optional fallback — prompt once if not saved)
    _setup_openai_key()

    # Scan client folders
    client_folders = sorted(
        os.path.join(config.SIGN_PAY_PATH, d)
        for d in os.listdir(config.SIGN_PAY_PATH)
        if os.path.isdir(os.path.join(config.SIGN_PAY_PATH, d))
    )

    if not client_folders:
        print(f"\n  No client folders found in:\n  {config.SIGN_PAY_PATH}")
        return

    # Track completion state for each folder
    done = [False] * len(client_folders)

    def _process_one(idx: int):
        """Process a single client folder by index. Updates done[idx]."""
        client_folder = client_folders[idx]
        folder_name   = os.path.basename(client_folder)
        persons       = parse_person_names(folder_name)
        pdfs          = scan_taxcycle_pdfs(client_folder)

        print(f"\n{'=' * 60}")
        print(f"  Client  : {folder_name}")

        if not pdfs:
            print(f"  [SKIP] No PDFs found in {config.TAXCYCLE_SUBFOLDER}\\")
            input("  Press Enter to return to list...")
            done[idx] = True
            return

        if not persons:
            print(f"  [SKIP] Could not parse person name(s) from folder name.")
            input("  Press Enter to return to list...")
            done[idx] = True
            return

        print(f"  Person(s): {', '.join(persons)}")

        groups, unmatched = group_pdfs_by_person(pdfs, persons)

        # Reunite persons whose PDFs were unmatched due to name change (married vs maiden)
        unmatched = _reunite_renamed_persons(persons, groups, unmatched)

        any_sent     = False
        parent_email = ""   # captured from first parent for child fallback

        # ── Process parents ────────────────────────────────────────────────
        for person in persons:
            sent, email = process_person(
                person, groups[person], gemini_client, creds, template_text,
                all_pdfs=pdfs, template_subject=template_subject,
            )
            if sent:
                any_sent = True
            # Capture first valid parent email as fallback for children (reuse what AI already extracted)
            if not parent_email and email:
                parent_email = email

        # ── Process children (unmatched PDFs grouped by apparent name) ─────
        if unmatched:
            child_groups = group_unmatched_by_child(unmatched)
            if child_groups:
                print(f"\n  {'─' * 56}")
                print(f"  Detected {len(child_groups)} dependant(s) / child(ren):")
                for name in child_groups:
                    print(f"    • {name}  ({len(child_groups[name])} file(s))")

                for child_name, child_pdfs in child_groups.items():
                    sent, _ = process_person(
                        child_name, child_pdfs, gemini_client, creds, template_text,
                        fallback_email=parent_email,
                        cc_email=parent_email,
                        all_pdfs=pdfs,
                        template_subject=template_subject,
                    )
                    if sent:
                        any_sent = True

        if any_sent:
            move = input("\n  Move client folder to 6.1 Sent? (Y / N): ").strip().upper()
            if move == "Y":
                move_to_sent(client_folder)

        done[idx] = True
        input("\n  Press Enter to return to list...")

    # ── Selection loop ─────────────────────────────────────────────────────────
    while True:
        pending = [i for i, d in enumerate(done) if not d]
        if not pending:
            print("\n\n  All clients processed.")
            break

        # Print selection list
        print(f"\n{'=' * 60}")
        print(f"  Client List  ({len(pending)} remaining / {len(client_folders)} total)")
        print(f"{'=' * 60}")
        for i, folder in enumerate(client_folders):
            status = "  Done  " if done[i] else "       "
            print(f"  [{i + 1:>2}] {status} {os.path.basename(folder)}")
        print(f"\n  [ A]  Process remaining in order")
        print(f"  [ Q]  Quit")
        print(f"{'─' * 60}")

        choice = input("  Select: ").strip().upper()

        if choice == "Q":
            print("\n  Exiting.")
            break
        elif choice == "A":
            for i in pending:
                _process_one(i)
        elif choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(client_folders):
                _process_one(idx)
            else:
                print("  Invalid number. Please try again.")
        else:
            print("  Invalid input. Enter a number, A, or Q.")

    print("\n  Done.")


if __name__ == "__main__":
    send_package_email()
    input("\nPress Enter to exit...")
