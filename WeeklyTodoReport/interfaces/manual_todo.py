"""
Reads the "加入周报" list — items the bot appended to
workspace/weekly_todo_manual.jsonl when someone in chat explicitly asked to
have something added to the weekly report (see AGENTS.md's "加入周报代办"
section). Pure file read, no AI call involved.

After a successful report send, the file is archived (renamed with a
timestamp) rather than deleted, so a bad run never silently loses items —
worst case they show up again next week from the archived copy if needed.
"""
import logging
import subprocess
from datetime import datetime

log = logging.getLogger(__name__)

WORKSPACE_FILE = "/home/node/.openclaw/workspace/weekly_todo_manual.jsonl"


def get_manual_rows(*, container: str) -> list[tuple]:
    try:
        result = subprocess.run(
            ["docker", "exec", container, "cat", WORKSPACE_FILE],
            capture_output=True, timeout=30, encoding="utf-8", errors="replace",
        )
    except Exception as exc:
        log.error("Reading manual todo file failed: %s", exc)
        return []

    if result.returncode != 0:
        # File doesn't exist yet -- nobody has used the feature this week.
        log.info("No manual todo file yet (or read failed): %s", result.stderr.strip())
        return []

    rows = []
    import json
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            log.warning("Skipping malformed manual todo line: %r", line)
            continue
        rows.append((
            item.get("date", "-"),
            "群内/私聊手动添加",
            item.get("item", ""),
            f"{item.get('source', '')} {item.get('note', '')}".strip(),
        ))
    log.info("Manual todo rows: %d", len(rows))
    return rows


def archive_manual_file(*, container: str) -> None:
    """Move workspace/weekly_todo_manual.jsonl aside so next week starts fresh."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_path = f"{WORKSPACE_FILE}.sent_{stamp}"
    try:
        result = subprocess.run(
            ["docker", "exec", container, "sh", "-c",
             f"[ -f {WORKSPACE_FILE} ] && mv {WORKSPACE_FILE} {archive_path} || true"],
            capture_output=True, timeout=30, encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            log.warning("Archiving manual todo file failed: %s", result.stderr.strip())
        else:
            log.info("Archived manual todo file to %s", archive_path)
    except Exception as exc:
        log.error("Archiving manual todo file error: %s", exc)
