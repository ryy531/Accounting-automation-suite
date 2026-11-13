from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .pair_tag_parser import parse_pair_tag


_MONEY_QUANT = Decimal("0.01")


def _is_truthy_review(value) -> bool:
    """安全判断 needs_review，避免字符串 'False' 被误判为 True"""
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


def _append_review(row: dict, tag: str) -> None:
    note = row.get("note") or ""
    if tag not in note:
        row["note"] = f"{note} {tag}".strip()
    row["needs_review"] = True


def _parse_money(value) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value.quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP)
    if isinstance(value, (int, float)):
        return Decimal(str(value)).quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP)

    text = str(value).strip()
    if not text:
        return None

    negative = text.startswith("(") and text.endswith(")")
    cleaned = (
        text.replace("$", "")
        .replace(",", "")
        .replace("(", "")
        .replace(")", "")
        .strip()
    )
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return None
    if negative:
        amount = -amount
    return amount.quantize(_MONEY_QUANT, rounding=ROUND_HALF_UP)


def _merge_inv_pay(pair_id: str, inv_row: dict, pay_row: dict) -> dict:
    merged = dict(inv_row)
    pay_card_tail = pay_row.get("payment_card_last_four")
    if pay_card_tail:
        merged["payment_card_last_four"] = pay_card_tail

    inv_tag = parse_pair_tag(inv_row.get("source_file") or "")
    pay_tag = parse_pair_tag(pay_row.get("source_file") or "")
    inv_source = inv_tag.clean_filename if inv_tag else (inv_row.get("source_file") or "")
    pay_source = pay_tag.clean_filename if pay_tag else (pay_row.get("source_file") or "")
    merged["source_file"] = f"INV: {inv_source} | PAY: {pay_source}"

    # 修复：保留 INV/PAY 原有的 needs_review 状态，避免字符串误判
    merged["needs_review"] = _is_truthy_review(inv_row.get("needs_review")) or _is_truthy_review(pay_row.get("needs_review"))

    # 修复：保留 INV 原有 note，移除旧标签，改为非 REVIEW 格式
    inv_note = inv_row.get("note") or ""
    inv_note = inv_note.replace("[REVIEW: merged_by_filename_pair]", "").replace("verify before posting", "").strip()
    merged["note"] = (
        f"rule_id=merged_by_filename_pair; merged by {pair_id}; "
        f"invoice values used; payment card tail from PAY file"
    )
    if inv_note:
        merged["note"] = f"{merged['note']}; {inv_note}"

    return merged


def _rows_with_review(rows: list[dict], tag: str) -> list[dict]:
    reviewed = []
    for row in rows:
        row_copy = dict(row)
        _append_review(row_copy, tag)
        reviewed.append(row_copy)
    return reviewed


def _process_pair_group(pair_id: str, rows: list[dict]) -> list[dict]:
    inv_rows = []
    pay_rows = []
    invalid_rows = []
    other_rows = []

    for row in rows:
        tag = parse_pair_tag(row.get("source_file") or "")
        if tag is None:
            other_rows.append(row)
        elif tag.invalid_role:
            invalid_rows.append(row)
        elif tag.pair_role == "INV":
            inv_rows.append(row)
        elif tag.pair_role == "PAY":
            pay_rows.append(row)
        else:
            other_rows.append(row)

    if invalid_rows:
        return _rows_with_review(rows, "[REVIEW: invalid filename pair role]")
    if len(inv_rows) != 1:
        return _rows_with_review(rows, "[REVIEW: filename pair requires exactly one INV]")
    if len(pay_rows) != 1:
        return _rows_with_review(rows, "[REVIEW: filename pair requires exactly one PAY]")

    inv_total = _parse_money(inv_rows[0].get("total"))
    pay_total = _parse_money(pay_rows[0].get("total"))
    if inv_total is None:
        return _rows_with_review(rows, "[REVIEW: filename pair INV total missing]")
    if pay_total is not None and pay_total != inv_total:
        return _rows_with_review(rows, "[REVIEW: filename pair total mismatch]")

    merged = _merge_inv_pay(pair_id, inv_rows[0], pay_rows[0])
    return [merged] + [dict(row) for row in other_rows]


def merge_filename_pairs(rows: list[dict]) -> list[dict]:
    pair_groups: dict[str, list[dict]] = {}
    output_rows = []

    for row in rows:
        tag = parse_pair_tag(row.get("source_file") or "")
        if tag is None:
            output_rows.append(row)
            continue
        pair_groups.setdefault(tag.pair_id, []).append(row)

    for pair_id, pair_rows in pair_groups.items():
        output_rows.extend(_process_pair_group(pair_id, pair_rows))

    return output_rows
