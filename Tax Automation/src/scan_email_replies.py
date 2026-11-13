# =============================================================================
# scan_email_replies.py — Menu 4C/4D: Scan client replies & create folders
#
# C. Scan inbox for replies to reminder emails → AI classify → update Excel
# D. Read Positive rows → append to NewClient.xlsx → create folders
# =============================================================================

import email
import email.utils
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz, process

import config

_SCRIPTS_ROOT = Path(__file__).resolve().parents[2]
if str(_SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_ROOT))

from _lib.connectors.email_client import EmailClient

# ── Colour fills for Reply Status ──────────────────────────────────────────
_FILL_COLORS = {
    "positive": "C6EFCE",   # green
    "negative": "FFC7CE",   # red
    "question": "FFEB9C",   # yellow
    "unclear":  "D9D9D9",   # gray
    "handled":  "D6EAF8",   # light blue
}


# ── IMAP layer ─────────────────────────────────────────────────────────────

def _connect_imap(creds: dict) -> EmailClient:
    """Connect the shared client using Gmail API or legacy IMAP."""
    client = EmailClient(credentials=creds)
    client.connect_imap()
    return client


def _extract_body_text(msg: email.message.Message) -> str:
    """Extract plain text body from an email message."""
    if msg.is_multipart():
        html_parts = []
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                charset = part.get_content_charset() or "utf-8"
                text = payload.decode(charset, errors="replace").strip()
                if text:
                    return text
            elif ct == "text/html":
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                charset = part.get_content_charset() or "utf-8"
                html_parts.append(payload.decode(charset, errors="replace"))
        # Fallback: strip HTML tags from collected HTML parts
        for html_text in html_parts:
            text = re.sub(r"<[^>]+>", " ", html_text).strip()
            if text:
                return text
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace").strip()
    return ""


def _fetch_replies_since(client: EmailClient, since_date) -> list:
    """
    Search INBOX for emails since `since_date` that are NOT from our own address.
    Returns list of dicts: {uid, subject, from_addr, date, body_text}
    """
    replies = []
    messages = client.fetch_since(since_date, config.IMAP_INBOX_FOLDER)
    for message in messages:
        uid = message["uid"]
        from_addr = email.utils.parseaddr(message.get("from", ""))[1].lower()
        body = message.get("body_text", "")
        if not body:
            continue
        replies.append({
            "uid": uid.decode() if isinstance(uid, bytes) else str(uid),
            "subject": message.get("subject", ""),
            "from_addr": from_addr,
            "date": message.get("date", ""),
            "body": body,
        })

    return replies


def _close_imap(client: EmailClient):
    """Logout cleanly."""
    client.close_imap()


# ── Matching layer ─────────────────────────────────────────────────────────

