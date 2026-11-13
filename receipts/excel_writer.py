from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from . import review_issues, warnings_writer
from .config import EXCEL_COLUMNS, ROW_COLORS

COLUMN_WIDTHS = {
    "date": 12,
    "description": 45,
    "subtotal": 12,
    "gst": 12,
    "pst": 12,
    "total": 12,
    "payment_card_last_four": 20,
    "has_line_items": 15,
    "doc_type": 18,
    "note": 50,
    "source_file": 35,
    "page_num": 10,
    "needs_review": 14,
    "thumbnail_path": 45,
}

_WRAP_COLUMNS = {"description", "note"}
_GRAY_BORDER = Border(
    left=Side(style="thin", color="D9D9D9"),
    right=Side(style="thin", color="D9D9D9"),
    top=Side(style="thin", color="D9D9D9"),
    bottom=Side(style="thin", color="D9D9D9"),
)


def _issue_types_by_row(issue_rows: list[dict]) -> dict[int, list[str]]:
    by_row: dict[int, list[str]] = {}
    for issue_row in issue_rows:
        row_number = issue_row.get("row_number")
        if not isinstance(row_number, int):
            continue
        by_row.setdefault(row_number, []).append(str(issue_row.get("issue_type") or ""))
    return by_row


def write_excel(
    transactions : list[dict],
    output_path : Path,
    warnings: list[dict] | None = None,
    source_audit_rows: list[dict] | None = None,
) -> Path:
    warnings = warnings or []
    wb = Workbook()
    ws = wb.active
    ws.title = "Receipts"

    # Header
    ws.append(EXCEL_COLUMNS)
    HEADER_FILL = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = HEADER_FILL
        cell.border = _GRAY_BORDER
        cell.alignment = Alignment(vertical="center")

    # Data rows
    for tx in transactions:
        row = [tx.get(col) for col in EXCEL_COLUMNS]
        ws.append(row)

    max_row = ws.max_row
    max_col = len(EXCEL_COLUMNS)
    review_issue_rows = review_issues.issue_rows_from_receipt_rows(transactions, ws.title)
    issue_types_by_row = _issue_types_by_row(review_issue_rows)

    for row_idx in range(2, max_row + 1):
        tx = transactions[row_idx - 2]
        for col_idx in range(1, max_col + 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.border = _GRAY_BORDER
            cell.alignment = Alignment(vertical="top")
            col_name = EXCEL_COLUMNS[col_idx - 1]
            if col_name in _WRAP_COLUMNS:
                cell.alignment = Alignment(wrap_text=True, vertical="top")

        # Row coloring
        if tx.get("extract_failed"):
            fill = PatternFill(
                start_color=ROW_COLORS["failed"], end_color=ROW_COLORS["failed"], fill_type="solid"
            )
            for col_idx in range(1, max_col + 1):
                ws.cell(row=row_idx, column=col_idx).fill = fill
        elif row_idx in issue_types_by_row:
            fill = review_issues.fill_for_issue_type(
                review_issues.highest_issue_type(issue_types_by_row[row_idx])
            )
            if fill is not None:
                for col_idx in range(1, max_col + 1):
                    ws.cell(row=row_idx, column=col_idx).fill = fill
        elif tx.get("needs_review"):
            fill = PatternFill(
                start_color=ROW_COLORS["review"], end_color=ROW_COLORS["review"], fill_type="solid"
            )
            for col_idx in range(1, max_col + 1):
                ws.cell(row=row_idx, column=col_idx).fill = fill

    # Column widths
    for col_idx, col_name in enumerate(EXCEL_COLUMNS, start=1):
        cap = COLUMN_WIDTHS.get(col_name, 20)
        letter = get_column_letter(col_idx)
        max_len = len(str(col_name))
        for row_idx in range(2, max_row + 1):
            val = ws.cell(row=row_idx, column=col_idx).value
            if val is not None:
                max_len = max(max_len, len(str(val)))
        ws.column_dimensions[letter].width = min(max_len + 2, cap)

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(max_col)}{max_row}"

    warnings_writer.append_bottom_warnings(ws, warnings, max_col)
    warnings_writer.add_warnings_sheet(wb, warnings)
    review_issues.write_review_issues_sheet(
        wb,
        review_issue_rows,
        activate=review_issues.should_activate_review_sheet(review_issue_rows),
    )
    if not warnings and not review_issue_rows:
        wb.active = wb.index(ws)

    wb.save(output_path)
    return output_path
