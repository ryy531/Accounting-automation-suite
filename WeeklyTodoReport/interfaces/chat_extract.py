"""
Pulls the AI-summarized list of ad-hoc chat-derived to-dos for the week.

Runs WeeklyTodoReport/container_scripts/weekly_todo_extract.py (deployed
alongside nocodb_briefing.py / email_briefing.py inside the OpenClaw
container) via docker exec, then docker cp's the resulting JSON back to a
host temp file and parses it.

Requires the container-side script to already be deployed at
config.CHAT_EXTRACT_SCRIPT — see WeeklyTodoReport/container_scripts/README.md
for the one-time deploy step. If the script or its output is missing, this
silently returns an empty list so the rest of the report still goes out.
"""
import json
import logging
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


def get_chat_rows(start: str, end: str, *, container: str, extract_script: str,
                   extract_out: str) -> list[tuple]:
    try:
        result = subprocess.run(
            ["docker", "exec", container, "python3", extract_script, start, end],
            capture_output=True, timeout=240, encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            log.error("chat extract failed (rc=%d): %s", result.returncode, result.stderr.strip())
            return []
    except subprocess.TimeoutExpired:
        log.error("chat extract timed out after 240s")
        return []
    except Exception as exc:
        log.error("chat extract error: %s", exc)
        return []

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        cp = subprocess.run(
            ["docker", "cp", f"{container}:{extract_out}", tmp_path],
            capture_output=True, timeout=30, encoding="utf-8", errors="replace",
        )
        if cp.returncode != 0:
            log.error("docker cp of extract output failed: %s", cp.stderr.strip())
            return []
        data = json.loads(Path(tmp_path).read_text(encoding="utf-8"))
    except Exception as exc:
        log.error("reading extract output failed: %s", exc)
        return []
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    if isinstance(data, dict) and "error" in data:
        log.error("chat extract reported error: %s", data.get("error"))
        return []
    if not isinstance(data, list):
        log.error("chat extract output not a list: %r", data)
        return []

    rows = []
    for item in data:
        try:
            rows.append((
                item.get("date", "-"),
                item.get("category", "其他"),
                item.get("item", ""),
                item.get("note", ""),
            ))
        except AttributeError:
            continue
    log.info("Chat-derived rows: %d", len(rows))
    return rows
