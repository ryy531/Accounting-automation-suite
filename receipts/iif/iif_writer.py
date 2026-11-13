from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .account_list import AccountList
from .schema import (
    QB_FIELD_ACCOUNT,
    QB_FIELD_INCLUDE,
    QB_FIELD_MEMO,
    QB_FIELD_PAYMENT_ACCOUNT,
    QB_FIELD_PAYEE,
    QB_FIELD_REF_NO,
    QB_FIELD_TRANSACTION_TYPE,
    WorkbookRow,
)
from .vendor_list import VendorList


TAX_CODE_FIELD = "QB Tax Code"
VALID_TAX_CODES = {"G", "P", "S", "Z", "E"}
GST_TAX_NAME = "GST/HST Payable"
PST_TAX_NAME = "PST Payable (SK)"

IIF_TRANSACTION_TYPES = {
    "cheque": "CHEQUE",
    "credit card charge": "CREDIT CARD",
}
PAYMENT_CREDIT_CARD_TYPE_MARKERS = ("credit card", "ccard", "creditcard")


@dataclass(frozen=True)
class ExportBlock:
    sheet_name: str
    row_number: int
    reason: str


@dataclass(frozen=True)
class ExportResult:
    output_path: Path
    exported_rows: int
    blocked_rows: int
    blocks: list[ExportBlock]
    warnings: list[str]
    new_vendors: list[str]


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


def _format_amount(amount: Decimal) -> str:
    rounded = amount.quantize(Decimal("0.01"))
    return format(rounded, "f")


def _format_date(value: object) -> str:
    if isinstance(value, datetime):
        return f"{value.month}/{value.day}/{value.year}"
    if isinstance(value, date):
        return f"{value.month}/{value.day}/{value.year}"
    return _clean_text(value)


def _row_key(row: WorkbookRow) -> tuple[str, int]:
    return (row.sheet_name, row.row_number)


def _block(row: WorkbookRow, reason: str) -> ExportBlock:
    return ExportBlock(row.sheet_name, row.row_number, reason)


def _payee_for_row(row: WorkbookRow) -> str:
    return _clean_text(row.values.get(QB_FIELD_PAYEE)) or _clean_text(row.values.get("description"))


def _account_type(accounts: AccountList, account_name: object) -> str:
    account = accounts.get(account_name)
    return _clean_text(account.account_type if account else "").lower()


def _is_bank_account(accounts: AccountList, account_name: object) -> bool:
    account_type = _account_type(accounts, account_name)
    return account_type == "bank"


def _is_credit_card_account(accounts: AccountList, account_name: object) -> bool:
    account_type = _account_type(accounts, account_name)
    return any(marker in account_type for marker in PAYMENT_CREDIT_CARD_TYPE_MARKERS)


def _find_tax_account_with_lookup(
    accounts: AccountList, tax_name: str, row: WorkbookRow, field: str
) -> list[ExportBlock]:
    """Find tax account using TaxAccountLookup and return blocks if any."""
    lookup = accounts.find_tax_account(tax_name)
    if lookup.lookup_status == "found":
        return []
    if lookup.lookup_status == "not_found":
        return [_block(row, f"Tax account not found in COA: {tax_name}")]
    # multiple_matches
    candidates_str = ", ".join(lookup.candidates[:3])
    if len(lookup.candidates) > 3:
        candidates_str += f", ... ({len(lookup.candidates)} total)"
    return [
        _block(
            row,
            f"Multiple tax accounts match '{tax_name}' in COA: {candidates_str}. "
            "Please ensure only one account name contains this tax name.",
        )
    ]


