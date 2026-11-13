"""
NocoDB engagement queries for the weekly report — deterministic, no AI.
Thin wrapper around _lib/data_access.py's query_engagements.
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))

log = logging.getLogger(__name__)

# Engagements deliberately deferred by Alex (2026-06-26) -- Companies-table
# data (Filed Until) for these is still genuinely missing, not just stale.
# Remove an id here once its Companies record is filled in; the next run
# will then pick it back up automatically via the normal P0 query.
DEFERRED_ENGAGEMENT_IDS = {
    312,  # Sample Market Inc -- PST Filed Until still blank
    414,  # Sample Properties Inc -- PST Filed Until still blank
}


def _fmt_status(s: str) -> str:
    return s.replace(",", " / ").replace("stage | ", "").replace("blocker | ", "⚠")


def get_review_rows() -> list[tuple]:
    from data_access import query_engagements
    rows = query_engagements(review=True, exclude_done=False, limit=100)
    return [
        ("待alex审核", r["company"], r["type"], r.get("due_date") or "-",
         _fmt_status(r["current_status"]),
         "engagement已到review阶段，需alex确认后推进到下一步")
        for r in rows
    ]


def get_blocked_rows() -> list[tuple]:
    from data_access import query_engagements
    rows = query_engagements(blocked=True, exclude_done=False, limit=100)
    out = []
    for r in rows:
        status = r["current_status"]
        if "signature" in status:
            blocker = "缺签名"
        elif "payment" in status:
            blocker = "缺付款"
        else:
            blocker = "客户文件缺失"
        out.append((
            "卡住-需介入", r["company"], r["type"], r.get("due_date") or "-",
            _fmt_status(status), f"{blocker}，建议alex直接联系客户跟进",
        ))
    return out


def get_p0_rows() -> list[tuple]:
    from data_access import query_engagements
    rows = query_engagements(priorities=["P0"], exclude_done=True, limit=200)
    rows = [r for r in rows if r["id"] not in DEFERRED_ENGAGEMENT_IDS]
    return [
        ("P0待确认分类", r["company"], r["type"], r.get("due_date") or "-",
         _fmt_status(r["current_status"]),
         "新/异常engagement，标记needs-review，需确认归类或优先级")
        for r in rows
    ]


def get_noco_rows() -> list[tuple]:
    """All three NocoDB sections combined, in the order shown in the report."""
    rows = []
    rows.extend(get_review_rows())
    rows.extend(get_blocked_rows())
    rows.extend(get_p0_rows())
    log.info("NocoDB rows: %d", len(rows))
    return rows
