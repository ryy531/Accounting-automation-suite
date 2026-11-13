"""
Excel tracker loader.
Single responsibility: resolve path, open workbook, return list[dict].
No business logic here.
"""
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

try:
    import openpyxl
except ImportError:
    raise SystemExit("[ReminderAutomation] Missing openpyxl. Run: pip install openpyxl")


def _resolve(options: list[str]) -> Optional[Path]:
    for p in options:
        path = Path(p)
        if path.exists():
            return path
    return None


def _sheet_to_dicts(ws) -> list[dict]:
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    headers = [str(h).strip() if h is not None else "" for h in rows[0]]
    result = []
    for row in rows[1:]:
        if all(c is None for c in row):
            continue
        result.append(dict(zip(headers, row)))
    return result


def load_annual_return_tracker() -> list[dict]:
    from ReminderAutomation.config import ANNUAL_RETURN_TRACKER_OPTIONS
    path = _resolve(ANNUAL_RETURN_TRACKER_OPTIONS)
    if not path:
        raise FileNotFoundError(
            f"Annual Return tracker not found. Tried: {ANNUAL_RETURN_TRACKER_OPTIONS}"
        )
    log.info("Loading Annual Return tracker: %s", path)
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    return _sheet_to_dicts(wb.active)


def load_tax_tracker(sheet_name: str) -> list[dict]:
    from ReminderAutomation.config import TAX_REMINDER_TRACKER_OPTIONS
    path = _resolve(TAX_REMINDER_TRACKER_OPTIONS)
    if not path:
        raise FileNotFoundError(
            f"Tax Reminder tracker not found. Tried: {TAX_REMINDER_TRACKER_OPTIONS}"
        )
    log.info("Loading Tax Reminder tracker sheet '%s': %s", sheet_name, path)
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    if sheet_name not in wb.sheetnames:
        raise ValueError(
            f"Sheet '{sheet_name}' not in tracker. Available: {list(wb.sheetnames)}"
        )
    return _sheet_to_dicts(wb[sheet_name])
