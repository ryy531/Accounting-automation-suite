"""
Excel tracker writer.
Single responsibility: find a row by company name, update Status and date columns.
No business logic — only knows how to write to the file.
"""
import logging
from datetime import date
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


def update_annual_return_status(
    company: str,
    status: str,
    filed_by: str = "",
) -> dict:
    """
    Update Status (and FiledDate when status='Filed') for a company in the tracker.
    Returns {"ok": True} or {"ok": False, "error": "..."}.
    Caller is responsible for validating the status value before calling.
    """
    from ReminderAutomation.config import ANNUAL_RETURN_TRACKER_OPTIONS

    path = _resolve(ANNUAL_RETURN_TRACKER_OPTIONS)
    if not path:
        return {"ok": False, "error": "Annual Return tracker not found"}

    try:
        wb = openpyxl.load_workbook(path)
        ws = wb.active

        # Build column index from header row
        headers = {}
        for cell in ws[1]:
            if cell.value:
                headers[str(cell.value).strip()] = cell.column

        status_col    = headers.get("Status")
        filed_date_col = headers.get("FiledDate")
        legal_name_col = headers.get("Legal Name")

        if not legal_name_col or not status_col:
            return {"ok": False, "error": "Required columns (Legal Name, Status) not found in tracker"}

        company_lower = company.strip().lower()
        for row in ws.iter_rows(min_row=2):
            cell = row[legal_name_col - 1]
            if cell.value and str(cell.value).strip().lower() == company_lower:
                row[status_col - 1].value = status
                if filed_date_col and status.lower() == "filed":
                    row[filed_date_col - 1].value = date.today().isoformat()
                wb.save(path)
                log.info("Updated tracker: %s → %s (by %s)", company, status, filed_by or "unknown")
                return {"ok": True}

        return {"ok": False, "error": f"Company '{company}' not found in tracker"}

    except PermissionError:
        return {"ok": False, "error": "Tracker file is locked — close it in Excel first"}
    except Exception as exc:
        log.exception("Tracker write failed for %s", company)
        return {"ok": False, "error": str(exc)}
