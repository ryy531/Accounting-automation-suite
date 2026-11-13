# =============================================================================
# send_efile_email.py — Menu 7: Efile Confirmation Emails
# Scans 8. Efiled recursively for EFILEReceipt PDFs, derives person names
# from receipt filenames, extracts email from T1 via Gemini.
# If no email found for a person, falls back to family members' emails.
# Generates CSV for user review in Excel, deduplicates against prior logs.
# =============================================================================

import os
import re
import csv
import json
import glob as glob_mod
import tempfile
import subprocess
from datetime import datetime

import config

# ── Logging ────────────────────────────────────────────────────────────────────

def _log_path() -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(config.SCRIPTS_LOGS_PATH, f"efile_email_{ts}.log")

def _followup_path() -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(config.SCRIPTS_LOGS_PATH, f"efile_followup_{ts}.txt")

def _csv_path() -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(config.SCRIPTS_LOGS_PATH, f"efile_review_{ts}.csv")


# ── Dedup: load previously sent clients from logs ─────────────────────────────

def _load_sent_clients() -> set:
    """
    Scan all efile_email_*.log files and return set of "folder|person"
    for successfully sent entries.  Used to skip duplicates.
    """
    sent = set()
    pattern = os.path.join(config.SCRIPTS_LOGS_PATH, "efile_email_*.log")
    for log_path in sorted(glob_mod.glob(pattern)):
        try:
            with open(log_path, encoding="utf-8") as f:
                for line in f:
                    # Match: [OK]  folder_name | person_name | ...
                    m = re.match(r"\[OK\]\s+(.+?)\s*\|\s*(.+?)\s*\|", line)
                    if m:
                        key = f"{m.group(1).strip()}|{m.group(2).strip()}"
                        sent.add(key.lower())
        except Exception:
            continue
    return sent


# ── Gemini + credentials ───────────────────────────────────────────────────────

def _init_gemini():
    from google import genai
    from send_package_email import _load_gemini_key
    api_key = _load_gemini_key()
    if not api_key:
        raise RuntimeError("Gemini API key not found. Run Menu 3 first to set it up.")
    return genai.Client(api_key=api_key)


def _load_credentials():
    from send_package_email import load_credentials
    credentials = load_credentials()
    if not credentials:
        raise RuntimeError("Email credentials are not configured")
    return credentials


# ── Template ───────────────────────────────────────────────────────────────────

def _read_template() -> str:
    import mammoth
    if not os.path.exists(config.EFILE_TEMPLATE):
        raise FileNotFoundError(f"Efile template not found:\n  {config.EFILE_TEMPLATE}")
    with open(config.EFILE_TEMPLATE, "rb") as fh:
        return mammoth.convert_to_html(fh).value


def _fill_template(html: str, client_name: str) -> str:
    return html.replace("{{CLIENT_NAME}}", client_name)


# ── Receipt discovery ──────────────────────────────────────────────────────────

def _find_all_receipts(folder_path: str) -> list:
    found = []
    for root, _dirs, files in os.walk(folder_path):
        for f in files:
            if f.lower().endswith(".pdf") and "efilereceipt" in f.lower():
                found.append(os.path.join(root, f))
    return found


def _person_from_receipt(filename: str) -> str:
    idx = filename.lower().find("efilereceipt")
    if idx > 0:
        return filename[:idx].strip(" -_")
    return os.path.splitext(filename)[0]


# ── T1 PDF finder ──────────────────────────────────────────────────────────────

def _find_t1_pdf(pdfs: list, person_name: str) -> str:
    clean = re.sub(r"[,.]", " ", person_name)
    parts = [p.lower() for p in clean.split() if p]

    def _is_t1(fname):
        fl = fname.lower()
        return "t1" in fl and "t183" not in fl and "t1135" not in fl

    for path in pdfs:
        fname = os.path.basename(path).lower()
        if _is_t1(fname) and all(p in fname for p in parts):
            return path

    if len(parts) >= 2:
        for path in pdfs:
            fname = os.path.basename(path).lower()
            if _is_t1(fname):
                if sum(1 for p in parts if p in fname) >= len(parts) - 1:
                    return path

    return ""


# ── CSV review: generate / open / read ─────────────────────────────────────────

