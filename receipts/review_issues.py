from __future__ import annotations

from copy import copy

from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


REVIEW_SHEET_TITLE = "Review_Issues"

ISSUE_TYPE_IIF_BLOCK = "IIF_BLOCK"
ISSUE_TYPE_QB_IMPORT_ERROR = "QB_IMPORT_ERROR"
ISSUE_TYPE_POSSIBLE_MISSING_RECEIPT = "POSSIBLE_MISSING_RECEIPT"
ISSUE_TYPE_OCR_REVIEW = "OCR_REVIEW"
ISSUE_TYPE_IIF_WARNING = "IIF_WARNING"

ISSUE_COLUMNS = [
    "issue_type",
    "severity",
    "sheet_name",
    "row_number",
    "field",
    "current_value",
    "message",
    "source_file",
    "page_num",
    "output_rows",
]

SOURCE_REVIEW_STATUSES = {
    "MULTI_TRANSACTION_SOURCE",
    "MISSING_TRANSACTION_SUSPECTED",
    "POSSIBLE_DUPLICATE",
    "NO_TRANSACTION_RETURNED",
}

ISSUE_RANK = {
    ISSUE_TYPE_IIF_WARNING: 1,
    ISSUE_TYPE_OCR_REVIEW: 2,
    ISSUE_TYPE_POSSIBLE_MISSING_RECEIPT: 3,
    ISSUE_TYPE_QB_IMPORT_ERROR: 4,
    ISSUE_TYPE_IIF_BLOCK: 5,
}

ISSUE_FILLS = {
    ISSUE_TYPE_IIF_BLOCK: PatternFill(start_color="FFCDD2", end_color="FFCDD2", fill_type="solid"),
    ISSUE_TYPE_QB_IMPORT_ERROR: PatternFill(start_color="FFCDD2", end_color="FFCDD2", fill_type="solid"),
    ISSUE_TYPE_POSSIBLE_MISSING_RECEIPT: PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid"),
    ISSUE_TYPE_OCR_REVIEW: PatternFill(start_color="FFF59D", end_color="FFF59D", fill_type="solid"),
    ISSUE_TYPE_IIF_WARNING: PatternFill(start_color="D9EAF7", end_color="D9EAF7", fill_type="solid"),
}

_HEADER_FILL = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
_WHITE_BOLD = Font(bold=True, color="FFFFFF")
_GRAY_BORDER = Border(
    left=Side(style="thin", color="D9D9D9"),
    right=Side(style="thin", color="D9D9D9"),
    top=Side(style="thin", color="D9D9D9"),
    bottom=Side(style="thin", color="D9D9D9"),
)


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _clean_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _is_true(value: object) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


def _source_issue_type(row: dict) -> str:
    status = _clean_text(row.get("_source_coverage_status"))
    if status in SOURCE_REVIEW_STATUSES:
        return ISSUE_TYPE_POSSIBLE_MISSING_RECEIPT
    if row.get("extract_failed"):
        return ISSUE_TYPE_OCR_REVIEW
    if _is_true(row.get("needs_review")):
        return ISSUE_TYPE_OCR_REVIEW
    return ""


def _source_issue_message(row: dict, issue_type: str) -> str:
    note = _clean_text(row.get("note"))
    if issue_type == ISSUE_TYPE_POSSIBLE_MISSING_RECEIPT:
        return note or "Source image may contain more receipt activity than the extracted rows show."
    if row.get("extract_failed"):
        return note or "OCR extraction failed; review this source manually."
    return note or "OCR marked this row for staff review."


def highest_issue_type(issue_types: list[str]) -> str:
    best = ""
    best_rank = 0
    for issue_type in issue_types:
        rank = ISSUE_RANK.get(issue_type, 0)
        if rank > best_rank:
            best = issue_type
            best_rank = rank
    return best


def fill_for_issue_type(issue_type: str):
    return ISSUE_FILLS.get(issue_type)


def issue_rows_from_receipt_rows(rows: list[dict], sheet_name: str) -> list[dict]:
    issues: list[dict] = []
    for offset, row in enumerate(rows, start=2):
        issue_type = _source_issue_type(row)
        if not issue_type:
            continue
        issues.append(
            {
                "issue_type": issue_type,
                "severity": "WARNING",
                "sheet_name": sheet_name,
                "row_number": offset,
                "field": "description" if issue_type == ISSUE_TYPE_POSSIBLE_MISSING_RECEIPT else "needs_review",
                "current_value": _clean_text(row.get("description")),
                "message": _source_issue_message(row, issue_type),
                "source_file": _clean_text(row.get("source_file")),
                "page_num": _clean_text(row.get("page_num")),
                "output_rows": _clean_text(row.get("row_num")),
            }
        )
    return issues


