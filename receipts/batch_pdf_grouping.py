from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path

from .pair_tag_parser import parse_pair_tag


# 配对规则语义 (2026-05-27 Riley + Alex 确认):
# - 配对成功 (金额一致) -> 不标黄, note: grouped by page order
# - 配对失败 (金额不一致 > 0.10) -> 标黄
# - 单独 payment_receipt 且 doc_type == receipt 且字段完整 -> 不标黄
# - 单独 invoice 且字段完整 -> 不标黄
# - orphan payment slip (符合上面 d 条款) -> 标黄
# - AI page_role unclear / 关键字段缺失 -> 标黄
# 不引入 receipt_kind 字段, 等 pilot 数据出现 >=2 个漏判案例再立项
# utility_bill 保持无条件标黄 (pilot update 2026-05-15 §9.1):
# 结构复杂不清晰, 单页 bill 有时不完整, 无法稳定提取有效信息, 强制人工 review。
HIGH_CONFIDENCE_THRESHOLD = 0.85
AMOUNT_TOLERANCE = Decimal("0.10")
MAX_TOTAL = Decimal("50000")
BANK_DOC_TYPES = {"bank_statement", "statement_transaction"}
INVOICE_ROLES = {"invoice_start", "invoice_continuation"}
GROUP_ATTACH_ROLES = {"invoice_continuation", "payment_receipt"}
MONEY_FIELDS = ("subtotal", "gst", "pst", "total")
_MONEY_QUANT = Decimal("0.01")
UNCERTAINTY_NOTE_KEYWORDS = (
    "unclear",
    "blurry",
    "not sure",
    "no monetary",
    "partially",
)
ORPHAN_PAYMENT_NOTE_KEYWORDS = (
    "authorization",
    "approval only",
    "card slip",
    "payment slip",
    "merchant copy",
    "customer copy",
    "debit slip",
)
GENERIC_PAYMENT_DESCRIPTIONS = {
    "payment receipt",
    "debit receipt",
    "credit card receipt",
    "interac",
}


def _append_review(row: dict, tag: str) -> None:
    note = row.get("note") or ""
    if tag not in note:
        row["note"] = f"{note} {tag}".strip()
    row["needs_review"] = True


def _append_rule_note(row: dict, rule_id: str, detail: str | None = None) -> None:
    note = row.get("note") or ""
    tag = f"rule_id={rule_id}"
    if detail:
        tag = f"{tag}; {detail}"
    if tag not in note:
        row["note"] = f"{note} {tag}".strip()


def _append_review_rule(row: dict, rule_id: str, reason: str) -> None:
    _append_review(row, f"[REVIEW: {rule_id}] {reason}")


def _page_role(row: dict) -> str:
    return str(row.get("page_role") or "").strip().lower()


def _doc_type(row: dict) -> str:
    return str(row.get("doc_type") or row.get("document_type") or "").strip().lower()


def _is_bank_row(row: dict) -> bool:
    return _page_role(row) == "bank_statement" or _doc_type(row) in BANK_DOC_TYPES


def _confidence(row: dict) -> float:
    try:
        return float(row.get("confidence") or 0)
    except (TypeError, ValueError):
        return 0.0


def _row_order(row: dict, position: int) -> tuple:
    return (
        row.get("source_file") or "",
        int(row.get("page_num") or 0),
        int(row.get("row_index_within_page") or row.get("row_num") or position),
        position,
    )


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


def _has_value(value) -> bool:
    return value is not None and str(value).strip() != ""


def _money_completeness(row: dict) -> int:
    return sum(1 for field in MONEY_FIELDS if _parse_money(row.get(field)) is not None)


def _invoice_rows(group_rows: list[dict]) -> list[dict]:
    return [row for row in group_rows if _page_role(row) in INVOICE_ROLES]


def _payment_rows(group_rows: list[dict]) -> list[dict]:
    return [row for row in group_rows if _page_role(row) == "payment_receipt"]


def _best_invoice_row(group_rows: list[dict]) -> dict | None:
    invoices = _invoice_rows(group_rows)
    if not invoices:
        return None
    return max(invoices, key=_money_completeness)


def _has_amount_conflict(invoice_rows: list[dict]) -> bool:
    seen: dict[str, Decimal] = {}
    for row in invoice_rows:
        for field in MONEY_FIELDS:
            amount = _parse_money(row.get(field))
            if amount is None:
                continue
            if field in seen and abs(seen[field] - amount) > AMOUNT_TOLERANCE:
                return True
            seen[field] = amount
    return False


def _amount_anomaly_reasons(row: dict) -> list[str]:
    reasons = []
    raw_total = row.get("total")
    total = _parse_money(raw_total)
    if not _has_value(raw_total):
        reasons.append("total is missing")
        return reasons
    if total is None:
        reasons.append("total is not numeric")
        return reasons
    if total < 0:
        reasons.append("total is negative")
    if total > MAX_TOTAL:
        reasons.append("total exceeds 50000")

    gst = _parse_money(row.get("gst"))
    pst = _parse_money(row.get("pst"))
    subtotal = _parse_money(row.get("subtotal"))
    if gst is not None and gst > total:
        reasons.append("gst exceeds total")
    if pst is not None and pst > total:
        reasons.append("pst exceeds total")
    if subtotal is not None and gst is not None and pst is not None:
        expected = subtotal + gst + pst
        if abs(expected - total) > AMOUNT_TOLERANCE:
            reasons.append("subtotal plus tax does not match total")
    return reasons


