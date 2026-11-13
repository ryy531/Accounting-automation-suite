"""
server/cron.py -- Payroll auto-create-period cron job.

Triggered daily at 6 AM by Windows Task Scheduler.
Ensures all due payroll engagements are created in NocoDB through data_access.py.
"""

import sys
import os
import json
import time
import random
import logging
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config.settings as settings
import data_access
from server.payroll_periods import (
    calculate_period_start, backfill_due_periods, FREQ_BI_WEEKLY,
)

logger = logging.getLogger(__name__)

_LOG_DIR = Path(__file__).resolve().parents[1] / "logs"


def _get_today() -> date:
    return datetime.now(ZoneInfo(settings.PAYROLL_TIMEZONE)).date()


class CronLock:
    """PID-based lock file to prevent concurrent runs."""

    def __init__(self, lock_path: Path):
        self.lock_path = lock_path
        self.acquired = False

    def __enter__(self):
        if self.lock_path.exists():
            try:
                pid = int(self.lock_path.read_text().strip())
                if self._pid_alive(pid):
                    raise RuntimeError(
                        f"Another payroll cron instance (PID {pid}) is running. Exiting."
                    )
                logger.warning("Stale lock file from dead PID %s, removing.", pid)
                self.lock_path.unlink()
            except (ValueError, OSError) as exc:
                logger.warning("Corrupt lock file, removing: %s", exc)
                self.lock_path.unlink(missing_ok=True)

        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock_path.write_text(str(os.getpid()))
        self.acquired = True
        return self

    def __exit__(self, *args):
        if self.acquired:
            self.lock_path.unlink(missing_ok=True)

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if sys.platform == "win32":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            SYNCHRONIZE = 0x00100000
            handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False


def retry(fn, *, max_attempts=3, base_delay=1.0, jitter=0.2):
    """Exponential backoff with jitter for transient data_access failures."""
    attempts = 0
    while True:
        try:
            return fn()
        except Exception as exc:
            attempts += 1
            if attempts >= max_attempts:
                raise
            delay = base_delay * (2 ** (attempts - 1))
            delay *= (1 + random.uniform(-jitter, jitter))
            logger.warning(
                "Data access error attempt %d, retrying in %.1fs: %s",
                attempts,
                delay,
                exc,
            )
            time.sleep(delay)


def get_payroll_companies() -> list[dict]:
    return data_access.list_payroll_companies()


def _row_id(row: dict):
    return row.get("Id", row.get("id"))


def _select_value(value) -> str:
    if isinstance(value, dict):
        return value.get("value") or value.get("title") or ""
    return str(value or "").strip()


def resolve_biweekly_anchor(company: dict) -> date | None:
    company_name = company.get(settings.NOCODB_COMPANY_FIELD_LEGAL_NAME, "")
    last = data_access.get_last_payroll_period_end(company_name)
    if last:
        return last

    rule_text = str(
        company.get(settings.NOCODB_COMPANY_FIELD_PAY_PERIOD_RULE) or ""
    ).strip()
    if rule_text:
        try:
            return date.fromisoformat(rule_text)
        except ValueError:
            pass

    return None


def engagement_exists(company: dict, period_end: date) -> bool:
    company_name = company.get(settings.NOCODB_COMPANY_FIELD_LEGAL_NAME, "")
    return data_access.engagement_exists(company_name, period_end)


def create_payroll_engagement(company: dict, period_end: date, frequency: str) -> dict:
    company_name = company[settings.NOCODB_COMPANY_FIELD_LEGAL_NAME]
    period_start = calculate_period_start(period_end, frequency)
    return data_access.create_payroll_engagement(
        company_name,
        period_start,
        period_end,
        frequency,
    )


