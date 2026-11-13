from decimal import Decimal, InvalidOperation

from .account_list import AccountList
from .schema import (
    QB_FIELD_ACCOUNT,
    QB_FIELD_INCLUDE,
    QB_FIELD_MEMO,
    QB_FIELD_PAYMENT_ACCOUNT,
    QB_FIELD_REF_NO,
    QB_FIELD_TRANSACTION_TYPE,
    SUPPORTED_TRANSACTION_TYPES,
    ValidationIssue,
    WorkbookRow,
)


RECEIPT_TRANSACTION_TYPES = {"cheque", "credit card charge"}
TAX_CODE_FIELD = "QB Tax Code"
VALID_TAX_CODES = {"G", "P", "S", "Z", "E"}
GST_TAX_NAME = "GST/HST Payable"
PST_TAX_NAME = "PST Payable (SK)"


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _is_true(value: object) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


def _parse_decimal(value: object) -> Decimal | None:
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


def _issue(
    row: WorkbookRow,
    severity: str,
    field: str,
    message: str,
    current_value: object | None = None,
) -> ValidationIssue:
    return ValidationIssue(
        severity=severity,
        sheet_name=row.sheet_name,
        row_number=row.row_number,
        field=field,
        message=message,
        current_value=_clean_text(row.values.get(field) if current_value is None else current_value),
    )


def _amount_field(source_mode: str) -> str:
    if source_mode == "bank":
        return "amount"
    return "total"


def _validate_required_text(row: WorkbookRow, field: str, label: str) -> list[ValidationIssue]:
    if _clean_text(row.values.get(field)):
        return []
    return [_issue(row, "BLOCK", field, f"{label} is required")]


def _account_type(accounts: AccountList, account_name: object) -> str:
    account = accounts.get(account_name)
    return _clean_text(account.account_type if account else "").lower()


def _is_bank_account(accounts: AccountList, account_name: object) -> bool:
    account_type = _account_type(accounts, account_name)
    return account_type == "bank"


def _find_tax_account_with_lookup(
    accounts: AccountList, tax_name: str, row: WorkbookRow, field: str
) -> list[ValidationIssue]:
    """Find tax account using TaxAccountLookup and return issues if any."""
    lookup = accounts.find_tax_account(tax_name)
    if lookup.lookup_status == "found":
        return []
    if lookup.lookup_status == "not_found":
        return [
            _issue(
                row,
                "BLOCK",
                field,
                f"Tax account not found in COA: {tax_name}",
            )
        ]
    # multiple_matches
    candidates_str = ", ".join(lookup.candidates[:3])
    if len(lookup.candidates) > 3:
        candidates_str += f", ... ({len(lookup.candidates)} total)"
    return [
        _issue(
            row,
            "BLOCK",
            field,
            f"Multiple tax accounts match '{tax_name}' in COA: {candidates_str}. "
            "Please ensure only one account name contains this tax name.",
        )
    ]


