from __future__ import annotations

import json
import sys
from copy import copy
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.cell_range import CellRange
from openpyxl.worksheet.datavalidation import DataValidation, DataValidationList

from .account_list import AccountList
from .schema import (
    GENERATED_SHEET_NAMES,
    QB_FIELD_ACCOUNT,
    QB_FIELD_INCLUDE,
    QB_FIELD_MEMO,
    QB_FIELD_NOTE,
    QB_FIELD_PAYEE,
    QB_FIELD_PAYMENT_ACCOUNT,
    QB_FIELD_READY,
    QB_FIELD_REF_NO,
    QB_FIELD_TRANSACTION_TYPE,
)
from .vendor_list import VendorList


ACCOUNT_LIST_SHEET = "Account_List"
VENDOR_LIST_SHEET = "Vendor_List"
QB_TAX_CODE_FIELD = "QB Tax Code"
ACCOUNT_CONFIDENCE_FIELD = "Account Confidence"
NEEDS_REVIEW_FIELD = "needs_review"
NOTE_FIELD = "note"
QB_HEADER_FILL = PatternFill("solid", fgColor="C6E0B4")
QB_HEADER_FONT = Font(bold=True)
HIDDEN_QB_FIELDS = {
    QB_FIELD_NOTE,
    QB_FIELD_READY,
    QB_TAX_CODE_FIELD,
    ACCOUNT_CONFIDENCE_FIELD,
}
QB_HEADER_FIELDS = {
    QB_FIELD_INCLUDE,
    QB_FIELD_TRANSACTION_TYPE,
    QB_FIELD_PAYEE,
    QB_FIELD_ACCOUNT,
    QB_FIELD_PAYMENT_ACCOUNT,
    QB_FIELD_MEMO,
    QB_FIELD_REF_NO,
    QB_FIELD_READY,
    QB_FIELD_NOTE,
    QB_TAX_CODE_FIELD,
    ACCOUNT_CONFIDENCE_FIELD,
}
REVIEW_ROW_FILL = PatternFill("solid", fgColor="FFF59D")
DATA_ROW_HEIGHT = 18
HEADER_ROW_HEIGHT = 22
REVIEW_COLUMN_ORDER = [
    "date",
    "description",
    "subtotal",
    "gst",
    "pst",
    "total",
    "payment_card_last_four",
    QB_FIELD_INCLUDE,
    QB_FIELD_ACCOUNT,
    QB_FIELD_PAYMENT_ACCOUNT,
    QB_FIELD_TRANSACTION_TYPE,
    QB_FIELD_MEMO,
    QB_FIELD_REF_NO,
    NOTE_FIELD,
    "source_file",
    "page_num",
    NEEDS_REVIEW_FIELD,
    "has_line_items",
    "doc_type",
    "thumbnail_path",
    QB_TAX_CODE_FIELD,
    QB_FIELD_READY,
    QB_FIELD_NOTE,
    ACCOUNT_CONFIDENCE_FIELD,
    QB_FIELD_PAYEE,
]
TRANSACTION_TYPE_OPTIONS = ["Cheque"]


@dataclass(frozen=True)
class PrefillResult:
    output_workbook: Path
    rows_checked: int
    accounts_filled: int
    payment_accounts_filled: int
    tax_codes_filled: int
    review_rows: int
    ai_failures: int


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


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


def _generated_sheet(sheet_name: str) -> bool:
    return sheet_name.strip().lower() in GENERATED_SHEET_NAMES


def _header_map(worksheet) -> dict[str, int]:
    headers: dict[str, int] = {}
    for column_index, cell in enumerate(worksheet[1], start=1):
        header = _clean_text(cell.value)
        if header:
            headers[header] = column_index
    return headers


def _ensure_column(worksheet, headers: dict[str, int], name: str) -> int:
    existing = headers.get(name)
    if existing:
        return existing

    column_index = worksheet.max_column + 1
    worksheet.cell(row=1, column=column_index).value = name
    headers[name] = column_index
    return column_index