def _norm_subject(s: str) -> str:
    """Strip Re:/Fwd:/FW: prefixes and normalise whitespace."""
    s = re.sub(r"^\s*(re|fwd|fw)\s*:\s*", "", s, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", s).strip()


def _match_reply_to_client(
    reply_subject: str,
    reply_from: str,
    reply_body: str,
    client_names: list,
    client_emails: dict,
) -> str | None:
    """
    Determine which client (from missing list) this reply corresponds to.

    Strategy:
    1. Subject contains a client name from missing list.
    2. Sender email matches a client's email.
    3. Fallback: None.
    """
    norm_subj = _norm_subject(reply_subject).lower()

    # Strategy 1: subject contains client name
    for name in client_names:
        if name.lower() in norm_subj:
            return name

    # Strategy 2: sender email match
    for name, em in client_emails.items():
        if em and em.lower() == reply_from.lower():
            return name

    return None


def _deduplicate_replies(replies: list) -> list:
    """Keep only the most recent reply per client name."""
    by_client = {}
    for r in replies:
        name = r.get("_matched_name")
        if not name:
            continue
        # Keep latest by uid (higher uid = more recent in IMAP)
        if name not in by_client or r["uid"] > by_client[name]["uid"]:
            by_client[name] = r
    return list(by_client.values())


# ── AI classification ──────────────────────────────────────────────────────

_CLASSIFY_PROMPT = """\
You are classifying an email reply from a client about tax preparation.
The client was sent a reminder to submit their tax documents.

Classify the reply into exactly one of:
  Positive  — Client intends to come in or send documents this year.
  Negative  — Client explicitly declines, has moved on, or used another firm.
  Question  — Client asks a question before deciding (needs staff follow-up).
  Unclear   — Cannot determine intent, or reply is unrelated / empty.

Also write a one-sentence summary of the reply (max 120 characters).

Respond ONLY with valid JSON, no markdown fences:
{"classification": "Positive", "summary": "Client says they will send documents this week."}
"""


def _classify_reply(client_name: str, reply_body: str) -> dict:
    """Send reply body to Gemini for classification. Returns {classification, summary}."""
    try:
        from ai_rename import setup_api_key
        api_key = setup_api_key()
        if not api_key:
            return {"classification": "Unclear", "summary": "AI key not available"}

        from google import genai
        client = genai.Client(api_key=api_key)

        prompt = (
            f"Client name: {client_name}\n\n"
            f"Their reply:\n{reply_body[:2000]}\n\n"
            f"{_CLASSIFY_PROMPT}"
        )

        response = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=[prompt],
        )
        text = response.text.strip()

        # Strip markdown fences if present
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

        result = json.loads(text)
        classification = result.get("classification", "Unclear")
        if classification not in ("Positive", "Negative", "Question", "Unclear"):
            classification = "Unclear"
        return {
            "classification": classification,
            "summary": result.get("summary", "")[:120],
        }
    except Exception as e:
        return {"classification": "Unclear", "summary": f"AI classification failed: {e}"}


# ── Reply log ──────────────────────────────────────────────────────────────

