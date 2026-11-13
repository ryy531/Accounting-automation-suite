from __future__ import annotations

from copy import copy
from dataclasses import dataclass

from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


AUDIT_SHEET_TITLE = "Source_Audit"
AUDIT_COLUMNS = [
    "source_file",
    "page_num",
    "thumbnail_path",
    "visible_transaction_count",
    "visible_payment_slip_count",
    "extracted_transaction_count",
    "output_row_count",
    "status",
    "output_rows",
    "note",
]

VISIBLE_TRANSACTION_KEYS = (
    "source_visible_transaction_count",
    "visible_transaction_count",
    "visible_receipt_count",
)
VISIBLE_PAYMENT_SLIP_KEYS = (
    "source_visible_payment_slip_count",
    "visible_payment_slip_count",
    "payment_slip_count",
)
COVERAGE_NOTE_KEYS = (
    "source_coverage_note",
    "coverage_note",
)

INTERNAL_VISIBLE_TRANSACTION_COUNT = "_source_visible_transaction_count"
INTERNAL_VISIBLE_PAYMENT_SLIP_COUNT = "_source_visible_payment_slip_count"
INTERNAL_COVERAGE_NOTE = "_source_coverage_note"
INTERNAL_COVERAGE_STATUS = "_source_coverage_status"

STATUS_OK = "OK"
STATUS_MULTI_TRANSACTION = "MULTI_TRANSACTION_SOURCE"
STATUS_MISSING = "MISSING_TRANSACTION_SUSPECTED"
STATUS_DUPLICATE = "POSSIBLE_DUPLICATE"
STATUS_NO_ROWS = "NO_TRANSACTION_RETURNED"
STATUS_FAILED = "EXTRACTION_FAILED"
STATUS_CACHE_HIT = "CACHE_HIT"

_HEADER_FILL = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
_REVIEW_FILL = PatternFill(start_color="FFF59D", end_color="FFF59D", fill_type="solid")
_FAILED_FILL = PatternFill(start_color="FFCDD2", end_color="FFCDD2", fill_type="solid")
_WHITE_BOLD = Font(bold=True, color="FFFFFF")
_GRAY_BORDER = Border(
    left=Side(style="thin", color="D9D9D9"),
    right=Side(style="thin", color="D9D9D9"),
    top=Side(style="thin", color="D9D9D9"),
    bottom=Side(style="thin", color="D9D9D9"),
)

_REVIEW_STATUSES = {
    STATUS_MULTI_TRANSACTION,
    STATUS_MISSING,
    STATUS_DUPLICATE,
    STATUS_NO_ROWS,
    STATUS_FAILED,
}


@dataclass(frozen=True)
class CoverageResult:
    rows: list[dict]
    audit_row: dict


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _parse_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = int(float(text))
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _first_int(rows: list[dict], keys: tuple[str, ...]) -> int | None:
    for row in rows:
        for key in keys:
            parsed = _parse_int(row.get(key))
            if parsed is not None:
                return parsed
    return None


def _first_text(rows: list[dict], keys: tuple[str, ...]) -> str:
    for row in rows:
        for key in keys:
            text = _clean_text(row.get(key))
            if text:
                return text
    return ""


def _append_note(row: dict, note: str) -> None:
    existing = _clean_text(row.get("note"))
    if note in existing:
        return
    row["note"] = f"{existing} {note}".strip()


def _mark_review(rows: list[dict], status: str, note: str) -> None:
    tag = f"[REVIEW: {status}] {note}"
    for row in rows:
        row["needs_review"] = True
        row[INTERNAL_COVERAGE_STATUS] = status
        _append_note(row, tag)


def _placeholder_row(
    source_file: str,
    page_num: int,
    thumbnail_path: str,
    row_num: int,
    note: str,
    template_row: dict | None,
    description: str = "[POSSIBLE MISSING RECEIPT]",
    status: str = STATUS_MISSING,
) -> dict:
    row = {
        "date": None,
        "description": description,
        "subtotal": None,
        "gst": None,
        "pst": None,
        "total": None,
        "payment_card_last_four": "",
        "has_line_items": False,
        "doc_type": "unknown",
        "note": note,
        "source_file": source_file,
        "page_num": page_num,
        "row_num": row_num,
        "row_index_within_page": row_num,
        "needs_review": True,
        "thumbnail_path": thumbnail_path,
        INTERNAL_COVERAGE_STATUS: status,
    }
    if template_row:
        for key in ("_batch_top_group", "_batch_sheet_key"):
            if key in template_row:
                row[key] = template_row[key]
    return row


