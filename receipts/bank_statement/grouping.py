import re
from datetime import date, datetime


UNDATED_SHEET_TITLE = "⚠ Undated"
_MONTH_RE = re.compile(r"^\d{4}-\d{2}")


def get_month_key(row: dict) -> str | None:
    value = row.get("date")
    if isinstance(value, datetime):
        return value.strftime("%Y-%m")
    if isinstance(value, date):
        return value.strftime("%Y-%m")
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    match = _MONTH_RE.match(text)
    if not match:
        return None
    return match.group(0)


def group_by_source_and_month(rows: list[dict]) -> dict[str, dict[str, list[dict]]]:
    grouped: dict[str, dict[str, list[dict]]] = {}

    for row in rows:
        source_file = row.get("source_file") or "unknown_source"
        month_key = get_month_key(row) or UNDATED_SHEET_TITLE

        source_group = grouped.setdefault(source_file, {})
        month_group = source_group.setdefault(month_key, [])
        month_group.append(row)

    return grouped


def ordered_sheet_names(month_groups: dict[str, list[dict]]) -> list[str]:
    names = []
    if UNDATED_SHEET_TITLE in month_groups:
        names.append(UNDATED_SHEET_TITLE)

    month_names = sorted(
        name for name in month_groups
        if name != UNDATED_SHEET_TITLE
    )
    names.extend(month_names)
    return names
