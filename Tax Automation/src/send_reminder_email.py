# =============================================================================
# send_reminder_email.py — Menu 6: Follow-up Reminders
# Scans 5. Waiting Info, triggers reminder emails for folders modified 3+ days
# ago, reads missing info from .txt filenames, extracts email from info PDF.
# =============================================================================

import os
import re
import json
import datetime
import tempfile
import subprocess

import config


# ── Fallback template (used when .docx template not yet created) ───────────────

_FALLBACK_TEMPLATE = """\
<html><body>
<p>Dear {{CLIENT_NAME}},</p>
<p>We are following up regarding your 2024 personal tax return.</p>
<p>To complete your filing, we still need the following information from you:</p>
<p>{{MISSING_INFO}}</p>
<p>Please provide these documents at your earliest convenience. If you have any
questions, do not hesitate to contact us.</p>
<p>Thank you,<br>ExampleCo CPA</p>
</body></html>
"""


# ── Log helpers ────────────────────────────────────────────────────────────────

def _load_log():
    if os.path.exists(config.REMINDER_LOG_FILE):
        with open(config.REMINDER_LOG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_log(log):
    with open(config.REMINDER_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2, ensure_ascii=False)


# ── Gemini client ──────────────────────────────────────────────────────────────

def _init_gemini():
    from google import genai
    from send_package_email import _load_gemini_key
    api_key = _load_gemini_key()
    if not api_key:
        raise RuntimeError(
            "Gemini API key not found. Run Menu 3 first to set it up."
        )
    return genai.Client(api_key=api_key)


# ── Credentials ────────────────────────────────────────────────────────────────

def _load_credentials():
    from send_package_email import load_credentials
    credentials = load_credentials()
    if not credentials:
        raise RuntimeError("Email credentials are not configured")
    return credentials


# ── Person name parsing (delegate to send_package_email) ──────────────────────

def _parse_person_names(folder_name):
    try:
        from send_package_email import parse_person_names
        return parse_person_names(folder_name)
    except Exception:
        return [folder_name.strip()]


# ── Missing items: read .txt filenames from folder root ───────────────────────

def _find_missing_items(folder_path):
    """Returns list of .txt filenames (no extension) found directly in folder."""
    items = []
    try:
        for entry in os.scandir(folder_path):
            if entry.is_file() and entry.name.lower().endswith(".txt"):
                items.append(os.path.splitext(entry.name)[0])
    except Exception:
        pass
    return sorted(items)


def _format_missing_info(items):
    """['rental', 'RRSP'] → 'rental related information\nRRSP related information'"""
    if not items:
        return "(no missing items listed)"
    return "\n".join(f"{item} related information" for item in items)


# ── Info PDF: locate and extract email ────────────────────────────────────────

def _find_info_pdf(taxcycle_dir, person_name):
    """
    Find <person_name>info.pdf (case-insensitive) in taxcycle_dir.
    Falls back to any single *info*.pdf if no name match.
    """
    if not os.path.isdir(taxcycle_dir):
        return None

    name_norm = person_name.lower().replace(" ", "")
    candidates = [
        f for f in os.listdir(taxcycle_dir)
        if f.lower().endswith(".pdf") and "info" in f.lower()
    ]

    if not candidates:
        return None

    # Prefer file whose name contains person's name
    for f in candidates:
        if name_norm in f.lower().replace(" ", ""):
            return os.path.join(taxcycle_dir, f)

    # Fallback: single match
    if len(candidates) == 1:
        return os.path.join(taxcycle_dir, candidates[0])

    return None


def _extract_email_from_pdf(gemini_client, pdf_path):
    """Use Gemini to extract client email from info PDF. Returns str or None."""
    import time
    from google.genai import types as genai_types

    prompt = (
        "This is a client information document. "
        "Extract the client's email address. "
        "Reply with ONLY the email address, nothing else. "
        "If no email is found, reply with: NOT_FOUND"
    )

    with open(pdf_path, "rb") as fh:
        pdf_bytes = fh.read()

    for attempt in range(config.GEMINI_MAX_RETRIES):
        try:
            response = gemini_client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=[
                    genai_types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf"),
                    prompt,
                ],
            )
            result = response.text.strip()
            if result and result != "NOT_FOUND" and "@" in result:
                return result
            return None
        except Exception as e:
            err = str(e).lower()
            if any(k in err for k in ("quota", "rate", "429", "resource_exhausted")):
                wait = (attempt + 1) * 60
                print(f"    [QUOTA] Waiting {wait}s before retry...")
                time.sleep(wait)
            else:
                print(f"    [ERROR] Gemini: {e}")
                return None

    return None