def _hide_column(worksheet, column_index: int) -> None:
    column_letter = worksheet.cell(row=1, column=column_index).column_letter
    worksheet.column_dimensions[column_letter].hidden = True


def _copy_cell(source, target) -> None:
    target.value = source.value
    if source.has_style:
        target.font = copy(source.font)
        target.fill = copy(source.fill)
        target.border = copy(source.border)
        target.alignment = copy(source.alignment)
        target.number_format = source.number_format
        target.protection = copy(source.protection)
    if source.hyperlink:
        target._hyperlink = copy(source.hyperlink)
    if source.comment:
        target.comment = copy(source.comment)


def _move_column(worksheet, from_index: int, to_index: int) -> None:
    if from_index == to_index:
        return

    source_letter = worksheet.cell(row=1, column=from_index).column_letter
    source_dimension = copy(worksheet.column_dimensions[source_letter])

    insert_index = to_index if from_index > to_index else to_index + 1
    worksheet.insert_cols(insert_index)
    source_index = from_index + 1 if from_index >= insert_index else from_index
    for row_number in range(1, worksheet.max_row + 1):
        _copy_cell(
            worksheet.cell(row=row_number, column=source_index),
            worksheet.cell(row=row_number, column=insert_index),
        )

    target_letter = worksheet.cell(row=1, column=insert_index).column_letter
    worksheet.column_dimensions[target_letter].width = source_dimension.width
    worksheet.column_dimensions[target_letter].hidden = source_dimension.hidden

    worksheet.delete_cols(source_index)


def _reorder_columns(worksheet, headers: dict[str, int]) -> dict[str, int]:
    target_index = 1
    for header in REVIEW_COLUMN_ORDER:
        headers = _header_map(worksheet)
        current_index = headers.get(header)
        if not current_index:
            continue
        _move_column(worksheet, current_index, target_index)
        target_index += 1
    return _header_map(worksheet)


def _style_qb_headers(worksheet, headers: dict[str, int]) -> None:
    for field_name in QB_HEADER_FIELDS:
        column_index = headers.get(field_name)
        if not column_index:
            continue
        cell = worksheet.cell(row=1, column=column_index)
        cell.fill = copy(QB_HEADER_FILL)
        cell.font = copy(QB_HEADER_FONT)


def _row_values(worksheet, row_number: int, headers: dict[str, int]) -> dict[str, Any]:
    return {
        header: worksheet.cell(row=row_number, column=column_index).value
        for header, column_index in headers.items()
    }


def _append_note(existing: object, note: str) -> str:
    text = _clean_text(existing)
    if not text:
        return note
    if note in text:
        return text
    return f"{text}; {note}"


NOTE_JUNK_FRAGMENTS = (
    "AI account suggestion failed",
    "AI did not suggest",
    "AI suggested invalid",
    "AI returned invalid confidence",
    "No Bank/Credit Card Payment Account",
    "No Bank Payment Account",
    "Review required before IIF export",
    "503 UNAVAILABLE",
    "504 UNAVAILABLE",
    "502 UNAVAILABLE",
    "503 Service Unavailable",
    "504 Gateway Timeout",
    "No module named 'openai'",
)


def _clean_note(existing_note: object) -> str:
    """Remove known QB/AI technical fragments from a note, preserving OCR/business content.

    Splits the note by semicolons, removes segments containing known junk fragments,
    and returns the cleaned note. Returns empty string if all segments are removed.
    """
    text = _clean_text(existing_note)
    if not text:
        return ""

    segments = [seg.strip() for seg in text.split(";")]
    clean_segments = []

    for segment in segments:
        if not segment:
            continue
        is_junk = False
        for junk in NOTE_JUNK_FRAGMENTS:
            if junk in segment:
                is_junk = True
                break
        if not is_junk:
            clean_segments.append(segment)

    return "; ".join(clean_segments)


def _is_true(value: object) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