def _generate_review_csv(batch: list, csv_path: str):
    """Write batch items to CSV for Excel review."""
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Action", "Folder", "Person", "Email", "Files", "Notes"])
        for item in batch:
            # Action
            has_email = bool(item.get("email"))
            has_receipts = bool(item.get("receipts"))
            if item.get("already_sent"):
                action = "SENT"
            elif has_email and has_receipts:
                action = "SEND"
            else:
                action = "SKIP"

            # Email
            emails = item.get("email", "")
            if isinstance(emails, list):
                email_str = ", ".join(emails)
            else:
                email_str = emails or ""

            # Files (filenames only, semicolon-separated)
            files = "; ".join(os.path.basename(r) for r in item.get("receipts", []))

            # Notes
            notes = _build_notes(item)

            w.writerow([action, item["folder"], item["person"],
                        email_str, files, notes])


def _build_notes(item: dict) -> str:
    parts = []
    if item.get("already_sent"):
        parts.append("already sent")
    if item.get("is_fallback"):
        parts.append("fallback email")
    if not item.get("email") and not item.get("already_sent"):
        parts.append("no email")
    if not item.get("receipts"):
        parts.append("no receipt")
    if item.get("no_t1"):
        parts.append("no T1 found")
    return "; ".join(parts)


def _open_in_excel(csv_path: str):
    """Open CSV in Excel and block until user closes it."""
    subprocess.run(
        ["cmd", "/c", "start", "/wait", "excel", csv_path],
        check=True,
    )


def _read_review_csv(csv_path: str, original_batch: list) -> list:
    """
    Read back the CSV edited by user.  Match rows back to original batch
    by (folder, person).  Return updated batch with user edits applied.
    """
    # Read CSV rows
    rows = []
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    # Build lookup: (folder.lower, person.lower) → csv row
    csv_map = {}
    for row in rows:
        key = (row.get("Folder", "").strip().lower(),
               row.get("Person", "").strip().lower())
        csv_map[key] = row

    updated = []
    for item in original_batch:
        new_item = dict(item)
        key = (item["folder"].lower(), item["person"].lower())

        if key in csv_map:
            row = csv_map[key]

            # Action
            new_item["action"] = row.get("Action", "SKIP").strip().upper()

            # Email
            email_raw = row.get("Email", "").strip()
            if not email_raw:
                new_item["email"] = ""
            else:
                addrs = [a.strip() for a in email_raw.split(",") if a.strip()]
                new_item["email"] = addrs if len(addrs) > 1 else addrs[0]

            # Files — user may have edited the Files column
            files_raw = row.get("Files", "").strip()
            if files_raw:
                filenames = [f.strip() for f in files_raw.split(";") if f.strip()]
                new_item["_edited_files"] = filenames
        else:
            # Row was deleted from CSV — skip
            new_item["action"] = "SKIP"

        updated.append(new_item)

    return updated


def _resolve_file(folder_path: str, filename: str) -> str:
    """
    Find a file by basename within the client folder (recursive).
    Returns full path or "" if not found.
    """
    for root, _dirs, files in os.walk(folder_path):
        for f in files:
            if f == filename:
                return os.path.join(root, f)
    # Case-insensitive fallback
    for root, _dirs, files in os.walk(folder_path):
        for f in files:
            if f.lower() == filename.lower():
                return os.path.join(root, f)
    return ""


# ── Email build & send ─────────────────────────────────────────────────────────

def _build_message(sender: str, recipients: list, subject: str,
                   html_body: str, cid_images: list, attachment_paths: list):
    import email.mime.multipart
    import email.mime.text
    import email.mime.application
    import email.mime.image

    msg = email.mime.multipart.MIMEMultipart("mixed")
    msg["From"]    = sender
    msg["To"]      = ", ".join(recipients)
    msg["Subject"] = subject

    related = email.mime.multipart.MIMEMultipart("related")
    related.attach(email.mime.text.MIMEText(html_body, "html", "utf-8"))
    for cid, subtype, data in cid_images:
        img = email.mime.image.MIMEImage(data, _subtype=subtype)
        img.add_header("Content-ID", f"<{cid}>")
        img.add_header("Content-Disposition", "inline")
        related.attach(img)
    msg.attach(related)

    for path in attachment_paths:
        with open(path, "rb") as fh:
            data = fh.read()
        part = email.mime.application.MIMEApplication(data, _subtype="pdf")
        part.add_header(
            "Content-Disposition", "attachment",
            filename=os.path.basename(path),
        )
        msg.attach(part)

    return msg