# ── Email template ─────────────────────────────────────────────────────────────

def _read_template():
    """Load .docx template → HTML. Falls back to built-in if file not found."""
    if not os.path.exists(config.REMINDER_TEMPLATE):
        print(f"  [WARN] Reminder template not found — using built-in fallback.")
        print(f"         Expected: {config.REMINDER_TEMPLATE}")
        return _FALLBACK_TEMPLATE

    import mammoth
    with open(config.REMINDER_TEMPLATE, "rb") as fh:
        return mammoth.convert_to_html(fh).value


def _fill_template(html, client_name, missing_html):
    """Replace {{CLIENT_NAME}} and {{MISSING_INFO}} placeholders."""
    html = html.replace("{{CLIENT_NAME}}", client_name)
    html = html.replace("{{MISSING_INFO}}", missing_html)
    return html


# ── Notepad review ─────────────────────────────────────────────────────────────

def _build_review_text(folder_name, person_name, missing_info, email_addr):
    sep = "=" * 72
    return (
        f"{sep}\n"
        f"REMINDER EMAIL REVIEW — {folder_name}\n"
        f"{sep}\n\n"
        f"[CLIENT_NAME]\n"
        f"  (Edit: display name used in greeting)\n"
        f"{person_name}\n\n"
        f"[MISSING_INFO]\n"
        f"  (Edit: one item per line, format: \"<item> related information\")\n"
        f"{missing_info}\n\n"
        f"[EMAIL]\n"
        f"  (Edit: recipient email address)\n"
        f"{email_addr or ''}\n\n"
        f"{sep}\n"
    )


def _parse_review_text(text, keys):
    results = {}
    for key in keys:
        pattern = (
            rf"\[{re.escape(key)}\][^\n]*\n"   # [KEY] line
            rf"[^\n]*\n"                          # instruction line
            rf"(.*?)"                             # captured value
            rf"(?=\n\s*\[|\n\s*={{3,}}|\Z)"
        )
        m = re.search(pattern, text, re.DOTALL)
        if m:
            val = m.group(1).strip()
            val = re.sub(r"\n\s*={3,}.*", "", val, flags=re.DOTALL).strip()
            results[key] = val
        else:
            results[key] = ""
    return results


