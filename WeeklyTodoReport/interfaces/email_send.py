"""
Email delivery through the shared ExampleCo EmailClient and Gmail API transport.
The existing call signature is retained for compatibility with the weekly runner.
"""
import logging
import sys
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from _lib.connectors.email_client import EmailClient

log = logging.getLogger(__name__)


def build_raw_message(*, from_addr: str, to_addr: str, subject: str, body: str,
                       attachment_path: str) -> bytes:
    msg = MIMEMultipart()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    path = Path(attachment_path)
    part = MIMEApplication(path.read_bytes(), Name=path.name)
    part["Content-Disposition"] = f'attachment; filename="{path.name}"'
    msg.attach(part)

    return msg.as_bytes()


def send(*, from_addr: str, to_addr: str, subject: str, body: str,
          attachment_path: str, container: str, himalaya_bin: str,
          himalaya_config: str, account: str) -> bool:
    """Send through the shared EmailClient; legacy args keep caller compatibility."""
    del container, himalaya_bin, himalaya_config, account
    raw = build_raw_message(
        from_addr=from_addr, to_addr=to_addr, subject=subject,
        body=body, attachment_path=attachment_path,
    )
    client = None
    try:
        client = EmailClient.from_settings("ExampleCo Weekly Todo")
        client.send_raw(raw, to_addr)
        log.info("Email delivered to %s", to_addr)
        return True
    except Exception as exc:
        log.error("Email send error: %s", exc)
        return False
    finally:
        if client is not None:
            client.quit()