def _apply_review_layout(worksheet, headers: dict[str, int]) -> None:
    worksheet.row_dimensions[1].height = HEADER_ROW_HEIGHT
    for row_number in range(2, worksheet.max_row + 1):
        worksheet.row_dimensions[row_number].height = DATA_ROW_HEIGHT

    for field_name in ("description", NOTE_FIELD):
        column_index = headers.get(field_name)
        if not column_index:
            continue
        for row_number in range(2, worksheet.max_row + 1):
            worksheet.cell(row=row_number, column=column_index).alignment = Alignment(
                wrap_text=False,
                vertical="top",
            )


def _apply_review_highlights(worksheet, headers: dict[str, int], flagged_rows: set[int] | None = None) -> None:
    """Apply review highlights to rows that need attention.

    This function first clears all review fills (except header), then applies
    highlights to:
    1. Legacy rows: old needs_review=True with non-empty cleaned note
    2. Current flagged rows: rows with issues found in this run

    Args:
        worksheet: The worksheet to process
        headers: Column header mapping
        flagged_rows: Set of row numbers that need highlighting from this run
    """
    review_column = headers.get(NEEDS_REVIEW_FIELD)
    note_column = headers.get(NOTE_FIELD)

    # Step 1: Clear all review fills (except header row)
    # The review yellow color may be stored with or without alpha channel
    review_yellow_rgb = "FFF59D"
    for row_number in range(2, worksheet.max_row + 1):
        for column_index in range(1, worksheet.max_column + 1):
            cell = worksheet.cell(row=row_number, column=column_index)
            if cell.fill and cell.fill.fgColor:
                rgb = cell.fill.fgColor.rgb or ""
                # Handle both 6-digit (RGB) and 8-digit (ARGB) formats
                # RGB: "FFF59D", ARGB: "00FFF59D" or "FFFFF59D"
                if len(rgb) == 8:
                    rgb = rgb[2:]  # Remove alpha channel (first 2 chars)
                if rgb == review_yellow_rgb:
                    cell.fill = PatternFill()

    # Step 2: Collect legacy rows (old needs_review=True with non-empty cleaned note)
    legacy_rows = set()
    if review_column:
        for row_number in range(2, worksheet.max_row + 1):
            if _is_true(worksheet.cell(row=row_number, column=review_column).value):
                # Check if cleaned note is non-empty
                if note_column:
                    old_note = worksheet.cell(row=row_number, column=note_column).value
                    cleaned_note = _clean_note(old_note)
                    if cleaned_note:
                        legacy_rows.add(row_number)

    # Step 3: Apply highlights to legacy ∪ flagged rows
    rows_to_highlight = legacy_rows
    if flagged_rows:
        rows_to_highlight = rows_to_highlight | flagged_rows

    for row_number in rows_to_highlight:
        for column_index in range(1, worksheet.max_column + 1):
            worksheet.cell(row=row_number, column=column_index).fill = copy(REVIEW_ROW_FILL)

    # Step 4: Sync needs_review cell values to match the highlight set
    if review_column:
        for row_number in range(2, worksheet.max_row + 1):
            worksheet.cell(row=row_number, column=review_column).value = (
                row_number in rows_to_highlight
            )


def _first_text(values: dict[str, Any], field_names: tuple[str, ...]) -> str:
    for field_name in field_names:
        text = _clean_text(values.get(field_name))
        if text:
            return text
    return ""


def _payment_account_names(accounts: AccountList) -> list[str]:
    """Return only Bank-type payment account names for receipt payments."""
    return accounts.bank_payment_account_names()


def _default_transaction_type(values: dict[str, Any], payment_account: str, accounts: AccountList) -> str:
    """Return default transaction type for receipt payments (always Cheque)."""
    return "Cheque"


def _tax_code_for_row(values: dict[str, Any]) -> tuple[str, str]:
    gst = _parse_decimal(values.get("gst"))
    pst = _parse_decimal(values.get("pst"))
    gst_present = gst is not None and gst != 0
    pst_present = pst is not None and pst != 0

    if gst_present and pst_present:
        return "S", ""
    if gst_present:
        return "G", ""
    if pst_present:
        return "P", ""

    raw_gst = _clean_text(values.get("gst"))
    raw_pst = _clean_text(values.get("pst"))
    if (raw_gst and gst is None) or (raw_pst and pst is None):
        return "", "Tax amount could not be parsed; review tax code"
    return "", ""