def _export_blocks_for_row(
    row: WorkbookRow,
    accounts: AccountList,
    allow_review: bool,
    preblocked_rows: set[tuple[str, int]],
) -> list[ExportBlock]:
    values = row.values
    blocks: list[ExportBlock] = []

    if _row_key(row) in preblocked_rows:
        blocks.append(_block(row, "Row has validation block; export skipped"))

    if not _is_true(values.get(QB_FIELD_INCLUDE)):
        blocks.append(_block(row, "Include in IIF must be TRUE"))

    if not _clean_text(values.get("date")):
        blocks.append(_block(row, "Date is required"))

    payee = _payee_for_row(row)
    if not payee:
        blocks.append(_block(row, "Description/vendor is required"))

    total = _parse_decimal(values.get("total"))
    if total is None:
        blocks.append(_block(row, "total is required"))
    elif total <= 0:
        blocks.append(_block(row, "total must be greater than 0"))

    transaction_type = _clean_text(values.get(QB_FIELD_TRANSACTION_TYPE))
    if transaction_type.lower() not in IIF_TRANSACTION_TYPES:
        blocks.append(
            _block(
                row,
                "QB Transaction Type must be Cheque or Credit Card Charge for receipt export",
            )
        )

    qb_account = _clean_text(values.get(QB_FIELD_ACCOUNT))
    if not qb_account:
        blocks.append(_block(row, "QB Account is required"))
    elif not accounts.has_account(qb_account):
        blocks.append(_block(row, f"QB Account not found in COA: {qb_account}"))

    payment_account = _clean_text(values.get(QB_FIELD_PAYMENT_ACCOUNT))
    if not payment_account:
        blocks.append(_block(row, "Payment Account is required"))
    elif not accounts.has_account(payment_account):
        blocks.append(_block(row, f"Payment Account not found in COA: {payment_account}"))
    else:
        payment_account_type = _account_type(accounts, payment_account)
        if (
            payment_account_type
            and transaction_type.lower() == "credit card charge"
            and not _is_credit_card_account(accounts, payment_account)
        ):
            blocks.append(
                _block(
                    row,
                    "Credit Card Charge requires a Credit Card type Payment Account; "
                    f"{payment_account} is not Credit Card",
                )
            )
        elif (
            payment_account_type
            and transaction_type.lower() == "cheque"
            and not _is_bank_account(accounts, payment_account)
        ):
            blocks.append(
                _block(
                    row,
                    "Cheque requires a Bank type Payment Account; "
                    f"{payment_account} is not Bank",
                )
            )

    tax_code = _clean_text(values.get(TAX_CODE_FIELD))
    if tax_code and tax_code.upper() not in VALID_TAX_CODES:
        blocks.append(_block(row, f"Invalid QB Tax Code: {tax_code}"))

    gst = _parse_decimal(values.get("gst")) or Decimal("0")
    pst = _parse_decimal(values.get("pst")) or Decimal("0")
    if gst != 0:
        blocks.extend(_find_tax_account_with_lookup(accounts, GST_TAX_NAME, row, "gst"))
    if pst != 0:
        blocks.extend(_find_tax_account_with_lookup(accounts, PST_TAX_NAME, row, "pst"))

    if total is not None:
        expense_amount = total - gst - pst
        if expense_amount <= 0:
            blocks.append(_block(row, "Expense amount after GST/PST must be greater than 0"))

    return blocks


def _iif_line(values: list[object]) -> str:
    return "\t".join(_clean_text(value) for value in values)