def validate_rows(
    rows: list[WorkbookRow],
    accounts: AccountList,
    source_mode: str,
    workbook_warnings: list[str] | None = None,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    for warning in workbook_warnings or []:
        issues.append(
            ValidationIssue(
                severity="WARNING",
                sheet_name="Workbook",
                row_number=0,
                field="Workbook",
                message=warning,
            )
        )

    for row in rows:
        include_value = row.values.get(QB_FIELD_INCLUDE)
        if not _is_true(include_value):
            message = "Include in IIF must be TRUE before this row can export"
            if source_mode == "bank":
                message = "Bank source rows are blocked unless Include in IIF is explicitly TRUE"
            issues.append(_issue(row, "BLOCK", QB_FIELD_INCLUDE, message))

        issues.extend(_validate_required_text(row, "date", "Date"))
        if source_mode == "receipt":
            issues.extend(_validate_required_text(row, "description", "Description/vendor"))
        issues.extend(_validate_required_text(row, QB_FIELD_ACCOUNT, "QB Account"))
        issues.extend(_validate_required_text(row, QB_FIELD_PAYMENT_ACCOUNT, "Payment Account"))
        issues.extend(
            _validate_required_text(row, QB_FIELD_TRANSACTION_TYPE, "QB Transaction Type")
        )

        amount_field = _amount_field(source_mode)
        amount = _parse_decimal(row.values.get(amount_field))
        if amount is None:
            issues.append(_issue(row, "BLOCK", amount_field, f"{amount_field} is required"))
        elif source_mode == "receipt" and amount <= 0:
            issues.append(_issue(row, "BLOCK", amount_field, f"{amount_field} must be greater than 0"))

        transaction_type = _clean_text(row.values.get(QB_FIELD_TRANSACTION_TYPE))
        transaction_key = transaction_type.lower()
        if source_mode == "receipt" and transaction_type and transaction_key not in RECEIPT_TRANSACTION_TYPES:
            issues.append(
                _issue(
                    row,
                    "BLOCK",
                    QB_FIELD_TRANSACTION_TYPE,
                    "QB Transaction Type must be Cheque or Credit Card Charge for receipt export",
                )
            )
        elif transaction_type and transaction_key not in SUPPORTED_TRANSACTION_TYPES:
            issues.append(
                _issue(
                    row,
                    "BLOCK",
                    QB_FIELD_TRANSACTION_TYPE,
                    f"Unsupported QB Transaction Type: {transaction_type}",
                )
            )

        qb_account = _clean_text(row.values.get(QB_FIELD_ACCOUNT))
        if qb_account and not accounts.has_account(qb_account):
            issues.append(
                _issue(row, "BLOCK", QB_FIELD_ACCOUNT, f"QB Account not found in COA: {qb_account}")
            )

        payment_account = _clean_text(row.values.get(QB_FIELD_PAYMENT_ACCOUNT))
        if payment_account and not accounts.has_account(payment_account):
            issues.append(
                _issue(
                    row,
                    "BLOCK",
                    QB_FIELD_PAYMENT_ACCOUNT,
                    f"Payment Account not found in COA: {payment_account}",
                )
            )
        elif payment_account and transaction_type and transaction_key == "cheque":
            # For Cheque transactions, verify Payment Account is Bank type
            if not _is_bank_account(accounts, payment_account):
                issues.append(
                    _issue(
                        row,
                        "BLOCK",
                        QB_FIELD_PAYMENT_ACCOUNT,
                        "Cheque requires a Bank type Payment Account. "
                        f"'{payment_account}' is not a Bank account.",
                        payment_account,
                    )
                )

        tax_code = _clean_text(row.values.get(TAX_CODE_FIELD))
        if tax_code and tax_code.upper() not in VALID_TAX_CODES:
            issues.append(_issue(row, "BLOCK", TAX_CODE_FIELD, f"Invalid QB Tax Code: {tax_code}"))

        gst = _parse_decimal(row.values.get("gst")) or Decimal("0")
        pst = _parse_decimal(row.values.get("pst")) or Decimal("0")
        if source_mode == "receipt":
            if gst != 0:
                issues.extend(_find_tax_account_with_lookup(accounts, GST_TAX_NAME, row, "gst"))
            if pst != 0:
                issues.extend(_find_tax_account_with_lookup(accounts, PST_TAX_NAME, row, "pst"))
            if amount is not None and amount - gst - pst <= 0:
                issues.append(
                    _issue(
                        row,
                        "BLOCK",
                        amount_field,
                        "Expense amount after GST/PST must be greater than 0",
                    )
                )

        if _clean_text(row.values.get("needs_review")).lower() == "true":
            issues.append(
                _issue(
                    row,
                    "WARNING",
                    "needs_review",
                    "Original OCR needs_review=True; warning only after staff review",
                )
            )

        if not _clean_text(row.values.get(QB_FIELD_MEMO)):
            issues.append(_issue(row, "WARNING", QB_FIELD_MEMO, "QB Memo is blank"))

        if not _clean_text(row.values.get(QB_FIELD_REF_NO)):
            issues.append(_issue(row, "WARNING", QB_FIELD_REF_NO, "Ref No. is blank"))

        if (gst is not None and gst != 0) or (pst is not None and pst != 0):
            issues.append(
                _issue(
                    row,
                    "WARNING",
                    "gst/pst",
                    "GST/PST present; final tax IIF output is not implemented in Phase 1",
                )
            )

    return issues


def summarize_issues(issues: list[ValidationIssue]) -> dict[str, int]:
    blocked_rows = {
        (issue.sheet_name, issue.row_number)
        for issue in issues
        if issue.severity == "BLOCK" and issue.row_number > 0
    }
    return {
        "blocks": sum(1 for issue in issues if issue.severity == "BLOCK"),
        "warnings": sum(1 for issue in issues if issue.severity == "WARNING"),
        "blocked_rows": len(blocked_rows),
    }