def _core_missing_reasons(row: dict) -> list[str]:
    reasons = []
    for field in ("date", "description", "total"):
        if not _has_value(row.get(field)):
            reasons.append(f"{field} is missing")
    if _has_value(row.get("total")) and _parse_money(row.get("total")) is None:
        reasons.append("total is not numeric")
    return reasons


def _note_has_uncertainty(row: dict) -> bool:
    note = str(row.get("note") or "").lower()
    return any(keyword in note for keyword in UNCERTAINTY_NOTE_KEYWORDS)


def _is_orphan_payment_slip(row: dict) -> bool:
    if _page_role(row) != "payment_receipt":
        return False
    if _doc_type(row) != "receipt":
        return True
    if _core_missing_reasons(row):
        return True

    note = str(row.get("note") or "").lower()
    if any(keyword in note for keyword in ORPHAN_PAYMENT_NOTE_KEYWORDS):
        return True

    description = str(row.get("description") or "").strip().lower()
    return description in GENERIC_PAYMENT_DESCRIPTIONS


def _invoice_payment_conflict(invoice_row: dict, payment_rows: list[dict]) -> bool:
    invoice_total = _parse_money(invoice_row.get("total"))
    if invoice_total is None:
        return False
    for payment_row in payment_rows:
        payment_total = _parse_money(payment_row.get("total"))
        if payment_total is None:
            continue
        if abs(invoice_total - payment_total) > AMOUNT_TOLERANCE:
            return True
    return False


def _finalize_standalone(row: dict, ok_rule_id: str) -> dict:
    row_copy = dict(row)
    if _page_role(row_copy) == "unclear":
        _append_review_rule(row_copy, "ai_low_confidence", "page_role is unclear")
        return row_copy

    core_reasons = _core_missing_reasons(row_copy)
    if core_reasons:
        _append_review_rule(row_copy, "ai_low_confidence", "; ".join(core_reasons))
        return row_copy

    amount_reasons = _amount_anomaly_reasons(row_copy)
    if amount_reasons:
        _append_review_rule(row_copy, "amount_anomaly_needs_review", "; ".join(amount_reasons))
        return row_copy

    if _note_has_uncertainty(row_copy):
        _append_review_rule(
            row_copy,
            "note_uncertainty_keywords",
            "note contains uncertainty keyword",
        )
        return row_copy

    _append_rule_note(row_copy, ok_rule_id)
    row_copy["needs_review"] = bool(row_copy.get("needs_review"))
    return row_copy


def _finalize_orphan_payment(row: dict) -> dict:
    if _is_orphan_payment_slip(row):
        row_copy = dict(row)
        _append_review_rule(
            row_copy,
            "orphan_payment_slip_needs_review",
            "payment_receipt has no paired invoice and looks like a payment slip",
        )
        return row_copy
    return _finalize_standalone(row, "standalone_receipt_ok")


def _standalone_ok_rule(row: dict) -> str | None:
    doc_type = _doc_type(row)
    if doc_type == "receipt":
        return "standalone_receipt_ok"
    if doc_type == "invoice" or _page_role(row) in INVOICE_ROLES:
        return "standalone_invoice_ok"
    return None


def _finalize_loose_row(row: dict) -> dict:
    if _doc_type(row) == "utility_bill":
        return dict(row)

    ok_rule = _standalone_ok_rule(row)
    if ok_rule is None:
        row_copy = dict(row)
        _append_review_rule(
            row_copy,
            "ai_low_confidence",
            "page role is not receipt, invoice, or utility bill",
        )
        return row_copy
    return _finalize_standalone(row, ok_rule)


def _page_list(rows: list[dict]) -> list[int]:
    return sorted({int(row.get("page_num") or 0) for row in rows if row.get("page_num")})


def _page_text(pages: list[int], singular: str, plural: str) -> str:
    if not pages:
        return f"{plural} unknown"
    label = singular if len(pages) == 1 else plural
    return f"{label} {','.join(str(page) for page in pages)}"


def _source_with_page_range(source_file: str, rows: list[dict]) -> str:
    pages = _page_list(rows)
    if not pages:
        return source_file
    if len(pages) == 1:
        return f"{source_file} pages {pages[0]}"
    return f"{source_file} pages {pages[0]}-{pages[-1]}"


def _group_note(group_rows: list[dict], rule_id: str, review: bool = False) -> str:
    invoice_pages = _page_list(_invoice_rows(group_rows))
    payment_pages = _page_list(_payment_rows(group_rows))
    parts = [_page_text(invoice_pages, "invoice page", "invoice pages")]
    if payment_pages:
        parts.append(_page_text(payment_pages, "payment receipt page", "payment receipt pages"))
    parts.append("grouped by page order")
    parts.append(f"rule_id={rule_id}")
    body = "; ".join(parts)
    if review:
        return f"[REVIEW: {rule_id}] {body}"
    return body