def _transaction_lines(row: WorkbookRow, accounts: AccountList) -> list[str]:
    values = row.values
    transaction_type = IIF_TRANSACTION_TYPES[_clean_text(values.get(QB_FIELD_TRANSACTION_TYPE)).lower()]
    transaction_date = _format_date(values.get("date"))
    payee = _payee_for_row(row)
    expense_account = _clean_text(values.get(QB_FIELD_ACCOUNT))
    payment_account = _clean_text(values.get(QB_FIELD_PAYMENT_ACCOUNT))
    memo = _clean_text(values.get(QB_FIELD_MEMO)) or _clean_text(values.get("description"))
    ref_no = _clean_text(values.get(QB_FIELD_REF_NO))

    total = _parse_decimal(values.get("total")) or Decimal("0")
    gst = _parse_decimal(values.get("gst")) or Decimal("0")
    pst = _parse_decimal(values.get("pst")) or Decimal("0")
    expense_amount = total - gst - pst

    # Find tax accounts using TaxAccountLookup
    gst_lookup = accounts.find_tax_account(GST_TAX_NAME) if gst != 0 else None
    pst_lookup = accounts.find_tax_account(PST_TAX_NAME) if pst != 0 else None

    lines = [
        _iif_line(
            [
                "TRNS",
                transaction_type,
                transaction_date,
                payment_account,
                payee,
                "",
                _format_amount(-total),
                ref_no,
                memo,
            ]
        ),
        _iif_line(
            [
                "SPL",
                transaction_type,
                transaction_date,
                expense_account,
                "",
                "",
                _format_amount(expense_amount),
                ref_no,
                memo,
            ]
        ),
    ]

    if gst != 0 and gst_lookup and gst_lookup.account:
        lines.append(
            _iif_line(
                [
                    "SPL",
                    transaction_type,
                    transaction_date,
                    gst_lookup.account.name,
                    "",
                    "",
                    _format_amount(gst),
                    ref_no,
                    "GST",
                ]
            )
        )

    if pst != 0 and pst_lookup and pst_lookup.account:
        lines.append(
            _iif_line(
                [
                    "SPL",
                    transaction_type,
                    transaction_date,
                    pst_lookup.account.name,
                    "",
                    "",
                    _format_amount(pst),
                    ref_no,
                    "PST",
                ]
            )
        )

    lines.append("ENDTRNS")
    return lines


def write_receipt_iif(
    rows: list[WorkbookRow],
    accounts: AccountList,
    output_path: Path,
    allow_review: bool = False,
    preblocked_rows: set[tuple[str, int]] | None = None,
    vendors: VendorList | None = None,
    allow_vendor_creation: bool = False,
) -> ExportResult:
    preblocked = preblocked_rows or set()
    blocks: list[ExportBlock] = []
    warnings: list[str] = []
    transaction_header_lines = [
        _iif_line(["!TRNS", "TRNSTYPE", "DATE", "ACCNT", "NAME", "CLASS", "AMOUNT", "DOCNUM", "MEMO"]),
        _iif_line(["!SPL", "TRNSTYPE", "DATE", "ACCNT", "NAME", "CLASS", "AMOUNT", "DOCNUM", "MEMO"]),
        "!ENDTRNS",
    ]
    transaction_lines: list[str] = []
    new_vendors: list[str] = []
    new_vendor_names: set[str] = set()

    exported_rows = 0
    for row in rows:
        row_blocks = _export_blocks_for_row(row, accounts, allow_review, preblocked)
        if row_blocks:
            blocks.extend(row_blocks)
            continue

        gst = _parse_decimal(row.values.get("gst")) or Decimal("0")
        pst = _parse_decimal(row.values.get("pst")) or Decimal("0")
        if gst != 0 or pst != 0:
            warnings.append(
                f"{row.sheet_name}!{row.row_number}: GST/PST exported as separate account split lines; verify in QuickBooks test company."
            )

        payee = _payee_for_row(row)
        if allow_vendor_creation and vendors is not None and not vendors.has_vendor(payee) and payee not in new_vendor_names:
            new_vendor_names.add(payee)
            new_vendors.append(payee)

        transaction_lines.extend(_transaction_lines(row, accounts))
        exported_rows += 1

    iif_lines: list[str] = []
    if allow_vendor_creation and new_vendors:
        iif_lines.append(_iif_line(["!VEND", "NAME"]))
        for vendor_name in new_vendors:
            iif_lines.append(_iif_line(["VEND", vendor_name]))

    iif_lines.extend(transaction_header_lines)
    iif_lines.extend(transaction_lines)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="cp1252", newline="") as iif_file:
        iif_file.write("\r\n".join(iif_lines) + "\r\n")

    blocked_row_keys = {(block.sheet_name, block.row_number) for block in blocks}
    return ExportResult(
        output_path=output_path,
        exported_rows=exported_rows,
        blocked_rows=len(blocked_row_keys),
        blocks=blocks,
        warnings=warnings,
        new_vendors=new_vendors,
    )
