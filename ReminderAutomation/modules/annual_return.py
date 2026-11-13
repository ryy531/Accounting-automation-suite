"""
Annual Return business logic — pure functions, zero I/O.
Input: list[dict] rows from tracker_reader (or NocoDB).
Output: filtered/sorted client list + formatted WhatsApp preview text.
"""
from datetime import date, datetime
from typing import NamedTuple, Optional


FILED_STATUSES = {"filed", "confirmation sent", "inactive"}


class AnnualReturnClient(NamedTuple):
    legal_name: str
    owner_name: str
    owner_email: str
    due_date: date
    days_until: int   # negative = overdue
    assigned: str
    status: str


def get_due_this_month(
    rows: list[dict],
    year: Optional[int] = None,
    month: Optional[int] = None,
    today: Optional[date] = None,
) -> list[AnnualReturnClient]:
    """Clients whose annual return is due in the given calendar month."""
    today = today or date.today()
    year = year or today.year
    month = month or today.month
    result = []
    for row in rows:
        status = str(row.get("Status") or "").strip().lower()
        if status in FILED_STATUSES:
            continue
        due = _parse_date(row.get("Calculated Due Date") or row.get("Annual Return"))
        if due and due.year == year and due.month == month:
            result.append(_make_client(row, due, today))
    return sorted(result, key=lambda c: c.due_date)


def get_overdue_unfiled(
    rows: list[dict],
    months_back: int = 1,
    today: Optional[date] = None,
) -> list[AnnualReturnClient]:
    """
    Clients due in the previous `months_back` calendar months that are still not filed.
    Default 1 = only last month. Increase if you want a longer follow-up window.
    """
    today = today or date.today()
    cutoff = _month_subtract(today.year, today.month, months_back)
    this_month = (today.year, today.month)
    result = []
    for row in rows:
        status = str(row.get("Status") or "").strip().lower()
        if status in FILED_STATUSES:
            continue
        due = _parse_date(row.get("Calculated Due Date") or row.get("Annual Return"))
        if due and cutoff <= (due.year, due.month) < this_month:
            result.append(_make_client(row, due, today))
    return sorted(result, key=lambda c: c.due_date)


def format_preview(
    due_this_month: list[AnnualReturnClient],
    overdue: list[AnnualReturnClient],
    today: Optional[date] = None,
) -> str:
    today = today or date.today()
    month_label = f"{today.year}年{today.month}月"

    if not due_this_month and not overdue:
        return f"📋 年度报告提醒 — {month_label}\n\n✅ 本月无到期客户，上月无遗留，无需发送"

    lines = [f"📋 *年度报告提醒* — {month_label}\n"]

    if due_this_month:
        lines.append(f"*本月到期（{len(due_this_month)} 个）*")
        for c in due_this_month:
            sent_flag = "✉️ 已发提醒" if "reminder" in c.status else "⏳ 未提醒"
            lines.append(
                f"• {c.legal_name}  [{sent_flag}]\n"
                f"  到期：{c.due_date}\n"
                f"  邮件：{c.owner_email or '—'}"
            )

    if overdue:
        if due_this_month:
            lines.append("")
        lines.append(f"*上月未完成（{len(overdue)} 个）⚠️*")
        for c in overdue:
            days_ago = -c.days_until
            lines.append(
                f"• {c.legal_name}\n"
                f"  到期：{c.due_date}（逾期 {days_ago} 天）\n"
                f"  邮件：{c.owner_email or '—'}"
            )

    total = len(due_this_month) + len(overdue)
    lines.append(f"\n共 {total} 个  回复 *发送* 确认发邮件 | 回复 *跳过* 取消本次")
    return "\n".join(lines)


def format_followup(
    overdue: list[AnnualReturnClient],
    today: Optional[date] = None,
) -> Optional[str]:
    """
    15th-of-month follow-up message for unfinished clients.
    Returns None when there is nothing to follow up on (caller should stay silent).
    """
    if not overdue:
        return None
    today = today or date.today()
    lines = [
        f"🔔 *年度报告跟进* — {today.year}年{today.month}月15日",
        f"以下 {len(overdue)} 个客户上月到期，状态尚未更新：\n",
    ]
    for c in overdue:
        days_ago = -c.days_until
        lines.append(
            f"• {c.legal_name}\n"
            f"  到期：{c.due_date}（逾期 {days_ago} 天）"
        )
    lines.append("\n已完成的请告知 bot，说「XXX Corp 年报做完了」即可自动更新状态")
    return "\n".join(lines)


def valid_statuses() -> set[str]:
    return {"Filed", "Confirmation Sent", "Reminder Sent", "Pending", "Inactive"}


# ── internal helpers ──────────────────────────────────────────────────────────

def _make_client(row: dict, due: date, today: date) -> AnnualReturnClient:
    return AnnualReturnClient(
        legal_name=str(row.get("Legal Name") or "").strip(),
        owner_name=str(row.get("Owner Name") or "").strip(),
        owner_email=str(row.get("Owner Email") or "").strip(),
        due_date=due,
        days_until=(due - today).days,
        assigned=str(row.get("Assigned") or "").strip(),
        status=str(row.get("Status") or "").strip().lower(),
    )


def _month_subtract(year: int, month: int, n: int) -> tuple[int, int]:
    """Return (year, month) going back n months."""
    month -= n
    while month < 1:
        month += 12
        year -= 1
    return year, month


def _parse_date(val) -> Optional[date]:
    if val is None:
        return None
    if isinstance(val, datetime):   # datetime is a subclass of date — check first
        return val.date()
    if isinstance(val, date):
        return val
    s = str(val).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None
