from decimal import Decimal, InvalidOperation


AMOUNT_COLUMNS = (
    "withdrawal",
    "deposit",
    "charge",
    "payment_credit",
    "refund",
    "transfer_returned",
)
NET_AMOUNT_COLUMNS = ("withdrawal", "deposit", "charge", "payment_credit")
SUMMARY_KEYWORDS = (
    "opening balance",
    "closing balance",
    "total",
    "subtotal",
    "page total",
    "continued",
    "brought forward",
    "carried forward",
)
LOW_CONFIDENCE_THRESHOLD = Decimal("0.75")


def _parse_decimal(value) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))

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
    return -amount if negative else amount


def _has_amount(row: dict, column: str) -> bool:
    return _parse_decimal(row.get(column)) is not None


def _filled_amount_columns(row: dict) -> list[str]:
    return [column for column in AMOUNT_COLUMNS if _has_amount(row, column)]


def _append_review_tag(row: dict, tag: str) -> None:
    note = row.get("note") or ""
    if tag in note:
        return
    row["note"] = f"{note} {tag}".strip()


def _cash_account_amount(row: dict) -> Decimal | None:
    withdrawal = _parse_decimal(row.get("withdrawal"))
    deposit = _parse_decimal(row.get("deposit"))
    if withdrawal is not None and deposit is None:
        return -abs(withdrawal)
    if deposit is not None and withdrawal is None:
        return abs(deposit)
    return None


def _balance_mismatch(previous_row: dict | None, row: dict) -> bool:
    if previous_row is None:
        return False

    if _has_amount(row, "refund") or _has_amount(row, "transfer_returned"):
        return False

    effective_amount = _cash_account_amount(row)
    if effective_amount is None:
        return False

    previous_balance = _parse_decimal(previous_row.get("balance"))
    current_balance = _parse_decimal(row.get("balance"))
    if previous_balance is None or current_balance is None:
        return False

    expected = previous_balance + effective_amount
    return abs(expected - current_balance) > Decimal("0.01")


def evaluate(
    row: dict,
    previous_row: dict | None = None,
    declared_type: str | None = None,
    mixed_run: bool = False,
) -> tuple[bool, str]:
    reasons = []
    already_review = bool(row.get("needs_review"))

    filled_amounts = _filled_amount_columns(row)
    if not filled_amounts:
        reasons.append("missing amount")
    if len(filled_amounts) > 1:
        reasons.append("multiple amount columns")

    if not str(row.get("date") or "").strip():
        reasons.append("missing date")

    description = str(row.get("description") or "").strip()
    if not description:
        reasons.append("empty description")
    else:
        lowered_description = description.lower()
        if any(keyword in lowered_description for keyword in SUMMARY_KEYWORDS):
            reasons.append("suspicious summary row")

    confidence = _parse_decimal(row.get("confidence"))
    if confidence is not None and confidence < LOW_CONFIDENCE_THRESHOLD:
        reasons.append("low confidence")

    note = str(row.get("note") or "")
    if "[EXTRA:" in note:
        reasons.append("extra fields in note")
    if "continued" in note.lower() or description.endswith("-"):
        reasons.append("cross page transaction")

    doc_type = str(row.get("doc_type") or "").strip()
    if declared_type == "bank" and doc_type != "bank_statement":
        reasons.append("doc type mismatch")
    if mixed_run and doc_type == "bank_statement":
        reasons.append("from mixed run")

    if _balance_mismatch(previous_row, row):
        reasons.append("balance mismatch")

    for reason in reasons:
        _append_review_tag(row, f"[REVIEW: {reason}]")

    row["needs_review"] = already_review or bool(reasons)
    return row["needs_review"], "; ".join(reasons)


def evaluate_rows(
    rows: list[dict],
    declared_type: str | None = None,
    mixed_run: bool = False,
) -> list[dict]:
    previous_row = None
    for row in rows:
        if row.get("extract_failed"):
            previous_row = None
            continue
        evaluate(row, previous_row, declared_type=declared_type, mixed_run=mixed_run)
        previous_row = row
    return rows