def _install_ai_client():
    scripts_root = Path(__file__).resolve().parents[2]
    lib_path = scripts_root / "_lib"
    if str(lib_path) not in sys.path:
        sys.path.insert(0, str(lib_path))
    from connectors.ai_client import AIClient

    return AIClient


def _build_prompt(
    values: dict[str, Any],
    account_names: list[str],
) -> str:
    payload = {
        "row": {
            "vendor_or_payee": values.get("vendor")
            or values.get("payee")
            or values.get("merchant")
            or values.get("name")
            or values.get("QB Payee")
            or "",
            "description": values.get("description") or values.get("memo") or "",
            "amount": values.get("total") or values.get("amount") or "",
            "gst": values.get("gst") or "",
            "pst": values.get("pst") or "",
        },
        "valid_qb_accounts": account_names,
    }
    return (
        "Choose the best QuickBooks expense account for this receipt row.\n"
        "Use these business hints when applicable: SaskPower -> Utilities; Costco purchases or supplies -> Supplies; bank fee/service charge -> Bank Charges.\n"
        "Return JSON only with keys: qb_account, confidence, review_note.\n"
        "qb_account must be exactly one of valid_qb_accounts, or an empty string if uncertain.\n"
        "confidence must be HIGH, MEDIUM, or LOW.\n"
        "Do not invent accounts. Do not choose payment/bank/credit card accounts as qb_account unless the row is truly a bank fee.\n"
        f"{json.dumps(payload, default=str, ensure_ascii=False)}"
    )


def _suggest_accounts(
    values: dict[str, Any],
    accounts: AccountList,
) -> tuple[str, str, str, bool]:
    """Suggest QB account using AI. Payment account is NOT suggested by AI.

    Returns:
        (qb_account, confidence, note, invalid_account)
    """
    account_names = list(accounts.accounts.keys())
    AIClient = _install_ai_client()
    client = AIClient(timeout=30, max_retries=1)
    response = client.call(
        prompt=_build_prompt(values, account_names),
        file_parts=None,
        json_mode=True,
        timeout=30,
        max_retries=1,
    )

    suggested = _clean_text(response.get("qb_account"))
    confidence = _clean_text(response.get("confidence")).upper() or "LOW"
    # AI system-related notes are not added to row-level notes
    note = ""
    if confidence not in {"HIGH", "MEDIUM", "LOW"}:
        confidence = "LOW"

    invalid_account = False

    if suggested and accounts.has_account(suggested):
        qb_account = suggested
    else:
        qb_account = ""
        invalid_account = bool(suggested)

    return qb_account, confidence, note, invalid_account


def _rebuild_account_list_sheet(
    workbook,
    account_names: list[str],
    payment_account_names: list[str],
) -> None:
    for sheet_name in list(workbook.sheetnames):
        if sheet_name.strip().lower() == ACCOUNT_LIST_SHEET.lower():
            del workbook[sheet_name]

    worksheet = workbook.create_sheet(ACCOUNT_LIST_SHEET)
    worksheet["A1"] = "QB Account"
    worksheet["B1"] = "Payment Account"
    for row_number, account_name in enumerate(account_names, start=2):
        worksheet.cell(row=row_number, column=1).value = account_name
    for row_number, account_name in enumerate(payment_account_names, start=2):
        worksheet.cell(row=row_number, column=2).value = account_name
    worksheet.sheet_state = "hidden"


