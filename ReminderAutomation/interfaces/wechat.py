"""
WeChat outbound delivery via the local OCR channel bridge.
Single responsibility: POST text to the WeChat outbound HTTP server.
"""
import logging
import requests

log = logging.getLogger(__name__)

WECHAT_OUTBOUND_URL = "http://127.0.0.1:18791/send"
WECHAT_TOKEN        = "123456"
WECHAT_DEFAULT_GROUP = "Alex's Office"


def send_to_group(text: str, *, group: str = WECHAT_DEFAULT_GROUP, timeout: int = 10) -> bool:
    """Send text to the WeChat group via the OCR channel bridge. Returns True on success."""
    try:
        resp = requests.post(
            WECHAT_OUTBOUND_URL,
            headers={
                "Authorization": f"Bearer {WECHAT_TOKEN}",
                "Content-Type": "application/json",
            },
            json={"text": text, "group": group},
            timeout=timeout,
        )
        if resp.status_code == 200:
            log.info("WeChat message delivered to %r", group)
            return True
        log.error("WeChat send failed (HTTP %d): %s", resp.status_code, resp.text[:200])
        return False
    except requests.exceptions.ConnectionError:
        log.error("WeChat outbound bridge not reachable — is wechat_ocr_channel.py running?")
        return False
    except Exception as exc:
        log.error("WeChat send error: %s", exc)
        return False
