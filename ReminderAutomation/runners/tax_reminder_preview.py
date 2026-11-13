"""
Entry point: generate Tax Reminder previews for all sheets and send to WhatsApp group.
Run via: python -m ReminderAutomation.runners.tax_reminder_preview
(cwd must be Scripts root so the ReminderAutomation package is importable)
"""
import logging
import sys
from datetime import date

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
log = logging.getLogger(__name__)

from ReminderAutomation.config import TAX_REMINDER_SHEETS
from ReminderAutomation.interfaces import nocodb, tracker_reader, whatsapp, wechat
from ReminderAutomation.modules import tax_reminder


def main() -> int:
    today = date.today()
    errors = 0

    for sheet in TAX_REMINDER_SHEETS:
        rows = nocodb.get_tax_reminder_clients(sheet)
        if rows is None:
            try:
                rows = tracker_reader.load_tax_tracker(sheet)
            except (FileNotFoundError, ValueError) as exc:
                log.error("Cannot load sheet '%s': %s", sheet, exc)
                errors += 1
                continue

        clients = tax_reminder.get_due_clients(rows, sheet, today)
        log.info("Tax Reminder [%s]: %d clients", sheet, len(clients))

        if not clients:
            continue  # nothing to send — stay silent

        preview = tax_reminder.format_preview(clients, sheet, today)
        if not whatsapp.send_to_group(preview):
            log.error("Failed to deliver WhatsApp preview for sheet '%s'", sheet)
            errors += 1
        wechat.send_to_group(preview)

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