def _send_email(msg, creds: dict, recipients: list):
    from send_package_email import send_one
    success, error = send_one(msg, recipients, creds)
    if not success:
        raise RuntimeError(error)


# ── Main ───────────────────────────────────────────────────────────────────────

def send_efile_email():
    efiled_path = config.EFILED_PATH
    log_file    = _log_path()
    csv_file    = _csv_path()

    if not os.path.isdir(efiled_path):
        print(f"\n  [ERROR] Folder not found:\n  {efiled_path}")
        return

    try:
        creds = _load_credentials()
    except Exception as e:
        print(f"\n  [ERROR] Could not load credentials: {e}")
        return

    sender = f"{creds['sender_name']} <{creds['sender_email']}>"

    try:
        gemini_client = _init_gemini()
        print("  Gemini OK.")
    except Exception as e:
        print(f"\n  [ERROR] {e}")
        return

    try:
        template_html = _read_template()
    except FileNotFoundError as e:
        print(f"\n  [ERROR] {e}")
        return

    from send_package_email import extract_t1_info, extract_cid_images

    # ── Dedup: load previously sent ──────────────────────────────────────────
    sent_clients = _load_sent_clients()
    if sent_clients:
        print(f"  Found {len(sent_clients)} previously sent record(s) in logs.")

    # ── Scan client folders ──────────────────────────────────────────────────
    try:
        client_entries = sorted(
            [e for e in os.scandir(efiled_path) if e.is_dir()],
            key=lambda e: e.name,
        )
    except Exception as e:
        print(f"\n  [ERROR] Cannot scan efiled folder: {e}")
        return

    if not client_entries:
        print("\n  No client folders found in 8. Efiled.")
        return

    print(f"\n  Scanning {len(client_entries)} folder(s)...\n")

    batch    = []
    skipped  = []

    for entry in client_entries:
        folder_name  = entry.name
        folder_path  = entry.path
        taxcycle_dir = os.path.join(folder_path, config.TAXCYCLE_SUBFOLDER)

        all_pdfs = []
        if os.path.isdir(taxcycle_dir):
            all_pdfs = [
                os.path.join(taxcycle_dir, f)
                for f in os.listdir(taxcycle_dir)
                if f.lower().endswith(".pdf")
            ]

        all_receipts = _find_all_receipts(folder_path)

        if not all_receipts:
            print(f"  [{folder_name}] [SKIP] No EFILEReceipt PDFs found")
            skipped.append({
                "folder": folder_name, "person": "(entire folder)",
                "email": "", "receipts": [], "is_fallback": False,
            })
            continue

        receipt_groups: dict[str, list] = {}
        for path in all_receipts:
            person = _person_from_receipt(os.path.basename(path))
            receipt_groups.setdefault(person, []).append(path)

        person_emails: dict[str, str] = {}
        folder_batch  = []

        for person, receipts in receipt_groups.items():
            # Dedup check
            dedup_key = f"{folder_name}|{person}".lower()
            already_sent = dedup_key in sent_clients

            t1_path = _find_t1_pdf(all_pdfs, person)
            email_addr = ""
            if t1_path:
                print(f"  [{folder_name}] {person}: extracting email...")
                info = extract_t1_info(gemini_client, t1_path)
                email_addr = info.get("email", "")
                if email_addr == "unknown":
                    email_addr = ""
            else:
                print(f"  [{folder_name}] {person}: [WARN] no T1 found")

            if email_addr:
                person_emails[person] = email_addr

            folder_batch.append({
                "folder":       folder_name,
                "person":       person,
                "email":        email_addr,
                "receipts":     receipts,
                "is_fallback":  False,
                "already_sent": already_sent,
                "no_t1":        not t1_path,
                "folder_path":  folder_path,
            })

        fallback_emails = list(person_emails.values())
        for item in folder_batch:
            if not item["email"] and fallback_emails:
                item["email"]       = fallback_emails
                item["is_fallback"] = True

        batch.extend(folder_batch)

    # ── Always create log ────────────────────────────────────────────────────
    log = open(log_file, "w", encoding="utf-8")
    log.write(f"Efile Confirmation Email Log — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    log.write(f"{'=' * 80}\n\n")

    no_receipt_folders = [s for s in skipped if s["person"] == "(entire folder)"]
    if no_receipt_folders:
        log.write("NO EFILERECEIPT (entire folder skipped):\n\n")
        for item in no_receipt_folders:
            log.write(f"[SKIP] {item['folder']} | no efile receipt found in folder\n")
        log.write("\n")

    if not batch:
        log.write(f"{'=' * 80}\n")
        log.write(f"Sent: 0   Failed: 0   Skipped: {len(skipped)}\n")
        log.close()
        print(f"\n  No persons with receipts found.")
        print(f"  Log: {log_file}")
        _write_followup(skipped)
        return

    # ── Generate CSV & open in Excel for review ─────────────────────────────
    _generate_review_csv(batch, csv_file)

    already_sent_count = sum(1 for b in batch if b.get("already_sent"))
    new_count = len(batch) - already_sent_count

    print(f"\n  Found {len(batch)} person(s): {new_count} new, {already_sent_count} already sent.")
    print(f"  Opening CSV in Excel for review — close Excel to continue...")
    print(f"  CSV: {csv_file}")

    _open_in_excel(csv_file)

    # Read back user edits
    batch = _read_review_csv(csv_file, batch)

    # ── Classify into sendable / skipped ─────────────────────────────────────
    col_w = [30, 22, 35, 7]
    print(f"\n{'─' * 100}")
    print(f"  {'Folder':<{col_w[0]}}  {'Person':<{col_w[1]}}  {'Email':<{col_w[2]}}  {'Action'}")
    print(f"{'─' * 100}")

    sendable = []

    for item in batch:
        action = item.get("action", "SKIP")

        emails = item.get("email", "")
        if isinstance(emails, list):
            email_disp = ", ".join(emails)
        else:
            email_disp = emails or "(blank)"

        print(f"  {item['folder']:<{col_w[0]}}  {item['person']:<{col_w[1]}}"
              f"  {email_disp:<{col_w[2]}}  {action}")

        if action == "SEND":
            sendable.append(item)
        else:
            skipped.append(item)

    print(f"{'─' * 100}")
    print(f"\n  Will send : {len(sendable)}")
    print(f"  Will skip : {len(skipped)}")

    if not sendable:
        log.write("NO EMAILS TO SEND:\n\n")
        for item in skipped:
            log.write(f"[SKIP] {item['folder']} | {item['person']} | {_email_str(item)} | {_skip_reason(item)}\n")
        log.write(f"\n{'=' * 80}\n")
        log.write(f"Sent: 0   Failed: 0   Skipped: {len(skipped)}\n")
        log.close()
        print(f"\n  Nothing to send.")
        print(f"  Log: {log_file}")
        _write_followup(skipped)
        return

    ans = input("\n  Send all? (Y / N): ").strip().upper()
    if ans != "Y":
        log.write("USER CANCELLED — no emails sent.\n\n")
        for item in sendable:
            log.write(f"[SKIP] {item['folder']} | {item['person']} | {_email_str(item)} | cancelled by user\n")
        for item in skipped:
            log.write(f"[SKIP] {item['folder']} | {item['person']} | {_email_str(item)} | {_skip_reason(item)}\n")
        log.write(f"\n{'=' * 80}\n")
        log.write(f"Sent: 0   Failed: 0   Skipped: {len(sendable) + len(skipped)}\n")
        log.close()
        print("  Cancelled.")
        print(f"  Log: {log_file}")
        _write_followup(sendable + skipped)
        return

    # ── Resolve file paths & send ────────────────────────────────────────────
    print()
    sent_ok = sent_err = 0

    for item in sendable:
        client_name = item["person"]
        folder_path = item.get("folder_path", os.path.join(efiled_path, item["folder"]))

        # Resolve attachment files
        attachments = []
        edited_files = item.get("_edited_files")
        if edited_files is not None:
            # User edited the Files column — resolve each filename
            for fname in edited_files:
                full = _resolve_file(folder_path, fname)
                if full:
                    attachments.append(full)
                else:
                    print(f"  [WARN] File not found: {fname} in {item['folder']}")
        else:
            # Use original receipts
            attachments = list(item.get("receipts", []))

        if not attachments:
            print(f"  [SKIP] {item['folder']} / {client_name}: no files to attach")
            log.write(f"[SKIP] {item['folder']} | {client_name} | no files resolved\n")
            skipped.append({**item, "error": "no files resolved"})
            continue

        filled_html         = _fill_template(template_html, client_name)
        send_html, cid_imgs = extract_cid_images(filled_html)
        subject             = config.EFILE_SUBJECT.format(client_name=client_name)

        recipients = item["email"] if isinstance(item["email"], list) else [item["email"]]

        try:
            msg = _build_message(sender, recipients, subject, send_html, cid_imgs, attachments)
            _send_email(msg, creds, recipients)
            file_names = ", ".join(os.path.basename(a) for a in attachments)
            fb_note = " [fallback]" if item.get("is_fallback") else ""
            print(f"  OK  {item['folder']} / {client_name} → {', '.join(recipients)}{fb_note}")
            print(f"      Attached: {file_names}")
            log.write(f"[OK]  {item['folder']} | {client_name} | {', '.join(recipients)}{fb_note}\n")
            log.write(f"      Attached: {file_names}\n")
            sent_ok += 1
        except Exception as e:
            print(f"  ERR {item['folder']} / {client_name}: {e}")
            log.write(f"[ERR] {item['folder']} | {client_name} | {', '.join(recipients)} | {e}\n")
            sent_err += 1
            skipped.append({**item, "error": str(e)})

    # ── Log skipped items ────────────────────────────────────────────────────
    if skipped:
        log.write(f"\n{'─' * 80}\n")
        log.write("SKIPPED / FAILED:\n\n")
        for item in skipped:
            log.write(f"[SKIP] {item['folder']} | {item['person']} | {_email_str(item)} | {_skip_reason(item)}\n")

    log.write(f"\n{'=' * 80}\n")
    log.write(f"Sent: {sent_ok}   Failed: {sent_err}   Skipped: {len(skipped)}\n")
    log.close()

    _write_followup(skipped)

    print(f"\n{'─' * 60}")
    print(f"  Sent: {sent_ok}   Failed: {sent_err}   Skipped: {len(skipped)}")
    print(f"  Log: {log_file}")
    print("  Done.")


# ── Helpers for log / follow-up ──────────────────────────────────────────────

def _skip_reason(item: dict) -> str:
    parts = []
    if item.get("person") == "(entire folder)":
        return "no efile receipt in folder"
    if item.get("already_sent"):
        parts.append("already sent")
    if item.get("is_fallback"):
        parts.append("fallback email")
    if not item.get("email"):
        parts.append("no email")
    if not item.get("receipts") and not item.get("_edited_files"):
        parts.append("no receipt")
    if item.get("no_t1"):
        parts.append("no T1 found")
    if item.get("error"):
        parts.append(f"send failed: {item['error']}")
    return ", ".join(parts) or "skipped in review"


def _email_str(item: dict) -> str:
    emails = item.get("email", "")
    if isinstance(emails, list):
        return ", ".join(emails) + " *"
    return emails or "(blank)"


def _write_followup(skipped: list):
    if not skipped:
        return
    fp_path = _followup_path()
    with open(fp_path, "w", encoding="utf-8") as fp:
        fp.write(f"Efile Follow-up — Failed/Skipped Clients\n")
        fp.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        fp.write(f"{'=' * 110}\n\n")

        fp.write(f"{'#':<4} {'Folder':<35} {'Person':<24} {'Email':<35} {'Reason'}\n")
        fp.write(f"{'─' * 110}\n")

        for idx, item in enumerate(skipped, 1):
            reason = _skip_reason(item)
            email_str = _email_str(item)
            fp.write(f"{idx:<4} {item['folder']:<35} {item['person']:<24} {email_str:<35} {reason}\n")

        fp.write(f"\n{'─' * 110}\n")
        fp.write(f"Total: {len(skipped)} client(s) need manual follow-up.\n")
        fp.write("* = fallback email (from family member)\n")

    print(f"  Follow-up: {fp_path}")
