from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


SUMMARY_COLUMNS = [
    "relative_group",
    "workbook_path",
    "sheet_count",
    "source_file_count",
    "transaction_rows",
    "ok_count",
    "review_count",
    "failed_count",
    "warning_count",
    "notes",
]

COLUMN_WIDTHS = {
    "relative_group": 28,
    "workbook_path": 70,
    "sheet_count": 14,
    "source_file_count": 18,
    "transaction_rows": 18,
    "ok_count": 12,
    "review_count": 14,
    "failed_count": 14,
    "warning_count": 15,
    "notes": 55,
}

_HEADER_FILL = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
_REVIEW_FILL = PatternFill(start_color="FFF59D", end_color="FFF59D", fill_type="solid")
_FAILED_FILL = PatternFill(start_color="FFCDD2", end_color="FFCDD2", fill_type="solid")
_GRAY_BORDER = Border(
    left=Side(style="thin", color="D9D9D9"),
    right=Side(style="thin", color="D9D9D9"),
    top=Side(style="thin", color="D9D9D9"),
    bottom=Side(style="thin", color="D9D9D9"),
)


def _style_header(ws) -> None:
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = _HEADER_FILL
        cell.border = _GRAY_BORDER
        cell.alignment = Alignment(vertical="center")


def _style_rows(ws, rows: list[dict]) -> None:
    max_col = len(SUMMARY_COLUMNS)
    for row_idx in range(2, len(rows) + 2):
        row = rows[row_idx - 2]
        fill = None
        if row.get("failed_count", 0):
            fill = _FAILED_FILL
        elif row.get("review_count", 0) or row.get("warning_count", 0):
            fill = _REVIEW_FILL

        for col_idx in range(1, max_col + 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.border = _GRAY_BORDER
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            if fill is not None:
                cell.fill = fill


def _set_column_widths(ws) -> None:
    for col_idx, col_name in enumerate(SUMMARY_COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(col_idx)].width = COLUMN_WIDTHS[col_name]


def write_batch_summary(summary_rows: list[dict], output_dir: Path) -> Path:
    output_path = output_dir / "batch_summary.xlsx"

    wb = Workbook()
    ws = wb.active
    ws.title = "Batch Summary"
    ws.append(SUMMARY_COLUMNS)

    for row in summary_rows:
        ws.append([row.get(column) for column in SUMMARY_COLUMNS])

    max_row = max(1, len(summary_rows) + 1)
    max_col = len(SUMMARY_COLUMNS)

    _style_header(ws)
    _style_rows(ws, summary_rows)
    _set_column_widths(ws)

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(max_col)}{max_row}"

    wb.save(output_path)
    return output_path