def apply_page_coverage(
    transactions: list[dict],
    source_file: str,
    page_num: int,
    thumbnail_path: str,
    declared_type: str = "receipts",
) -> CoverageResult:
    extracted_count = len(transactions)
    visible_count = _first_int(transactions, VISIBLE_TRANSACTION_KEYS)
    payment_slip_count = _first_int(transactions, VISIBLE_PAYMENT_SLIP_KEYS)
    coverage_note = _first_text(transactions, COVERAGE_NOTE_KEYS)
    rows = [dict(row) for row in transactions]

    for row in rows:
        if visible_count is not None:
            row[INTERNAL_VISIBLE_TRANSACTION_COUNT] = visible_count
        if payment_slip_count is not None:
            row[INTERNAL_VISIBLE_PAYMENT_SLIP_COUNT] = payment_slip_count
        if coverage_note:
            row[INTERNAL_COVERAGE_NOTE] = coverage_note

    status = STATUS_OK
    note = coverage_note
    placeholders: list[dict] = []

    if extracted_count == 0:
        status = STATUS_NO_ROWS
        note = note or "AI returned 0 transactions for this source page; review if this page should contain a receipt."
        placeholders.append(
            _placeholder_row(
                source_file,
                page_num,
                thumbnail_path,
                1,
                f"[REVIEW: {STATUS_NO_ROWS}] {note}",
                None,
                description="[NO TRANSACTION RETURNED]",
                status=STATUS_NO_ROWS,
            )
        )
    elif visible_count is None:
        status = STATUS_MULTI_TRANSACTION if extracted_count > 1 else STATUS_OK
        if extracted_count > 1:
            note = note or "Multiple transactions were extracted from one source page; review for completeness."
    elif visible_count > extracted_count:
        status = STATUS_MISSING
        missing_count = visible_count - extracted_count
        note = (
            note
            or f"Detected {visible_count} visible accounting transaction(s) but extracted {extracted_count} row(s)."
        )
        for offset in range(missing_count):
            placeholders.append(
                _placeholder_row(
                    source_file,
                    page_num,
                    thumbnail_path,
                    extracted_count + offset + 1,
                    f"[REVIEW: {STATUS_MISSING}] {note}",
                    rows[0] if rows else None,
                )
            )
    elif visible_count < extracted_count:
        status = STATUS_DUPLICATE
        note = (
            note
            or f"Detected {visible_count} visible accounting transaction(s) but extracted {extracted_count} row(s)."
        )
    elif visible_count > 1 or extracted_count > 1:
        status = STATUS_MULTI_TRANSACTION
        note = note or "Multiple accounting transactions are visible on one source page; review extracted rows."

    if status in _REVIEW_STATUSES:
        _mark_review(rows, status, note)

    output_rows = rows + placeholders
    audit_row = {
        "source_file": source_file,
        "page_num": page_num,
        "thumbnail_path": thumbnail_path,
        "visible_transaction_count": visible_count,
        "visible_payment_slip_count": payment_slip_count,
        "extracted_transaction_count": extracted_count,
        "output_row_count": len(output_rows),
        "status": status,
        "output_rows": ",".join(str(row.get("row_num") or "") for row in output_rows),
        "note": note,
    }
    return CoverageResult(rows=output_rows, audit_row=audit_row)


def failed_page_audit_row(
    source_file: str,
    page_num: int,
    thumbnail_path: str,
    error: Exception,
) -> dict:
    return {
        "source_file": source_file,
        "page_num": page_num,
        "thumbnail_path": thumbnail_path,
        "visible_transaction_count": None,
        "visible_payment_slip_count": None,
        "extracted_transaction_count": 0,
        "output_row_count": 1,
        "status": STATUS_FAILED,
        "output_rows": "1",
        "note": f"{type(error).__name__}: {str(error)[:200]}",
    }


