"""
connectors/email_client.py — Shared IMAP + SMTP email client.

Credentials: loaded from credentials.json (same as existing scripts).
TODO: migrate to config.settings for centralized credential management.
"""

import os
import json
import ssl
import smtplib
import imaplib
import re
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from email.utils import formatdate, make_msgid
from datetime import datetime

try:
    from .gmail_api_transport import GmailApiTransport
except ImportError:  # Supports legacy scripts importing this module directly.
    from gmail_api_transport import GmailApiTransport


class EmailClient:
    """IMAP + SMTP client with auto-save-to-sent."""

    def __init__(self, credentials_path: str | None = None,
                 credentials: dict | None = None):
        if credentials is not None:
            self._creds = credentials
        elif credentials_path:
            self._creds = self._load_credentials(credentials_path)
        else:
            raise ValueError("credentials_path or credentials is required")
        auth_mode = str(self._creds.get("auth_mode", "password")).lower()
        if auth_mode not in ("password", "gmail_api"):
            raise ValueError("auth_mode must be 'password' or 'gmail_api'")
        self._gmail = (
            GmailApiTransport(self._creds)
            if auth_mode == "gmail_api"
            else None
        )
        self._imap = None

    @classmethod
    def from_credentials(cls, credentials: dict):
        """Create a client from already-loaded credentials."""
        return cls(credentials=credentials)

    @classmethod
    def from_settings(cls, sender_name: str, password: str | None = None):
        """Create a client from centralized settings for business scripts."""
        try:
            from ..config import settings
        except ImportError:
            import config.settings as settings

        auth_mode = getattr(settings, "EMAIL_AUTH_MODE", "password")
        credentials = {
            "auth_mode": auth_mode,
            "sender_name": sender_name,
            "sender_email": settings.EMAIL_ADDRESS,
        }
        if auth_mode == "gmail_api":
            credentials.update({
                "oauth_client_secret_path": (
                    settings.GMAIL_OAUTH_CLIENT_SECRET_PATH
                ),
                "oauth_refresh_token_path": (
                    settings.GMAIL_OAUTH_REFRESH_TOKEN_PATH
                ),
            })
        else:
            credentials.update({
                "sender_password": password or settings.EMAIL_PASSWORD,
                "smtp_host": settings.EMAIL_SMTP_SERVER,
                "smtp_port": settings.EMAIL_SMTP_PORT,
                "imap_host": settings.EMAIL_IMAP_SERVER,
                "imap_port": settings.EMAIL_IMAP_PORT,
            })
        return cls(credentials=credentials)

    # ── Credentials ────────────────────────────────────────────────────────────
    # TODO: migrate to config.settings

    @staticmethod
    def _load_credentials(path: str) -> dict:
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    # ── IMAP ───────────────────────────────────────────────────────────────────

    def connect_imap(self):
        """Connect and authenticate to IMAP server via SSL."""
        if self._gmail:
            self._gmail.connect()
            return
        creds = self._creds
        host = creds.get("imap_host") or creds["smtp_host"].replace("smtp.", "imap.", 1)
        port = int(creds.get("imap_port", 993))
        ctx = ssl.create_default_context()
        self._imap = imaplib.IMAP4_SSL(
            host,
            port,
            ssl_context=ctx,
            timeout=60,
        )
        self._imap.login(creds["sender_email"], creds["sender_password"])

    def fetch_unread(self, folder: str = "INBOX") -> list[dict]:
        """
        Fetch unread (UNSEEN) messages from the given folder.
        Returns list of dicts with keys: uid, subject, from, date, body_text.
        """
        import email

        if self._gmail:
            return [
                self._message_summary(uid, email.message_from_bytes(raw))
                for uid, raw in self._gmail.fetch_unread_raw(folder)
            ]

        imap = self._require_imap()
        imap.select(folder, readonly=True)
        typ, data = imap.search(None, "UNSEEN")
        if typ != "OK":
            return []

        uids = [u for u in data[0].split() if u]
        messages = []
        for uid in uids:
            typ, msg_data = imap.fetch(uid, "(RFC822)")
            if typ != "OK":
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            messages.append(self._message_summary(uid, msg))
        return messages

    def fetch_since(self, since_date, folder: str = "INBOX") -> list[dict]:
        """Fetch all messages in a folder on or after a calendar date."""
        import email

        if self._gmail:
            query = f"after:{since_date.strftime('%Y/%m/%d')}"
            return [
                self._message_summary(uid, email.message_from_bytes(raw))
                for uid, raw in self._gmail.fetch_raw(query, folder)
            ]

        imap = self._require_imap()
        imap.select(folder, readonly=True)
        since_str = since_date.strftime("%d-%b-%Y")
        typ, data = imap.search(None, f'(SINCE "{since_str}")')
        if typ != "OK" or not data or not data[0]:
            return []

        messages = []
        for uid in data[0].split():
            typ, msg_data = imap.fetch(uid, "(RFC822)")
            if typ != "OK" or not msg_data:
                continue
            raw = next(
                (
                    item[1]
                    for item in msg_data
                    if isinstance(item, tuple) and len(item) >= 2
                ),
                None,
            )
            if raw:
                messages.append(
                    self._message_summary(uid, email.message_from_bytes(raw))
                )
        return messages

    def save_sent(self, raw_bytes: bytes):
        """
        Upload a raw message to the IMAP Sent folder.
        Tries folders in order: INBOX.Sent → Sent → Sent Items → Sent Messages.
        Non-critical: prints warning on failure, does not raise.
        """
        if self._gmail:
            return
        imap = self._require_imap()
        try:
            for folder in ("INBOX.Sent", "Sent", "Sent Items", "Sent Messages"):
                try:
                    typ, _ = imap.append(
                        folder, "\\Seen",
                        imaplib.Time2Internaldate(datetime.now().astimezone()),
                        raw_bytes,
                    )
                    if typ == "OK":
                        return
                except Exception:
                    continue
            print("  [WARN] IMAP: could not find Sent folder.")
        except Exception as exc:
            print(f"  [WARN] IMAP save failed (non-critical): {exc}")

    def save_to_folder(self, raw_bytes: bytes, folder: str) -> bool:
        """Append a message to an IMAP folder, creating it when necessary."""
        if self._gmail:
            try:
                return self._gmail.add_raw_to_label(raw_bytes, folder)
            except Exception as exc:
                print(
                    f"  [WARN] Gmail archive failed for {folder!r}: "
                    f"{type(exc).__name__}"
                )
                return False
        imap = self._require_imap()
        internal_date = imaplib.Time2Internaldate(datetime.now().astimezone())

        try:
            resolved_folder = folder
            if not folder.upper().startswith("INBOX"):
                list_type, list_data = imap.list()
                delimiter = "."
                if list_type == "OK":
                    for item in list_data or []:
                        line = item.decode(errors="replace")
                        match = re.search(r'\)\s+"([^"]+)"\s+', line)
                        if match:
                            delimiter = match.group(1)
                            break
                resolved_folder = f"INBOX{delimiter}{folder}"

            create_type, _ = imap.create(resolved_folder)
            if create_type not in ("OK", "NO"):
                print(
                    f"  [WARN] IMAP: could not create folder "
                    f"{resolved_folder!r}."
                )
                return False
            subscribe_type, _ = imap.subscribe(resolved_folder)
            if subscribe_type not in ("OK", "NO"):
                print(
                    f"  [WARN] IMAP: could not subscribe to folder "
                    f"{resolved_folder!r}."
                )

            typ, _ = imap.append(
                resolved_folder,
                "\\Seen",
                internal_date,
                raw_bytes,
            )
            if typ == "OK":
                return True
            print(
                f"  [WARN] IMAP: could not append to folder "
                f"{resolved_folder!r}."
            )
            return False
        except Exception as exc:
            print(f"  [WARN] IMAP archive failed for {folder!r}: {exc}")
            return False

    def move_inbox_message(self, message_id: str, folder: str,
                           attempts: int = 5, delay_seconds: int = 3) -> bool:
        """Move a delivered INBOX message into a report folder by Message-ID."""
        if self._gmail:
            return self._gmail.move_inbox_by_message_id(
                message_id,
                folder,
                attempts=attempts,
                delay_seconds=delay_seconds,
            )
        imap = self._require_imap()
        resolved_folder = self._resolve_folder(folder)

        try:
            create_type, _ = imap.create(resolved_folder)
            if create_type not in ("OK", "NO"):
                print(
                    f"  [WARN] IMAP: could not create folder "
                    f"{resolved_folder!r}."
                )
                return False
            subscribe_type, _ = imap.subscribe(resolved_folder)
            if subscribe_type not in ("OK", "NO"):
                print(
                    f"  [WARN] IMAP: could not subscribe to folder "
                    f"{resolved_folder!r}."
                )

            for attempt in range(attempts):
                select_type, _ = imap.select("INBOX")
                if select_type != "OK":
                    return False

                search_type, search_data = imap.search(
                    None,
                    "HEADER",
                    "Message-ID",
                    message_id,
                )
                sequence_ids = (
                    search_data[0].split()
                    if search_type == "OK" and search_data
                    else []
                )
                if sequence_ids:
                    sequence_id = sequence_ids[-1]
                    copy_type, _ = imap.copy(sequence_id, resolved_folder)
                    if copy_type != "OK":
                        return False
                    store_type, _ = imap.store(
                        sequence_id,
                        "+FLAGS.SILENT",
                        "(\\Deleted)",
                    )
                    if store_type != "OK":
                        return False
                    expunge_type, _ = imap.expunge()
                    return expunge_type == "OK"

                if attempt + 1 < attempts:
                    time.sleep(delay_seconds)

            return False
        except Exception as exc:
            print(f"  [WARN] IMAP move failed for {folder!r}: {exc}")
            return False

    def close_imap(self):
        """Logout cleanly from IMAP."""
        if self._gmail:
            return
        if self._imap:
            try:
                self._imap.close()
            except Exception:
                pass
            try:
                self._imap.logout()
            except Exception:
                pass
            self._imap = None

    # ── SMTP ───────────────────────────────────────────────────────────────────

    def send(self, to: str | list, subject: str, html_body: str,
             attachments: list[str] | None = None):
        """
        Send an HTML email with optional file attachments.
        On success, automatically saves a copy to IMAP Sent.

        Raises Exception on SMTP failure.
        """
        creds = self._creds
        if isinstance(to, str):
            to = [to]

        sender = f"{creds['sender_name']} <{creds['sender_email']}>"

        # Build message: mixed > related > html + attachments
        msg = MIMEMultipart("mixed")
        msg["From"] = sender
        msg["To"] = ", ".join(to)
        msg["Subject"] = subject

        related = MIMEMultipart("related")
        related.attach(MIMEText(html_body, "html", "utf-8"))
        msg.attach(related)

        for path in (attachments or []):
            with open(path, "rb") as fh:
                data = fh.read()
            import mimetypes
            subtype = (mimetypes.guess_type(path)[0] or "application/octet-stream").split("/")[-1]
            part = MIMEApplication(data, _subtype=subtype)
            part.add_header(
                "Content-Disposition", "attachment",
                filename=os.path.basename(path),
            )
            msg.attach(part)

        raw = msg.as_bytes()

        self._send_raw(raw, to)

        # Save to IMAP Sent (non-critical)
        if self._imap:
            self.save_sent(raw)

    # ── Internal helpers ───────────────────────────────────────────────────────

    def send_plain(self, to: str | list, subject: str, body: str,
                   archive_folder: str | None = None) -> str:
        """
        Send a plain text email.

        Raises Exception on SMTP failure.
        """
        creds = self._creds
        if isinstance(to, str):
            to = [to]

        sender = f"{creds['sender_name']} <{creds['sender_email']}>"

        msg = MIMEMultipart("mixed")
        msg["From"] = sender
        msg["To"] = ", ".join(to)
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=creds["sender_email"].split("@")[-1])
        msg.attach(MIMEText(body, "plain", "utf-8"))

        raw = msg.as_bytes()

        send_result = self._send_raw(raw, to)

        if archive_folder:
            if self._gmail and send_result and send_result.get("id"):
                self._gmail.add_label(send_result["id"], archive_folder)
            else:
                self.save_to_folder(raw, archive_folder)
        elif self._imap:
            self.save_sent(raw)
        return msg["Message-ID"]

    def send_html(self, to: str | list, subject: str, html_body: str,
                  text_body: str) -> str:
        """Send an HTML email with a plain-text fallback."""
        creds = self._creds
        if isinstance(to, str):
            to = [to]

        sender = f"{creds['sender_name']} <{creds['sender_email']}>"
        msg = MIMEMultipart("alternative")
        msg["From"] = sender
        msg["To"] = ", ".join(to)
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(
            domain=creds["sender_email"].split("@")[-1]
        )
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        raw = msg.as_bytes()
        self._send_raw(raw, to)
        return msg["Message-ID"]

    def send_raw(self, raw_bytes: bytes, to: str | list):
        """Send an already-built RFC 822 message through the active transport."""
        recipients = [to] if isinstance(to, str) else to
        result = self._send_raw(raw_bytes, recipients)
        if self._imap:
            self.save_sent(raw_bytes)
        return result

    def quit(self):
        """Compatibility alias for scripts that previously closed SMTP clients."""
        self.close_imap()

    def _send_raw(self, raw_bytes: bytes, recipients: list[str]):
        """Send RFC 822 bytes through Gmail API or legacy password SMTP."""
        if self._gmail:
            return self._gmail.send_raw(raw_bytes)

        creds = self._creds
        with smtplib.SMTP_SSL(
            creds["smtp_host"],
            int(creds["smtp_port"]),
            timeout=30,
        ) as server:
            server.login(creds["sender_email"], creds["sender_password"])
            server.sendmail(creds["sender_email"], recipients, raw_bytes)
        return None

    def _require_imap(self) -> imaplib.IMAP4_SSL:
        if self._imap is None:
            raise RuntimeError("IMAP not connected. Call connect_imap() first.")
        return self._imap

    def _resolve_folder(self, folder: str) -> str:
        """Resolve a user-facing folder name into the server's INBOX namespace."""
        if folder.upper().startswith("INBOX"):
            return folder

        imap = self._require_imap()
        delimiter = "."
        list_type, list_data = imap.list()
        if list_type == "OK":
            for item in list_data or []:
                line = item.decode(errors="replace")
                match = re.search(r'\)\s+"([^"]+)"\s+', line)
                if match:
                    delimiter = match.group(1)
                    break
        return f"INBOX{delimiter}{folder}"

    def _message_summary(self, uid, msg) -> dict:
        return {
            "uid": uid,
            "subject": self._decode_header_value(msg.get("Subject", "")),
            "from": self._decode_header_value(msg.get("From", "")),
            "date": msg.get("Date", ""),
            "body_text": self._extract_body_text(msg),
        }

    @staticmethod
    def _decode_header_value(value: str) -> str:
        """Decode RFC 2047 encoded header value."""
        from email.header import decode_header
        parts = decode_header(value or "")
        decoded = []
        for part, charset in parts:
            if isinstance(part, bytes):
                decoded.append(part.decode(charset or "utf-8", errors="replace"))
            else:
                decoded.append(part)
        return "".join(decoded)

    @staticmethod
    def _extract_body_text(msg) -> str:
        """Extract plain text body from an email message."""
        if msg.is_multipart():
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
            # Fallback: try HTML
            for part in msg.walk():
                ct = part.get_content_type()
                if ct == "text/html":
                    payload = part.get_payload(decode=True)
                    if payload is None:
                        continue
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace").strip()
            return ""
        payload = msg.get_payload(decode=True)
        if payload is None:
            return ""
        charset = msg.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace").strip()
