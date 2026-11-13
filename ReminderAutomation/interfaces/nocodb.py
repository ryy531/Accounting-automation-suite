"""
NocoDB interface — STUB (NocoDB is in prototype; schema not yet finalized).

All functions return None. Callers fall back to Excel trackers automatically.
Replace function bodies here once NocoDB reminder tables are ready.
"""
import logging
from typing import Optional

log = logging.getLogger(__name__)


def get_annual_return_clients() -> Optional[list[dict]]:
    # TODO: query NocoDB Annual Return table when schema is confirmed
    log.debug("NocoDB stub — annual_return falling back to Excel")
    return None


def get_tax_reminder_clients(sheet: str) -> Optional[list[dict]]:
    # TODO: query NocoDB engagement table filtered by type=sheet
    log.debug("NocoDB stub — tax_reminder[%s] falling back to Excel", sheet)
    return None


def update_preview_sent(module: str, legal_name: str) -> None:
    # TODO: write preview_sent_at timestamp to NocoDB record
    pass
