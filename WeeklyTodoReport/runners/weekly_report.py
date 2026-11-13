"""
Entry point: build this week's Alex to-do report and deliver it via WhatsApp
+ email. Run via: python -m WeeklyTodoReport.runners.weekly_report
(cwd must be Scripts root so the WeeklyTodoReport package is importable)

Intended Task Scheduler trigger: weekly, Friday at 12:00 (see
run_weekly_report.bat for the wrapper and suggested Task Scheduler config).

Safe to re-run: cleanup only deletes files older than RETENTION_DAYS, and
each run overwrites/creates only its own dated file.
"""
import argparse
import logging
import shutil
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
log = logging.getLogger(__name__)

from WeeklyTodoReport import config
from WeeklyTodoReport.interfaces import (
    chat_extract, email_send, excel_writer, manual_todo, nocodb_query, whatsapp,
)


def week_range(run_date: date) -> tuple[date, date]:
    """Monday..Friday of the ISO week containing run_date."""
    monday = run_date - timedelta(days=run_date.weekday())
    friday = monday + timedelta(days=4)
    return monday, friday


def cleanup_old_reports(out_dir: Path, retention_days: int) -> None:
    cutoff = time.time() - retention_days * 86400
    if not out_dir.exists():
        return
    for f in out_dir.glob("Weekly_*.xlsx"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                log.info("Cleaned up old report: %s", f.name)
        except OSError as exc:
            log.warning("Could not remove %s: %s", f, exc)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-date", help="YYYY-MM-DD, defaults to today (testing override)")
    parser.add_argument("--no-send", action="store_true", help="Build the Excel only, skip WhatsApp/email delivery")
    parser.add_argument("--skip-chat-extract", action="store_true",
                         help="Skip the AI chat-extraction step (e.g. container/DeepSeek unavailable, or not yet deployed) — report still goes out with the NocoDB sheet only")
    args = parser.parse_args()

    run_date = date.fromisoformat(args.run_date) if args.run_date else date.today()
    monday, friday = week_range(run_date)
    start_s, end_s = monday.isoformat(), friday.isoformat()
    log.info("Building weekly report for %s .. %s", start_s, end_s)

    noco_rows = nocodb_query.get_noco_rows()

    if args.skip_chat_extract:
        log.info("--skip-chat-extract set, skipping AI chat extraction")
        chat_rows = []
    else:
        chat_rows = chat_extract.get_chat_rows(
            start_s, end_s,
            container=config.OPENCLAW_CONTAINER,
            extract_script=config.CHAT_EXTRACT_SCRIPT,
            extract_out=config.CHAT_EXTRACT_OUT,
        )
        if not chat_rows:
            log.warning("No chat-derived rows (extraction script may not be deployed yet) — proceeding with NocoDB-only report")

    manual_rows = manual_todo.get_manual_rows(container=config.OPENCLAW_CONTAINER)
    chat_rows = chat_rows + manual_rows

    wb = excel_writer.build_workbook(chat_rows=chat_rows, noco_rows=noco_rows)

    out_dir = Path(config.OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"Weekly_{run_date.isoformat()}.xlsx"
    wb.save(out_path)
    log.info("Saved %s", out_path)

    cleanup_old_reports(out_dir, config.RETENTION_DAYS)

    if args.no_send:
        log.info("--no-send set, skipping WhatsApp/email delivery")
        return 0

    caption = (
        f"📋 Alex 本周代办 — {start_s} 至 {end_s}\n"
        f"聊天记录代办 {len(chat_rows)} 项 / NocoDB 代办 {len(noco_rows)} 项"
    )

    # docker.exe can't resolve paths correctly when given a UNC source (no
    # drive letter) -- stage the file on a local disk first, then docker cp
    # from there into the container.
    # /home/node/.openclaw/media/inbound is the gateway's allow-listed local
    # media directory -- /tmp is rejected with LocalMediaAccessError.
    container_tmp = "/home/node/.openclaw/media/inbound/weekly_report.xlsx"
    with tempfile.TemporaryDirectory() as tmp_dir:
        local_copy = Path(tmp_dir) / out_path.name
        shutil.copyfile(out_path, local_copy)
        ok_copy = whatsapp.copy_into_container(str(local_copy), container_tmp, container=config.OPENCLAW_CONTAINER)

    if ok_copy:
        whatsapp.send_file(
            caption, container_tmp,
            target=config.ALEX_WHATSAPP_TARGET, container=config.OPENCLAW_CONTAINER,
        )
    else:
        log.error("Skipping WhatsApp send — could not copy file into container")

    email_send.send(
        from_addr=f"ExampleCo <{config.HIMALAYA_FROM_ADDRESS}>",
        to_addr=config.ALEX_EMAIL,
        subject=f"ExampleCo 本周代办 Weekly To-Do — {start_s} 至 {end_s}",
        body=caption,
        attachment_path=str(out_path),
        container=config.OPENCLAW_CONTAINER,
        himalaya_bin=config.HIMALAYA_BIN,
        himalaya_config=config.HIMALAYA_CONFIG,
        account=config.HIMALAYA_ACCOUNT,
    )

    if manual_rows:
        manual_todo.archive_manual_file(container=config.OPENCLAW_CONTAINER)

    return 0


if __name__ == "__main__":
    sys.exit(main())
