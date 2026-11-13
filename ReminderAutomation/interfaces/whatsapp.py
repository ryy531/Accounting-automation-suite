"""
WhatsApp delivery via OpenClaw gateway (docker exec message send).
Single responsibility: send text to a WhatsApp target.
"""
import subprocess
import logging

log = logging.getLogger(__name__)


def send_to_group(text: str, *, group_jid: str = None) -> bool:
    """Send text to the Ai-Bot WhatsApp group. Returns True on success."""
    from ReminderAutomation.config import OPENCLAW_CONTAINER, WHATSAPP_GROUP_JID
    target = group_jid or WHATSAPP_GROUP_JID
    try:
        result = subprocess.run(
            [
                "docker", "exec", OPENCLAW_CONTAINER,
                "node", "dist/index.js",
                "message", "send",
                "--channel", "whatsapp",
                "--target", target,
                "-m", text,
            ],
            capture_output=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            log.error("WhatsApp send failed (rc=%d): %s", result.returncode, result.stderr.strip())
            return False
        log.info("WhatsApp message delivered to %s", target)
        return True
    except subprocess.TimeoutExpired:
        log.error("WhatsApp send timed out after 30s (gateway may be reconnecting)")
        return False
    except FileNotFoundError:
        log.error("'docker' not found — is Docker Desktop running?")
        return False
    except Exception as exc:
        log.error("WhatsApp send error: %s", exc)
        return False
