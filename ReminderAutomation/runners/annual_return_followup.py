"""
Entry point: 15th-of-month follow-up for unfinished Annual Return clients.
Only sends a message if there are genuinely overdue unfiled clients — stays
silent when everything is clear.

Run via: python -m ReminderAutomation.runners.annual_return_followup
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

    overdue = annual_return.get_overdue_unfiled(rows, AR_OVERDUE_MONTHS_BACK)
    message = annual_return.format_followup(overdue)

    if message is None:
        log.info("Annual Return follow-up: nothing overdue, staying silent")
        return 0

    log.info("Annual Return follow-up: %d overdue clients", len(overdue))
    wa_ok = whatsapp.send_to_group(message)
    if not wa_ok:
        log.error("Failed to deliver WhatsApp follow-up message")
    wechat.send_to_group(message)
    return 0 if wa_ok else 1


if __name__ == "__main__":
    sys.exit(main())
