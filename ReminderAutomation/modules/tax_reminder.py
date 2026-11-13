"""
Tax Reminder business logic — pure functions, zero I/O.
Mirrors the round/date logic from TaxAutomationReminderSystem/tax_reminder.py
without touching files, SMTP, or openpyxl.
"""
from datetime import date, datetime
from typing import NamedTuple, Optional

# Round opens on this day-of-month and stays open until the next round
ROUND_DAYS = {1: 1, 2: 15, 3: 25}
QUARTERLY_MONTHS = (1, 4, 7, 10)

SHEET_LABELS = {
    "Bookkeeping": "月度记账提醒",
    "PST":         "PST 提醒",
    "GST":         "GST/HST 提醒",
    "T2":          "T2 企业税提醒",
}


class TaxReminderClient(NamedTuple):
    legal_name: str
    owner_name: str
    owner_email: str
    round_num: int
    assigned: str
    language: str


def get_due_clients(
    rows: list[dict],
    sheet: str,
    today: Optional[date] = None,
) -> list[TaxReminderClient]:
    """Return clients needing a reminder round today for the given tracker sheet."""
    today = today or date.today()
    rnd = _current_round(today.day)
    result = []

    for row in rows:
        status = str(row.get("Status") or "Active").strip().lower()
        if status == "inactive":
            continue

        if sheet == "PST" and str(row.get("PST Frequency") or "").strip() == "Quarterly":
            if today.month not in QUARTERLY_MONTHS or rnd != 1:
                continue
            if _sent_this_quarter(row.get("R1SentDate"), today):
                continue
        else:
            if _sent_this_round(row.get(f"R{rnd}SentDate"), today):
                continue

        result.append(TaxReminderClient(
            legal_name=str(row.get("Legal Name") or "").strip(),
            owner_name=str(row.get("Owner Name") or "").strip(),
            owner_email=str(row.get("Owner Email") or "").strip(),
            round_num=rnd,
            assigned=str(row.get("Assigned") or "").strip(),
            language=str(row.get("Language") or "EN").strip(),
        ))
    return result


def format_preview(
    clients: list[TaxReminderClient],
    sheet: str,
    today: Optional[date] = None,
) -> str:
    today = today or date.today()
    label = SHEET_LABELS.get(sheet, sheet)
    rnd = clients[0].round_num if clients else _current_round(today.day)
    if not clients:
        return f"📋 {label} R{rnd} — {today}\n\n✅ 本期无需发送提醒"
    lines = [
        f"📋 *{label} R{rnd}* — {today}",
        f"共 {len(clients)} 个客户\n",
    ]
    for c in clients:
        lines.append(
            f"• {c.legal_name}  ({c.assigned or '—'})\n"
            f"  邮件：{c.owner_email or '—'}"
        )
    lines.append("\n回复 *发送* 确认发邮件 | 回复 *跳过* 取消本次")
    return "\n".join(lines)


# ── internal helpers ──────────────────────────────────────────────────────────

def _current_round(day: int) -> int:
    if day < 15:
        return 1
    if day < 25:
        return 2
    return 3


def _sent_this_round(val, today: date) -> bool:
    """True if the round was already sent in this calendar month."""
    if not val:
        return False
    try:
        dt = val if isinstance(val, datetime) else datetime.strptime(str(val).strip()[:10], "%Y-%m-%d")
        return (dt.year, dt.month) == (today.year, today.month)
    except (ValueError, AttributeError):
        return False


def _sent_this_quarter(val, today: date) -> bool:
    """True if quarterly PST was already sent in the current quarter."""
    if not val:
        return False
    try:
        dt = val if isinstance(val, datetime) else datetime.strptime(str(val).strip()[:10], "%Y-%m-%d")
        def qstart(m): return ((m - 1) // 3) * 3 + 1
        return (dt.year, qstart(dt.month)) == (today.year, qstart(today.month))
    except (ValueError, AttributeError):
        return False