def _edit_in_notepad(text):
    fd, path = tempfile.mkstemp(suffix=".txt", prefix="reminder_review_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        subprocess.run(["notepad.exe", path], check=True)
        with open(path, encoding="utf-8") as f:
            return f.read()
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass


# ── Email send ─────────────────────────────────────────────────────────────────

def _build_message(sender, recipient, subject, html_body):
    import email.mime.multipart
    import email.mime.text
    msg = email.mime.multipart.MIMEMultipart("alternative")
    msg["From"]    = sender
    msg["To"]      = recipient
    msg["Subject"] = subject
    msg.attach(email.mime.text.MIMEText(html_body, "html", "utf-8"))
    return msg


def _send_email(msg, creds, recipient):
    from send_package_email import send_one
    success, error = send_one(msg, recipient, creds)
    if not success:
        raise RuntimeError(error)


# ── Main ───────────────────────────────────────────────────────────────────────

def send_reminder_email():
    waiting_path = config.WAITING_INFO_PATH

    if not os.path.isdir(waiting_path):
        print(f"\n  [ERROR] Folder not found:\n  {waiting_path}")
        return

    # Load log + credentials
    log = _load_log()
    try:
        creds = _load_credentials()
    except Exception as e:
        print(f"\n  [ERROR] Could not load credentials: {e}")
        return

    sender = f"{creds['sender_name']} <{creds['sender_email']}>"

    # Init Gemini
    try:
        gemini_client = _init_gemini()
        print("  Gemini OK.")
    except Exception as e:
        import traceback
        print(f"\n  [ERROR] Gemini init failed: {type(e).__name__}: {e}")
        traceback.print_exc()
        return

    # Load template once
    template_html = _read_template()

    # Scan client folders
    now           = datetime.datetime.now()
    trigger_days  = config.REMINDER_TRIGGER_DAYS
    max_sends     = config.REMINDER_MAX_SENDS

    try:
        client_entries = sorted(
            [e for e in os.scandir(waiting_path) if e.is_dir()],
            key=lambda e: e.name,
        )
    except Exception as e:
        print(f"\n  [ERROR] Cannot scan waiting folder: {e}")
        return

    if not client_entries:
        print("\n  No client folders found in 5. Waiting Info.")
        return

    eligible = 0

    for entry in client_entries:
        folder_name = entry.name
        folder_path = entry.path

        mtime     = datetime.datetime.fromtimestamp(entry.stat().st_mtime)
        days_old  = (now - mtime).days

        # ── Eligibility checks ─────────────────────────────────────────────────
        if days_old < trigger_days:
            print(f"\n  [SKIP] {folder_name} — modified {days_old}d ago "
                  f"(trigger: {trigger_days}d)")
            continue

        folder_log = log.get(folder_name, {"send_count": 0, "history": []})
        send_count = folder_log.get("send_count", 0)

        if send_count >= max_sends:
            print(f"\n  [SKIP] {folder_name} — max reminders reached "
                  f"({send_count}/{max_sends})")
            continue

        eligible += 1
        print(f"\n{'─' * 60}")
        print(f"  Folder   : {folder_name}")
        print(f"  Modified : {mtime.strftime('%Y-%m-%d %H:%M')}  ({days_old}d ago)")
        print(f"  Reminders: {send_count}/{max_sends} sent")

        # ── Parse persons ──────────────────────────────────────────────────────
        persons = _parse_person_names(folder_name)
        print(f"  Persons  : {', '.join(persons)}")

        # ── Missing items (shared across entire folder) ────────────────────────
        missing_items = _find_missing_items(folder_path)
        if missing_items:
            print(f"  Missing  : {', '.join(missing_items)}")
        else:
            print("  [WARN] No .txt files found — missing info will be blank.")
        missing_info = _format_missing_info(missing_items)

        taxcycle_dir = os.path.join(folder_path, config.TAXCYCLE_SUBFOLDER)
        any_sent     = False

        # ── Per-person loop ────────────────────────────────────────────────────
        for person in persons:
            print(f"\n  ── {person} ──")

            # Locate info PDF
            info_pdf = _find_info_pdf(taxcycle_dir, person)
            if info_pdf:
                print(f"  Info PDF : {os.path.basename(info_pdf)}")
            else:
                print(f"  [WARN] No info PDF found for {person} in:\n"
                      f"         {taxcycle_dir}")

            # Extract email via Gemini
            extracted_email = None
            if info_pdf:
                print("  Extracting email via Gemini...")
                extracted_email = _extract_email_from_pdf(gemini_client, info_pdf)
                if extracted_email:
                    print(f"  Email    : {extracted_email}")
                else:
                    print("  [WARN] Could not extract email from PDF.")

            # Notepad review
            review_text = _build_review_text(
                folder_name, person, missing_info, extracted_email
            )
            print("\n  Opening Notepad for review — close to continue...")
            edited = _edit_in_notepad(review_text)

            # Parse edited fields
            fields       = _parse_review_text(edited, ["CLIENT_NAME", "MISSING_INFO", "EMAIL"])
            client_name  = fields["CLIENT_NAME"].strip()  or person
            missing_edit = fields["MISSING_INFO"].strip()  or missing_info
            email_addr   = fields["EMAIL"].strip()

            if not email_addr:
                print("  [SKIP] No email address — skipping.")
                continue

            # Build HTML body
            missing_html = "<br>".join(
                line.strip() for line in missing_edit.splitlines() if line.strip()
            )
            html_body = _fill_template(template_html, client_name, missing_html)
            subject   = config.REMINDER_SUBJECT.format(client_name=client_name)

            # Confirm
            print(f"\n  Ready to send:")
            print(f"    To      : {email_addr}")
            print(f"    Subject : {subject}")
            preview = missing_edit[:80] + ("..." if len(missing_edit) > 80 else "")
            print(f"    Missing : {preview}")

            ans = input("\n  Send this email? (Y / N): ").strip().upper()
            if ans != "Y":
                print("  Skipped.")
                continue

            try:
                msg = _build_message(sender, email_addr, subject, html_body)
                _send_email(msg, creds, email_addr)
                print(f"  Sent to {email_addr}.")
                any_sent = True
            except Exception as e:
                print(f"  [ERROR] Send failed: {e}")

        # ── Update log once after all persons in this folder ──────────────────
        if any_sent:
            folder_log["send_count"] = send_count + 1
            folder_log.setdefault("history", []).append(now.isoformat())
            log[folder_name] = folder_log
            _save_log(log)
            print(f"\n  Reminder count updated: "
                  f"'{folder_name}' → {folder_log['send_count']}/{max_sends}")

    if eligible == 0:
        print("\n  No eligible clients found for follow-up reminders.")

    print(f"\n{'─' * 60}")
    print("  Done.")