def _merge_group(source_file: str, group_rows: list[dict]) -> dict:
    invoice_row = _best_invoice_row(group_rows)
    if invoice_row is None:
        merged = _finalize_orphan_payment(group_rows[0])
        merged["source_file"] = _source_with_page_range(source_file, group_rows)
        return merged

    merged = dict(invoice_row)
    payment_rows = _payment_rows(group_rows)
    for payment_row in payment_rows:
        card_tail = payment_row.get("payment_card_last_four")
        if card_tail:
            merged["payment_card_last_four"] = card_tail
            break

    invoice_rows = _invoice_rows(group_rows)
    invoice_conflict = _has_amount_conflict(invoice_rows)
    payment_conflict = _invoice_payment_conflict(invoice_row, payment_rows)
    has_payment = bool(payment_rows)

    if invoice_conflict:
        rule_id = "multipage_invoice_amount_conflict"
        note = _group_note(group_rows, rule_id, review=True)
        note = (
            f"{note}; multiple invoice pages have inconsistent amount fields; "
            "verify manually"
        )
        merged["needs_review"] = True
    elif payment_conflict:
        rule_id = "paired_amount_conflict_needs_review"
        note = _group_note(group_rows, rule_id, review=True)
        note = f"{note}; invoice total and payment amount differ by more than 0.10"
        merged["needs_review"] = True
    elif has_payment:
        core_reasons = _core_missing_reasons(merged)
        amount_reasons = _amount_anomaly_reasons(merged)
        has_uncertainty = any(_note_has_uncertainty(row) for row in group_rows)
        if core_reasons:
            rule_id = "ai_low_confidence"
            note = f"{_group_note(group_rows, rule_id, review=True)}; {'; '.join(core_reasons)}"
            merged["needs_review"] = True
        elif amount_reasons:
            rule_id = "amount_anomaly_needs_review"
            note = f"{_group_note(group_rows, rule_id, review=True)}; {'; '.join(amount_reasons)}"
            merged["needs_review"] = True
        elif has_uncertainty:
            rule_id = "note_uncertainty_keywords"
            note = (
                f"{_group_note(group_rows, rule_id, review=True)}; "
                "note contains uncertainty keyword"
            )
            merged["needs_review"] = True
        else:
            rule_id = "paired_invoice_payment_ok"
            note = _group_note(group_rows, rule_id)
            merged["needs_review"] = bool(merged.get("needs_review"))
    else:
        if _doc_type(merged) == "utility_bill":
            merged["source_file"] = _source_with_page_range(source_file, group_rows)
            return merged
        finalized = _finalize_standalone(merged, "standalone_invoice_ok")
        finalized["source_file"] = _source_with_page_range(source_file, group_rows)
        return finalized

    merged["source_file"] = _source_with_page_range(source_file, group_rows)
    merged["note"] = note
    return merged


def _standalone_review(row: dict, tag: str) -> dict:
    row_copy = dict(row)
    _append_review(row_copy, tag)
    return row_copy


def _close_group(source_file: str, current_group: list[dict], output_rows: list[dict]) -> list[dict]:
    if current_group:
        output_rows.append(_merge_group(source_file, current_group))
    return []


def _group_source_rows(source_file: str, rows: list[dict]) -> list[dict]:
    ordered_rows = sorted(enumerate(rows), key=lambda item: _row_order(item[1], item[0]))
    output_rows = []
    current_group: list[dict] = []

    for _, row in ordered_rows:
        role = _page_role(row)
        if _is_bank_row(row):
            current_group = _close_group(source_file, current_group, output_rows)
            output_rows.append(dict(row))
            continue

        if role == "separator":
            current_group = _close_group(source_file, current_group, output_rows)
            continue

        if role == "invoice_start":
            current_group = _close_group(source_file, current_group, output_rows)
            current_group = [row]
            continue

        if role in GROUP_ATTACH_ROLES:
            if current_group:
                current_group.append(row)
            else:
                output_rows.append(_finalize_orphan_payment(row))
            continue

        if role in {"other", "unclear"}:
            if current_group and _confidence(row) >= HIGH_CONFIDENCE_THRESHOLD:
                current_group.append(row)
            else:
                output_rows.append(_finalize_loose_row(row))
            continue

        output_rows.append(dict(row))

    _close_group(source_file, current_group, output_rows)
    return output_rows


def group_batch_pdf_rows(rows: list[dict]) -> list[dict]:
    source_groups: dict[str, list[dict]] = {}
    passthrough_rows = []

    for row in rows:
        source_file = row.get("source_file") or ""
        if parse_pair_tag(source_file) is not None:
            passthrough_rows.append(row)
        elif Path(source_file).suffix.lower() == ".pdf":
            source_groups.setdefault(source_file, []).append(row)
        else:
            passthrough_rows.append(row)

    output_rows = list(passthrough_rows)
    for source_file, source_rows in source_groups.items():
        output_rows.extend(_group_source_rows(source_file, source_rows))

    return output_rows
