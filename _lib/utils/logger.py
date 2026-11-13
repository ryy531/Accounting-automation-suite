"""
utils/logger.py — Shared JSON-based log with dedup.

Usage:
    from utils.logger import Logger

    log = Logger("path/to/log.json")
    if not log.is_processed("Smith, John"):
        log.mark_done("Smith, John", {"email": "a@b.com"})
"""

import os
import json
from datetime import datetime


class Logger:
    """JSON-backed log with dedup and auto-timestamp."""

    def __init__(self, log_path: str, default: dict | None = None):
        self.log_path = log_path
        self.default = default if default is not None else {}
        self._data: dict | None = None

    # ── I/O ────────────────────────────────────────────────────────────────────

    def load(self) -> dict:
        """Load log from disk. Returns default on missing / corrupt file."""
        if self._data is not None:
            return self._data
        if os.path.exists(self.log_path):
            try:
                with open(self.log_path, encoding="utf-8") as f:
                    self._data = json.load(f)
                    return self._data
            except Exception:
                pass
        self._data = {**self.default}  # shallow copy
        return self._data

    def save(self):
        """Write current data to disk (creates parent dirs automatically)."""
        data = self.load()
        parent = os.path.dirname(self.log_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(self.log_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    # ── Dedup ──────────────────────────────────────────────────────────────────

    def is_processed(self, key: str) -> bool:
        """Return True if *key* already exists in the log."""
        data = self.load()
        return key in data

    def mark_done(self, key: str, data: dict | None = None):
        """
        Record *key* as processed with an auto-added timestamp.
        If *data* is provided, stored as {**data, "timestamp": ...}.
        If *data* is None, stored as {"timestamp": ...}.
        """
        entry: dict = {"timestamp": datetime.now().isoformat(timespec="seconds")}
        if data:
            entry.update(data)
        self.load()[key] = entry


# =============================================================================
# Usage examples (do not import this section)
# =============================================================================
#
# 1) missing_client_reminder style — append multiple send records per name
#    ---------------------------------------------------------------
#    log = Logger(config.MISSING_CLIENT_LOG)
#    if not log.is_processed("Smith, John"):
#        log.mark_done("Smith, John", {"email": "john@example.com"})
#        log.save()
#
# 2) scan_email_replies style — complex default structure
#    ---------------------------------------------------------------
#    log = Logger(
#        config.REPLY_LOG_FILE,
#        default={"last_scan_date": "", "scanned_uids": [], "classifications": {}},
#    )
#    data = log.load()
#    data["last_scan_date"] = "2026-04-11"
#    data["scanned_uids"].append("12345")
#    log.save()
#
# 3) send_reminder_email style — flat key-value dedup
#    ---------------------------------------------------------------
#    log = Logger(config.REMINDER_LOG_FILE)
#    key = f"{folder}|{person}"
#    if log.is_processed(key):
#        continue
#    log.mark_done(key)
#    log.save()