def _rebuild_vendor_list_sheet(workbook, vendor_names: list[str]) -> None:
    for sheet_name in list(workbook.sheetnames):
        if sheet_name.strip().lower() == VENDOR_LIST_SHEET.lower():
            del workbook[sheet_name]

    worksheet = workbook.create_sheet(VENDOR_LIST_SHEET)
    worksheet["A1"] = "QB Vendor"
    for row_number, vendor_name in enumerate(vendor_names, start=2):
        worksheet.cell(row=row_number, column=1).value = vendor_name
    worksheet.sheet_state = "hidden"


def _remove_overlapping_validations(
    worksheet,
    column_index: int,
    last_row: int,
) -> None:
    """Remove any existing validations that overlap with the specified column data range.

    Only removes validations that overlap with the data range (rows 2 to last_row),
    preserving any validations in the header row or below the data range.

    Args:
        worksheet: The worksheet to process
        column_index: 1-based column index
        last_row: The last row number of the data range (inclusive)
    """
    column_letter = worksheet.cell(row=1, column=column_index).column_letter
    target_range = CellRange(f'{column_letter}2:{column_letter}{last_row}')

    filtered_validations = []
    for v in worksheet.data_validations.dataValidation:
        has_overlap = False
        for existing_range in v.sqref.ranges:
            if not existing_range.isdisjoint(target_range):
                has_overlap = True
                break
        if not has_overlap:
            filtered_validations.append(v)

    worksheet.data_validations = DataValidationList(
        disablePrompts=worksheet.data_validations.disablePrompts,
        dataValidation=filtered_validations
    )


def _add_dropdown(
    worksheet,
    column_index: int,
    last_row: int,
    sheet_name: str,
    list_column: str,
    item_count: int,
) -> None:
    """Add a dropdown validation to a column, removing any overlapping validations first.

    This function is idempotent: running it multiple times will not accumulate validations.
    """
    if last_row < 2 or item_count < 1:
        return

    _remove_overlapping_validations(worksheet, column_index, last_row)

    formula = f"={sheet_name}!${list_column}$2:${list_column}${item_count + 1}"
    validation = DataValidation(type="list", formula1=formula, allow_blank=True)
    worksheet.add_data_validation(validation)
    column_letter = worksheet.cell(row=1, column=column_index).column_letter
    validation.add(f"{column_letter}2:{column_letter}{last_row}")


def _add_inline_dropdown(
    worksheet,
    column_index: int,
    last_row: int,
    options: list[str],
) -> None:
    """Add an inline dropdown validation to a column, removing any overlapping validations first.

    This function is idempotent: running it multiple times will not accumulate validations.
    """
    if last_row < 2 or not options:
        return

    _remove_overlapping_validations(worksheet, column_index, last_row)

    formula = '"' + ",".join(options) + '"'
    validation = DataValidation(type="list", formula1=formula, allow_blank=True)
    worksheet.add_data_validation(validation)
    column_letter = worksheet.cell(row=1, column=column_index).column_letter
    validation.add(f"{column_letter}2:{column_letter}{last_row}")


