"""
Entry point: generate Annual Return preview and send to WhatsApp group.
Run via: python -m ReminderAutomation.runners.annual_return_preview
(cwd must be Scripts root so the ReminderAutomation package is importable)
"""
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
log = logging.getLogger(__name__)

from ReminderAutomation.config import AR_OVERDUE_MONTHS_BACK
from ReminderAutomation.interfaces import nocodb, tracker_reader, whatsapp, wechat
from ReminderAutomation.modules import annual_return


def main() -> int:
    rows = nocodb.get_annual_return_clients()
    if rows is None:
        rows = tracker_reader.load_annual_return_tracker()

    due_this_month = annual_return.get_due_this_month(rows)
    overdue        = annual_return.get_overdue_unfiled(rows, AR_OVERDUE_MONTHS_BACK)

    log.info("Annual Return preview: %d this month, %d overdue",
             len(due_this_month), len(overdue))

    preview = annual_return.format_preview(due_this_month, overdue)

    wa_ok = whatsapp.send_to_group(preview)
    if not wa_ok:
        log.error("Failed to deliver WhatsApp preview")

    wc_ok = wechat.send_to_group(preview)
    if not wc_ok:
        log.warning("Failed to deliver WeChat preview (bot may not be running)")

    return 0 if wa_ok else 1


if __name__ == "__main__":
    sys.exit(main())