def issue_rows_from_source_audit(audit_rows: list[dict], sheet_name: str = "") -> list[dict]:
    issues: list[dict] = []
    for audit_row in audit_rows:
        status = _clean_text(audit_row.get("status"))
        if status not in SOURCE_REVIEW_STATUSES and status != "EXTRACTION_FAILED":
            continue
        issue_type = (
            ISSUE_TYPE_OCR_REVIEW
            if status == "EXTRACTION_FAILED"
            else ISSUE_TYPE_POSSIBLE_MISSING_RECEIPT
        )
        issues.append(
            {
                "issue_type": issue_type,
                "severity": "WARNING",
                "sheet_name": sheet_name,
                "row_number": "",
                "field": "source_file",
                "current_value": _clean_text(audit_row.get("source_file")),
                "message": _clean_text(audit_row.get("note")) or status,
                "source_file": _clean_text(audit_row.get("source_file")),
                "page_num": _clean_text(audit_row.get("page_num")),
                "output_rows": _clean_text(audit_row.get("output_rows")),
            }
        )
    return issues


def issue_row_from_validation_issue(issue) -> dict:
    if issue.severity == "BLOCK":
        issue_type = ISSUE_TYPE_IIF_BLOCK
    elif _clean_text(issue.field) == "needs_review":
        issue_type = ISSUE_TYPE_OCR_REVIEW
    else:
        issue_type = ISSUE_TYPE_IIF_WARNING
    return {
        "issue_type": issue_type,
        "severity": issue.severity,
        "sheet_name": issue.sheet_name,
        "row_number": issue.row_number or "",
        "field": issue.field,
        "current_value": issue.current_value,
        "message": issue.message,
        "source_file": "",
        "page_num": "",
        "output_rows": "",
    }


def issue_rows_from_validation_issues(issues: list) -> list[dict]:
    return [issue_row_from_validation_issue(issue) for issue in issues]


def _style_header(worksheet) -> None:
    for cell in worksheet[1]:
        cell.font = copy(_WHITE_BOLD)
        cell.fill = copy(_HEADER_FILL)
        cell.border = copy(_GRAY_BORDER)
        cell.alignment = Alignment(vertical="center")


def _style_issue_rows(worksheet, issue_count: int) -> None:
    for row_idx in range(2, issue_count + 2):
        issue_type = _clean_text(worksheet.cell(row=row_idx, column=1).value)
        fill = fill_for_issue_type(issue_type)
        for col_idx in range(1, len(ISSUE_COLUMNS) + 1):
            cell = worksheet.cell(row=row_idx, column=col_idx)
            cell.border = copy(_GRAY_BORDER)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            if fill is not None:
                cell.fill = copy(fill)


def _set_widths(worksheet) -> None:
    widths = {
        "issue_type": 26,
        "severity": 12,
        "sheet_name": 24,
        "row_number": 12,
        "field": 24,
        "current_value": 32,
        "message": 80,
        "source_file": 42,
        "page_num": 10,
        "output_rows": 16,
    }
    for col_idx, column_name in enumerate(ISSUE_COLUMNS, start=1):
        worksheet.column_dimensions[get_column_letter(col_idx)].width = widths[column_name]


def write_review_issues_sheet(workbook, issue_rows: list[dict], activate: bool = False):
    for sheet_name in list(workbook.sheetnames):
        if sheet_name.strip().lower() == REVIEW_SHEET_TITLE.lower():
            del workbook[sheet_name]

    if not issue_rows:
        return None

    worksheet = workbook.create_sheet(REVIEW_SHEET_TITLE, 0)
    worksheet.sheet_properties.tabColor = "F4B183"
    worksheet.append(ISSUE_COLUMNS)
    for issue_row in issue_rows:
        worksheet.append([issue_row.get(column, "") for column in ISSUE_COLUMNS])

    _style_header(worksheet)
    _style_issue_rows(worksheet, len(issue_rows))
    _set_widths(worksheet)

    max_row = max(1, len(issue_rows) + 1)
    max_col = len(ISSUE_COLUMNS)
    worksheet.freeze_panes = "A2"
    worksheet.auto_filter.ref = f"A1:{get_column_letter(max_col)}{max_row}"

    if activate:
        workbook.active = workbook.index(worksheet)
    return worksheet


def apply_issue_fills(workbook, issue_rows: list[dict]) -> None:
    issues_by_row: dict[tuple[str, int], list[str]] = {}
    for issue_row in issue_rows:
        row_number = _clean_int(issue_row.get("row_number"))
        sheet_name = _clean_text(issue_row.get("sheet_name"))
        if not sheet_name or row_number is None or row_number < 2:
            continue
        issues_by_row.setdefault((sheet_name, row_number), []).append(
            _clean_text(issue_row.get("issue_type"))
        )

    for (sheet_name, row_number), issue_types in issues_by_row.items():
        if sheet_name not in workbook.sheetnames:
            continue
        worksheet = workbook[sheet_name]
        fill = fill_for_issue_type(highest_issue_type(issue_types))
        if fill is None:
            continue
        for col_idx in range(1, worksheet.max_column + 1):
            worksheet.cell(row=row_number, column=col_idx).fill = copy(fill)


def should_activate_review_sheet(issue_rows: list[dict]) -> bool:
    for issue_row in issue_rows:
        issue_type = _clean_text(issue_row.get("issue_type"))
        if issue_type in {
            ISSUE_TYPE_IIF_BLOCK,
            ISSUE_TYPE_QB_IMPORT_ERROR,
            ISSUE_TYPE_POSSIBLE_MISSING_RECEIPT,
        }:
            return True
    return False
