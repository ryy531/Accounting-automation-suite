"""
WhatsApp delivery via OpenClaw gateway (docker exec message send), mirroring
ReminderAutomation.interfaces.whatsapp but targeting a specific number with
a media attachment instead of broadcasting plain text to the group.
"""
import logging
import subprocess

log = logging.getLogger(__name__)


def send_file(text: str, container_path: str, *, target: str, container: str) -> bool:
    """Send text + media attachment to a WhatsApp target. Returns True on success."""
    try:
        result = subprocess.run(
            [
                "docker", "exec", container,
                "node", "dist/index.js",
                "message", "send",
                "--channel", "whatsapp",
                "--target", target,
                "-m", text,
                "--media", container_path,
            ],
            capture_output=True,
            timeout=60,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            log.error("WhatsApp send failed (rc=%d): %s", result.returncode, result.stderr.strip())
            return False
        log.info("WhatsApp report delivered to %s", target)
        return True
    except subprocess.TimeoutExpired:
        log.error("WhatsApp send timed out after 60s (gateway may be reconnecting)")
        return False
    except FileNotFoundError:
        log.error("'docker' not found — is Docker Desktop running?")
        return False
    except Exception as exc:
        log.error("WhatsApp send error: %s", exc)
        return False


def copy_into_container(local_path: str, container_path: str, *, container: str) -> bool:
    """docker cp a file from the host into the container so --media can reach it."""
    try:
        result = subprocess.run(
            ["docker", "cp", local_path, f"{container}:{container_path}"],
            capture_output=True, timeout=30, encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            log.error("docker cp into container failed: %s", result.stderr.strip())
            return False
        return True
    except Exception as exc:
        log.error("docker cp error: %s", exc)
        return False