def main() -> int:
    """
    Returns exit code:
      0 = success (may include skipped_bi_weekly_uninit, not counted as failure)
      1 = has failed companies or unknown frequency
      2 = fatal (load failure / lock conflict)
    """
    logger.info("=" * 60)
    logger.info("Payroll cron starting")
    logger.info("  cwd:      %s", os.getcwd())
    logger.info("  python:   %s", sys.executable)
    logger.info("  pid:      %s", os.getpid())
    logger.info("  datastore: NocoDB via data_access.py")

    today = _get_today()
    logger.info("  today:    %s (%s)", today, settings.PAYROLL_TIMEZONE)
    logger.info("=" * 60)

    log_dir = _LOG_DIR
    lock_path = log_dir / "payroll_cron.lock"

    summary = {
        "run_date": today.isoformat(),
        "started_at": datetime.now(ZoneInfo(settings.PAYROLL_TIMEZONE)).isoformat(),
        "created": [],
        "skipped_existing": [],
        "skipped_not_due": [],
        "skipped_bi_weekly_uninit": [],
        "skipped_unknown_freq": [],
        "failed": [],
    }
    exit_code = 0

    try:
        with CronLock(lock_path):
            try:
                companies = retry(get_payroll_companies)
            except Exception as exc:
                logger.error("Failed loading payroll companies after retries: %s", exc)
                summary["fatal_error"] = f"load_companies: {exc}"
                return 2

            logger.info("Loaded %d payroll companies", len(companies))

            for company in companies:
                cid = _row_id(company)
                cname = company.get(
                    settings.NOCODB_COMPANY_FIELD_LEGAL_NAME,
                    f"<id={cid}>",
                )

                try:
                    freq = _select_value(
                        company.get(settings.NOCODB_COMPANY_FIELD_PAY_FREQUENCY)
                    )

                    if freq not in settings.NOCODB_VALID_FREQUENCIES:
                        logger.warning("[%s] unknown frequency: %s", cname, freq)
                        summary["skipped_unknown_freq"].append({
                            "company": cname,
                            "id": cid,
                            "frequency": freq,
                        })
                        continue

                    if freq == FREQ_BI_WEEKLY:
                        anchor = resolve_biweekly_anchor(company)
                        if anchor is None:
                            logger.warning(
                                "[%s] bi-weekly without anchor, skipping", cname
                            )
                            summary["skipped_bi_weekly_uninit"].append({
                                "company": cname,
                                "id": cid,
                            })
                            continue
                    else:
                        anchor = None

                    due_dates = backfill_due_periods(
                        freq, today, anchor, max_lookback=1
                    )

                    if not due_dates:
                        summary["skipped_not_due"].append({
                            "company": cname,
                            "id": cid,
                            "frequency": freq,
                        })
                        continue

                    for period_end in due_dates:
                        if engagement_exists(company, period_end):
                            summary["skipped_existing"].append({
                                "company": cname,
                                "id": cid,
                                "period_end": period_end.isoformat(),
                            })
                            continue

                        retry(
                            lambda pe=period_end: create_payroll_engagement(
                                company, pe, freq
                            )
                        )

                        summary["created"].append({
                            "company": cname,
                            "id": cid,
                            "period_end": period_end.isoformat(),
                            "frequency": freq,
                        })
                        logger.info("[%s] created period %s (%s)", cname, period_end, freq)

                except Exception as exc:
                    logger.error("[%s] failed: %s", cname, exc, exc_info=True)
                    summary["failed"].append({
                        "company": cname,
                        "id": cid,
                        "error": str(exc),
                    })
                    continue

            exit_code = 1 if (
                summary["failed"] or summary["skipped_unknown_freq"]
            ) else 0

    except RuntimeError as exc:
        logger.error(str(exc))
        return 2

    finally:
        summary["finished_at"] = datetime.now(
            ZoneInfo(settings.PAYROLL_TIMEZONE)
        ).isoformat()
        summary["exit_code"] = exit_code
        ts = datetime.now(ZoneInfo(settings.PAYROLL_TIMEZONE)).strftime("%Y%m%d_%H%M%S")
        summary_path = log_dir / f"payroll_cron_{ts}.json"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            summary_path.write_text(
                json.dumps(summary, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.error("Failed writing summary JSON: %s", exc)

        logger.info("=" * 60)
        logger.info("Payroll cron summary:")
        logger.info("  created:                  %d", len(summary["created"]))
        logger.info("  skipped_existing:         %d", len(summary["skipped_existing"]))
        logger.info("  skipped_not_due:          %d", len(summary["skipped_not_due"]))
        logger.info(
            "  skipped_bi_weekly_uninit: %d",
            len(summary["skipped_bi_weekly_uninit"]),
        )
        logger.info(
            "  skipped_unknown_freq:     %d",
            len(summary["skipped_unknown_freq"]),
        )
        logger.info("  failed:                   %d", len(summary["failed"]))
        logger.info("  exit_code:                %d", exit_code)
        logger.info("  summary_file:             %s", summary_path)
        logger.info("=" * 60)

    return exit_code


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    sys.exit(main())