def refresh_qb_review_controls(
    workbook,
    accounts: AccountList,
    vendors: VendorList | None = None,
) -> None:
    """Rebuild hidden account/vendor lists and refresh dropdowns on all data sheets.

    This ensures QB-related dropdowns are always present and up-to-date,
    even if the workbook has been manually edited or the sheet was created elsewhere.

    Note: openpyxl's DataValidation.add() replaces any existing validation on the
    same cell range, making this function idempotent for QB-related columns.
    """
    account_names = sorted(accounts.accounts.keys(), key=str.lower)
    payment_account_names = accounts.bank_payment_account_names()
    vendor_names = sorted(vendors.vendors.keys(), key=str.lower) if vendors else []

    _rebuild_account_list_sheet(workbook, account_names, payment_account_names)
    if vendors is not None:
        _rebuild_vendor_list_sheet(workbook, vendor_names)

    for worksheet in workbook.worksheets:
        if _generated_sheet(worksheet.title):
            continue

        headers = _header_map(worksheet)
        if not {"date", "description", "total"}.issubset(headers):
            continue

        for field_name in (
            QB_FIELD_INCLUDE,
            QB_FIELD_TRANSACTION_TYPE,
            QB_FIELD_ACCOUNT,
            QB_FIELD_PAYMENT_ACCOUNT,
            QB_FIELD_MEMO,
            QB_FIELD_REF_NO,
            QB_FIELD_READY,
            QB_FIELD_NOTE,
            QB_TAX_CODE_FIELD,
            ACCOUNT_CONFIDENCE_FIELD,
            NOTE_FIELD,
        ):
            _ensure_column(worksheet, headers, field_name)

        headers = _reorder_columns(worksheet, headers)
        _style_qb_headers(worksheet, headers)

        qb_account_col = headers.get(QB_FIELD_ACCOUNT)
        payment_col = headers.get(QB_FIELD_PAYMENT_ACCOUNT)
        description_col = headers.get("description")
        transaction_type_col = headers.get(QB_FIELD_TRANSACTION_TYPE)

        if qb_account_col:
            _add_dropdown(worksheet, qb_account_col, worksheet.max_row, ACCOUNT_LIST_SHEET, "A", len(account_names))
        if payment_col:
            _add_dropdown(worksheet, payment_col, worksheet.max_row, ACCOUNT_LIST_SHEET, "B", len(payment_account_names))
        if description_col and vendors is not None:
            _add_dropdown(worksheet, description_col, worksheet.max_row, VENDOR_LIST_SHEET, "A", len(vendor_names))
        if transaction_type_col:
            _add_inline_dropdown(worksheet, transaction_type_col, worksheet.max_row, TRANSACTION_TYPE_OPTIONS)

        for field_name in HIDDEN_QB_FIELDS:
            column_index = headers.get(field_name)
            if column_index:
                _hide_column(worksheet, column_index)

        _apply_review_layout(worksheet, headers)