def _load_reply_log() -> dict:
    if os.path.exists(config.REPLY_LOG_FILE):
        try:
            with open(config.REPLY_LOG_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_scan_date": "", "scanned_uids": [], "classifications": {}}


def _save_reply_log(log: dict):
    os.makedirs(os.path.dirname(config.REPLY_LOG_FILE), exist_ok=True)
    with open(config.REPLY_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)


# ── Excel layer ────────────────────────────────────────────────────────────

def _ensure_reply_columns(ws) -> bool:
    """Add Reply Status / Reply Summary / Reply Date columns if missing. Returns True if added."""
    headers = [c.value for c in ws[1]]
    added = False

    if "Reply Status" not in headers:
        col = len(headers) + 1
        ws.cell(row=1, column=col, value="Reply Status")
        from openpyxl.styles import Alignment, Font, PatternFill
        cell = ws.cell(row=1, column=col)
        cell.fill = PatternFill("solid", fgColor="2C4770")
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.column_dimensions[cell.column_letter].width = 14
        added = True

    if "Reply Summary" not in headers:
        col = len([c.value for c in ws[1]]) + 1
        ws.cell(row=1, column=col, value="Reply Summary")
        from openpyxl.styles import Alignment, Font, PatternFill
        cell = ws.cell(row=1, column=col)
        cell.fill = PatternFill("solid", fgColor="2C4770")
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.column_dimensions[cell.column_letter].width = 50
        added = True

    if "Reply Date" not in headers:
        col = len([c.value for c in ws[1]]) + 1
        ws.cell(row=1, column=col, value="Reply Date")
        from openpyxl.styles import Alignment, Font, PatternFill
        cell = ws.cell(row=1, column=col)
        cell.fill = PatternFill("solid", fgColor="2C4770")
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.column_dimensions[cell.column_letter].width = 12
        added = True

    # Add dropdown for Reply Status if not already present
    from openpyxl.worksheet.datavalidation import DataValidation
    headers = [c.value for c in ws[1]]
    reply_col_idx = headers.index("Reply Status") + 1  # 1-based
    reply_col_letter = chr(64 + reply_col_idx)

    # Check if validation already exists for this column
    has_dv = any(
        dv.sqref and reply_col_letter in str(dv.sqref)
        for dv in ws.data_validations.dataValidation
    )
    if not has_dv:
        reply_options = "Positive,Negative,Question,Unclear,Handled"
        dv = DataValidation(type="list", formula1=f'"{reply_options}"', allow_blank=True)
        dv.error = "Please select a valid status"
        dv.errorTitle = "Invalid value"
        ws.add_data_validation(dv)
        dv.add(f"{reply_col_letter}2:{reply_col_letter}{ws.max_row + 200}")

    return added


def _apply_row_color(ws, row_idx: int, status: str):
    """Apply color fill to an entire row based on Reply Status."""
    from openpyxl.styles import PatternFill
    color = _FILL_COLORS.get(status.lower())
    if color:
        fill = PatternFill("solid", fgColor=color)
        for cell in ws[row_idx]:
            cell.fill = fill


def _update_excel_replies(path: str, results: list):
    """Write classification results into the Excel sheet."""
    import openpyxl

    wb = openpyxl.load_workbook(path)
    ws = wb.active

    _ensure_reply_columns(ws)

    headers = {c.value: c.column for c in ws[1]}  # 1-based column indices
    name_col = headers.get("Name")
    status_col = headers.get("Reply Status")
    summary_col = headers.get("Reply Summary")
    date_col = headers.get("Reply Date")

    if not all([name_col, status_col, summary_col, date_col]):
        wb.close()
        return

    # Build lookup by normalised name
    name_map = {}
    for row in ws.iter_rows(min_row=2):
        val = row[name_col - 1].value
        if val:
            name_map[val.strip().lower()] = row

    for r in results:
        name_key = r["name"].strip().lower()
        row = name_map.get(name_key)
        if row:
            row[status_col - 1].value = r["classification"]
            row[summary_col - 1].value = r["summary"]
            row[date_col - 1].value = r.get("reply_date", "")
            _apply_row_color(ws, row[0].row, r["classification"])

    wb.save(path)
    wb.close()


# ── Feature C: Scan Replies ────────────────────────────────────────────────

def scan_replies():
    """Menu 4-C: Scan inbox for client replies, AI classify, update Excel."""
    print("\n" + "=" * 60)
    print("  C. Scan Email Replies")
    print("=" * 60)

    path = config.MISSING_CLIENT_LIST
    if not os.path.exists(path):
        print(f"\n  [ERROR] Missing client list not found. Run Phase A first.")
        return

    # Load credentials
    from send_package_email import load_credentials
    creds = load_credentials()
    if not creds:
        return

    # Load client names/emails from Excel
    df = pd.read_excel(path, sheet_name=0, engine="openpyxl")
    df.columns = df.columns.str.strip()
    client_names = [str(n).strip() for n in df["Name"] if pd.notna(n) and str(n).strip()]
    client_emails = {}
    for _, row in df.iterrows():
        name = str(row.get("Name", "")).strip()
        em = str(row.get("Email", "")).strip()
        if name and em and em not in ("", "nan"):
            client_emails[name] = em

    # Load reply log (skip already-scanned UIDs)
    reply_log = _load_reply_log()
    scanned_uids = set(reply_log.get("scanned_uids", []))

    # Connect IMAP
    print(f"\n  Connecting to IMAP...")
    try:
        imap = _connect_imap(creds)
    except Exception as e:
        print(f"  [ERROR] IMAP connection failed: {e}")
        return

    # Fetch emails from this week only (Monday to now)
    today = datetime.now()
    since_date = today - timedelta(days=today.weekday())  # Monday of this week
    print(f"  Scanning inbox since {since_date.strftime('%Y-%m-%d')} (this week)...")
    all_replies = _fetch_replies_since(imap, since_date)
    _close_imap(imap)

    # Filter out our own sent emails and already-scanned UIDs
    our_email = creds["sender_email"].lower()
    replies = [
        r for r in all_replies
        if r["from_addr"] != our_email and r["uid"] not in scanned_uids
    ]
    print(f"  Found {len(replies)} new incoming email(s)")

    if not replies:
        print("\n  No new client replies found.")
        return

    # Match replies to clients
    matched = []
    for r in replies:
        name = _match_reply_to_client(r["subject"], r["from_addr"], r["body"],
                                       client_names, client_emails)
        if name:
            r["_matched_name"] = name
            matched.append(r)

    if not matched:
        print("\n  No replies matched to missing clients.")
        # Still record scanned UIDs
        reply_log["scanned_uids"].extend(r["uid"] for r in replies)
        _save_reply_log(reply_log)
        return

    # Deduplicate
    matched = _deduplicate_replies(matched)
    print(f"  Matched {len(matched)} client reply/replies")

    # Classify each reply
    print("\n  Classifying replies with AI...")
    results = []
    for r in matched:
        name = r["_matched_name"]
        body = r["body"]
        print(f"    {name}...", end=" ", flush=True)
        classification = _classify_reply(name, body)
        reply_date = ""
        try:
            parsed = email.utils.parsedate_to_datetime(r["date"])
            reply_date = parsed.strftime("%Y-%m-%d")
        except Exception:
            reply_date = datetime.now().strftime("%Y-%m-%d")

        result = {
            "name": name,
            "classification": classification["classification"],
            "summary": classification["summary"],
            "reply_date": reply_date,
            "reply_from": r["from_addr"],
            "uid": r["uid"],
        }
        results.append(result)
        print(f"{classification['classification']}")

        # Update reply log
        reply_log["classifications"][name] = {
            "classification": classification["classification"],
            "summary": classification["summary"],
            "reply_date": reply_date,
            "reply_from": r["from_addr"],
            "classified_at": datetime.now().isoformat(timespec="seconds"),
        }
        reply_log["scanned_uids"].append(r["uid"])

    reply_log["last_scan_date"] = datetime.now().isoformat(timespec="seconds")
    _save_reply_log(reply_log)

    # Update Excel
    _update_excel_replies(path, results)

    # Print summary
    print(f"\n  {'Name':<35} {'Status':<12} {'Summary'}")
    print("  " + "\u2500" * 90)
    for r in results:
        print(f"  {r['name']:<35} {r['classification']:<12} {r['summary']}")

    print(f"\n  Updated: {path}")
    print(f"  Log    : {config.REPLY_LOG_FILE}")


# ── Name parsing (for Feature D) ──────────────────────────────────────────

def _parse_client_name(full_name: str) -> dict:
    """
    Parse "First Last" into NewClient.xlsx format.
    Returns dict: {husband_last, husband_first, wife_last, wife_first,
                   same_last_name, needs_review}
    """
    result = {
        "husband_last": "", "husband_first": "",
        "wife_last": "", "wife_first": "",
        "same_last_name": "", "needs_review": False,
    }
    name = full_name.strip()
    if not name:
        result["needs_review"] = True
        return result

    # Couple indicators → always flag for review
    if re.search(r"\s+(and|&)\s+", name, re.IGNORECASE):
        result["needs_review"] = True
        return result

    # Already in "Last,First" format
    if "," in name:
        parts = name.split(",", 1)
        result["husband_last"] = parts[0].strip()
        result["husband_first"] = parts[1].strip()
        return result

    words = name.split()
    if len(words) == 1:
        result["needs_review"] = True
        return result
    elif len(words) == 2:
        result["husband_first"] = words[0]
        result["husband_last"] = words[1]
        return result
    elif len(words) == 3:
        # Ambiguous: "Mary Ann Smith" vs "First Middle Last"
        result["husband_first"] = " ".join(words[:-1])
        result["husband_last"] = words[-1]
        result["needs_review"] = True
        return result
    else:
        result["needs_review"] = True
        return result


# ── Feature D: Create folders from positive replies ────────────────────────

def create_folders_from_replies():
    """Menu 4-D: Read Positive rows → NewClient.xlsx → create folders."""
    print("\n" + "=" * 60)
    print("  D. Create Folders from Positive Replies")
    print("=" * 60)

    path = config.MISSING_CLIENT_LIST
    if not os.path.exists(path):
        print(f"\n  [ERROR] Missing client list not found. Run Phase A first.")
        return

    # Load Excel
    import openpyxl
    df = pd.read_excel(path, sheet_name=0, engine="openpyxl")
    df.columns = df.columns.str.strip()

    # Filter Reply Status = Positive
    if "Reply Status" not in df.columns:
        print("\n  [ERROR] No Reply Status column found. Run option C first.")
        return

    positive = df[
        df["Reply Status"].astype(str).str.strip().str.lower() == "positive"
    ].copy()

    if positive.empty:
        print("\n  No clients with Reply Status = Positive.")
        return

    # Parse names
    auto_rows = []
    review_rows = []
    for _, row in positive.iterrows():
        name = str(row["Name"]).strip()
        parsed = _parse_client_name(name)
        parsed["original_name"] = name
        parsed["email"] = str(row.get("Email", "")).strip()
        if parsed["needs_review"]:
            review_rows.append(parsed)
        else:
            auto_rows.append(parsed)

    # Show review-required rows
    if review_rows:
        print(f"\n  {len(review_rows)} client(s) need manual review (couples / ambiguous names):")
        print(f"  {'Name':<40} {'Reason'}")
        print("  " + "\u2500" * 60)
        for r in review_rows:
            print(f"  {r['original_name']:<40} Needs manual name split")
        print("\n  Please add these manually to NewClient.xlsx after processing auto rows.")

    if not auto_rows:
        print("\n  No auto-processable clients. All need manual review.")
        return

    # Show auto-processable rows
    print(f"\n  {len(auto_rows)} client(s) ready for auto-processing:")
    print(f"  {'#':<4} {'Name':<35} {'Folder Name'}")
    print("  " + "\u2500" * 70)
    for i, r in enumerate(auto_rows, 1):
        folder = f"{r['husband_last']},{r['husband_first']}"
        if r.get("wife_first"):
            if r.get("same_last_name") == "Y":
                folder += f" & {r['wife_first']}"
            elif r.get("wife_last"):
                folder += f" & {r['wife_last']},{r['wife_first']}"
            else:
                folder += f" & {r['wife_first']}"
        print(f"  {i:<4} {r['original_name']:<35} {folder}")

    ans = input(f"\n  Create folders for {len(auto_rows)} client(s)? (Y / N): ").strip().upper()
    if ans != "Y":
        print("  Cancelled.")
        return

    # Write to NewClient.xlsx
    nc_path = config.NEW_CLIENT_FILE
    new_rows = []
    for r in auto_rows:
        new_rows.append({
            "HusbandLast": r["husband_last"],
            "HusbandFirst": r["husband_first"],
            "WifeLast": r.get("wife_last", ""),
            "WifeFirst": r.get("wife_first", ""),
            "SameLastName": r.get("same_last_name", ""),
            "Kids": "",
        })

    if os.path.exists(nc_path):
        existing = pd.read_excel(nc_path, engine="openpyxl")
        # Filter out the template/instruction row
        existing = existing[
            ~((existing["HusbandLast"] == "Last name") & (existing["HusbandFirst"] == "First name"))
        ]
        new_df = pd.concat([existing, pd.DataFrame(new_rows)], ignore_index=True)
    else:
        new_df = pd.DataFrame(new_rows, columns=[
            "HusbandLast", "HusbandFirst", "WifeLast", "WifeFirst",
            "SameLastName", "Kids",
        ])

    new_df.to_excel(nc_path, index=False, engine="openpyxl")
    print(f"\n  Appended {len(new_rows)} row(s) to {nc_path}")

    # Call create_folders
    from create_folders import create_folders
    create_folders()

    # Mark as Handled in missing client list
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    headers = {c.value: c.column for c in ws[1]}
    name_col = headers.get("Name")
    status_col = headers.get("Reply Status")

    if name_col and status_col:
        handled_names = {r["original_name"].lower() for r in auto_rows}
        for row in ws.iter_rows(min_row=2):
            val = row[name_col - 1].value
            if val and str(val).strip().lower() in handled_names:
                row[status_col - 1].value = "Handled"
                _apply_row_color(ws, row[0].row, "Handled")

    wb.save(path)
    wb.close()
    print(f"\n  Marked {len(auto_rows)} client(s) as Handled in {os.path.basename(path)}")