def cache_hit_audit_rows(source_file: str, rows: list[dict]) -> list[dict]:
    if not rows:
        return [
            {
                "source_file": source_file,
                "page_num": "",
                "thumbnail_path": "",
                "visible_transaction_count": None,
                "visible_payment_slip_count": None,
                "extracted_transaction_count": 0,
                "output_row_count": 0,
                "status": STATUS_CACHE_HIT,
                "output_rows": "",
                "note": "Cached OCR result reused; no rows were cached for this source.",
            }
        ]

    grouped: dict[int, list[dict]] = {}
    for row in rows:
        page_num = _parse_int(row.get("page_num")) or 1
        grouped.setdefault(page_num, []).append(row)

    audit_rows = []
    for page_num, page_rows in sorted(grouped.items()):
        visible_count = _first_int(page_rows, (INTERNAL_VISIBLE_TRANSACTION_COUNT,) + VISIBLE_TRANSACTION_KEYS)
        payment_slip_count = _first_int(page_rows, (INTERNAL_VISIBLE_PAYMENT_SLIP_COUNT,) + VISIBLE_PAYMENT_SLIP_KEYS)
        status = _first_text(page_rows, (INTERNAL_COVERAGE_STATUS,)) or STATUS_CACHE_HIT
        note = _first_text(page_rows, (INTERNAL_COVERAGE_NOTE,)) or "Cached OCR result reused."
        audit_rows.append(
            {
                "source_file": source_file,
                "page_num": page_num,
                "thumbnail_path": "",
                "visible_transaction_count": visible_count,
                "visible_payment_slip_count": payment_slip_count,
                "extracted_transaction_count": len(page_rows),
                "output_row_count": len(page_rows),
                "status": status,
                "output_rows": ",".join(str(row.get("row_num") or "") for row in page_rows),
                "note": note,
            }
        )
    return audit_rows


def _status_fill(status: str):
    if status == STATUS_FAILED:
        return _FAILED_FILL
    if status in _REVIEW_STATUSES:
        return _REVIEW_FILL
    return None


def write_source_audit_sheet(workbook, audit_rows: list[dict]):
    if not audit_rows:
        return None

    if AUDIT_SHEET_TITLE in workbook.sheetnames:
        del workbook[AUDIT_SHEET_TITLE]

    worksheet = workbook.create_sheet(AUDIT_SHEET_TITLE, 0)
    worksheet.sheet_properties.tabColor = "F4B183"
    worksheet.append(AUDIT_COLUMNS)

    for cell in worksheet[1]:
        cell.font = copy(_WHITE_BOLD)
        cell.fill = copy(_HEADER_FILL)
        cell.border = copy(_GRAY_BORDER)
        cell.alignment = Alignment(vertical="center")

    for audit_row in audit_rows:
        worksheet.append([audit_row.get(column, "") for column in AUDIT_COLUMNS])

    max_row = worksheet.max_row
    max_col = len(AUDIT_COLUMNS)
    has_review_status = False
    for row_idx in range(2, max_row + 1):
        status = _clean_text(worksheet.cell(row=row_idx, column=8).value)
        fill = _status_fill(status)
        if fill is not None:
            has_review_status = True
        for col_idx in range(1, max_col + 1):
            cell = worksheet.cell(row=row_idx, column=col_idx)
            cell.border = copy(_GRAY_BORDER)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            if fill is not None:
                cell.fill = copy(fill)

    widths = {
        "source_file": 42,
        "page_num": 10,
        "thumbnail_path": 45,
        "visible_transaction_count": 20,
        "visible_payment_slip_count": 20,
        "extracted_transaction_count": 22,
        "output_row_count": 18,
        "status": 28,
        "output_rows": 16,
        "note": 80,
    }
    for col_idx, column_name in enumerate(AUDIT_COLUMNS, start=1):
        worksheet.column_dimensions[get_column_letter(col_idx)].width = widths[column_name]

    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = f"A1:{get_column_letter(max_col)}{max_row}"
    if has_review_status:
        workbook.active = workbook.index(worksheet)
    return worksheet