def prefill_receipt_accounts(
    workbook_path: Path,
    output_workbook: Path,
    accounts: AccountList,
    default_payment_account: str = "",
    allow_in_place: bool = False,
    vendors: VendorList | None = None,
) -> PrefillResult:
    if workbook_path.resolve() == output_workbook.resolve() and not allow_in_place:
        raise ValueError("Output workbook must be different from input workbook.")

    payment_account = _clean_text(default_payment_account)
    if payment_account:
        if not accounts.has_account(payment_account):
            raise ValueError(f"Default payment account not found in COA: {payment_account}")
        bank_names = accounts.bank_payment_account_names()
        if payment_account not in bank_names:
            raise ValueError(
                f"Default payment account must be a Bank-type account. "
                f"'{payment_account}' is not in the Bank account list. "
                f"Valid Bank accounts: {', '.join(bank_names[:5])}{'...' if len(bank_names) > 5 else ''}"
            )

    account_names = sorted(accounts.accounts.keys(), key=str.lower)
    payment_account_names = _payment_account_names(accounts)
    vendor_names = sorted(vendors.vendors.keys(), key=str.lower) if vendors else []
    workbook = load_workbook(workbook_path)
    rows_checked = 0
    accounts_filled = 0
    payment_accounts_filled = 0
    tax_codes_filled = 0
    review_rows = 0
    ai_failures = 0

    refresh_qb_review_controls(workbook, accounts, vendors)

    for worksheet in workbook.worksheets:
        if _generated_sheet(worksheet.title):
            continue

        headers = _header_map(worksheet)
        if not {"date", "description", "total", "needs_review"}.issubset(headers):
            continue

        # Track rows that need highlighting in this worksheet
        flagged_rows: set[int] = set()

        include_col = headers.get(QB_FIELD_INCLUDE)
        transaction_type_col = headers.get(QB_FIELD_TRANSACTION_TYPE)
        qb_account_col = headers.get(QB_FIELD_ACCOUNT)
        payment_col = headers.get(QB_FIELD_PAYMENT_ACCOUNT)
        memo_col = headers.get(QB_FIELD_MEMO)
        ready_col = headers.get(QB_FIELD_READY)
        iif_note_col = headers.get(QB_FIELD_NOTE)
        tax_code_col = headers.get(QB_TAX_CODE_FIELD)
        confidence_col = headers.get(ACCOUNT_CONFIDENCE_FIELD)
        note_col = headers.get(NOTE_FIELD)

        for row_number in range(2, worksheet.max_row + 1):
            values = _row_values(worksheet, row_number, headers)
            if all(value is None or str(value).strip() == "" for value in values.values()):
                continue

            rows_checked += 1
            review_needed = False
            notes: list[str] = []

            if not _clean_text(worksheet.cell(row=row_number, column=include_col).value):
                worksheet.cell(row=row_number, column=include_col).value = True

            if not _clean_text(worksheet.cell(row=row_number, column=memo_col).value):
                worksheet.cell(row=row_number, column=memo_col).value = _first_text(
                    values,
                    ("memo", "description"),
                )

            existing_account = _clean_text(worksheet.cell(row=row_number, column=qb_account_col).value)
            existing_payment = _clean_text(worksheet.cell(row=row_number, column=payment_col).value)
            ai_account = ""
            ai_confidence = "LOW"
            ai_note = ""
            invalid_account = False

            should_call_ai = not existing_account
            if should_call_ai:
                try:
                    ai_account, ai_confidence, ai_note, invalid_account = _suggest_accounts(values, accounts)
                except Exception as exc:
                    ai_account = ""
                    ai_confidence = "LOW"
                    ai_note = ""
                    invalid_account = True
                    ai_failures += 1

            if not existing_account:
                if ai_account:
                    worksheet.cell(row=row_number, column=qb_account_col).value = ai_account
                    accounts_filled += 1
                else:
                    review_needed = True

                worksheet.cell(row=row_number, column=confidence_col).value = ai_confidence
                if invalid_account or not ai_account:
                    review_needed = True
            elif not accounts.has_account(existing_account):
                notes.append(f"Existing QB Account is not in COA: {existing_account}")
                review_needed = True

            if payment_account and not existing_payment:
                worksheet.cell(row=row_number, column=payment_col).value = payment_account
                existing_payment = payment_account
                payment_accounts_filled += 1
            elif not existing_payment and not payment_account_names:
                notes.append("No Bank Payment Account candidates found in COA")
                review_needed = True
            elif not existing_payment:
                review_needed = True
            if existing_payment and not accounts.has_account(existing_payment):
                notes.append(f"Payment Account is not in COA: {existing_payment}")
                review_needed = True

            if not _clean_text(worksheet.cell(row=row_number, column=transaction_type_col).value):
                worksheet.cell(row=row_number, column=transaction_type_col).value = _default_transaction_type(
                    values,
                    existing_payment,
                    accounts,
                )

            existing_tax_code = _clean_text(worksheet.cell(row=row_number, column=tax_code_col).value)
            if not existing_tax_code:
                tax_code, tax_note = _tax_code_for_row(values)
                if tax_code:
                    worksheet.cell(row=row_number, column=tax_code_col).value = tax_code
                    tax_codes_filled += 1
                if tax_note:
                    notes.append(tax_note)
                    review_needed = True

            # Always process notes: clean old junk, append new notes if any
            current_note = worksheet.cell(row=row_number, column=note_col).value
            cleaned_note = _clean_note(current_note)
            if notes:
                for note in notes:
                    cleaned_note = _append_note(cleaned_note, note)
            worksheet.cell(row=row_number, column=note_col).value = cleaned_note if cleaned_note else ""

            if review_needed:
                flagged_rows.add(row_number)
                worksheet.cell(row=row_number, column=ready_col).value = False
                review_rows += 1

        _apply_review_highlights(worksheet, headers, flagged_rows)

    output_workbook.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_workbook)
    return PrefillResult(
        output_workbook=output_workbook,
        rows_checked=rows_checked,
        accounts_filled=accounts_filled,
        payment_accounts_filled=payment_accounts_filled,
        tax_codes_filled=tax_codes_filled,
        review_rows=review_rows,
        ai_failures=ai_failures,
    )
