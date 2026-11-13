from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .. import output_manager, warnings_writer
from .grouping import UNDATED_SHEET_TITLE, group_by_source_and_month, ordered_sheet_names


BANK_STATEMENT_COLUMNS = [
    "date",
    "posting_date",
    "description",
    "withdrawal",
    "deposit",
    "charge",
    "payment_credit",
    "refund",
    "transfer_returned",
    "amount",
    "balance",
    "account_last_four",
    "card_last_four",
    "source_file",
    "page_num",
    "row_num",
    "needs_review",
    "note",
    "thumbnail_path",
]

COLUMN_WIDTHS = {
    "date": 12,
    "posting_date": 14,
    "description": 48,
    "withdrawal": 12,
    "deposit": 12,
    "charge": 12,
    "payment_credit": 16,
    "refund": 12,
    "transfer_returned": 18,
    "amount": 12,
    "balance": 12,
    "account_last_four": 18,
    "card_last_four": 16,
    "source_file": 35,
    "page_num": 10,
    "row_num": 10,
    "needs_review": 14,
    "note": 55,
    "thumbnail_path": 45,
}
MONEY_COLUMNS = {
    "withdrawal",
    "deposit",
    "charge",
    "payment_credit",
    "refund",
    "transfer_returned",
    "amount",
    "balance",
}
WRAP_COLUMNS = {"description", "note"}

_HEADER_FILL = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
_REVIEW_FILL = PatternFill(start_color="FFF59D", end_color="FFF59D", fill_type="solid")
_FAILED_FILL = PatternFill(start_color="FFCDD2", end_color="FFCDD2", fill_type="solid")
_GRAY_BORDER = Border(
    left=Side(style="thin", color="D9D9D9"),
    right=Side(style="thin", color="D9D9D9"),
    top=Side(style="thin", color="D9D9D9"),
    bottom=Side(style="thin", color="D9D9D9"),
)


def _warnings_for_source(warnings: list[dict], source_file: str) -> list[dict]:
    return [
        warning for warning in warnings
        if not warning.get("source_file") or warning.get("source_file") == source_file
    ]


def _unique_xlsx_path(folder: Path, xlsx_name: str) -> Path:
    path = folder / xlsx_name
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    counter = 2
    while True:
        candidate = folder / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _style_header(ws) -> None:
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = _HEADER_FILL
        cell.border = _GRAY_BORDER
        cell.alignment = Alignment(vertical="center")


def _style_data(ws, rows: list[dict]) -> None:
    max_col = len(BANK_STATEMENT_COLUMNS)
    for row_idx in range(2, len(rows) + 2):
        row = rows[row_idx - 2]
        fill = None
        if row.get("extract_failed"):
            fill = _FAILED_FILL
        elif row.get("needs_review"):
            fill = _REVIEW_FILL

        for col_idx, col_name in enumerate(BANK_STATEMENT_COLUMNS, start=1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.border = _GRAY_BORDER
            cell.alignment = Alignment(vertical="top")
            if col_name in WRAP_COLUMNS:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
            if col_name in MONEY_COLUMNS:
                cell.number_format = '#,##0.00'
            if fill is not None:
                cell.fill = fill

        for col_idx in range(max_col + 1, ws.max_column + 1):
            ws.cell(row=row_idx, column=col_idx).fill = fill


def _set_column_widths(ws, rows: list[dict]) -> None:
    for col_idx, col_name in enumerate(BANK_STATEMENT_COLUMNS, start=1):
        cap = COLUMN_WIDTHS.get(col_name, 20)
        letter = get_column_letter(col_idx)
        max_len = len(col_name)
        for row in rows:
            value = row.get(col_name)
            if value is not None:
                max_len = max(max_len, len(str(value)))
        ws.column_dimensions[letter].width = min(max_len + 2, cap)


def _write_data_sheet(ws, rows: list[dict], sheet_warnings: list[dict]) -> None:
    ws.append(BANK_STATEMENT_COLUMNS)
    for row in rows:
        ws.append([row.get(column) for column in BANK_STATEMENT_COLUMNS])

    max_col = len(BANK_STATEMENT_COLUMNS)
    data_max_row = max(1, len(rows) + 1)

    _style_header(ws)
    _style_data(ws, rows)
    _set_column_widths(ws, rows)

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(max_col)}{data_max_row}"

    if ws.title == UNDATED_SHEET_TITLE:
        ws.sheet_properties.tabColor = "C00000"

    warnings_writer.append_bottom_warnings(ws, sheet_warnings, max_col)


def write_bank_statement_workbooks(
    rows: list[dict],
    output_dir: Path,
    warnings: list[dict] | None = None,
) -> list[Path]:
    if not rows:
        return []

    warnings = warnings or []
    bank_dir = output_dir / "bank_statements"
    bank_dir.mkdir(exist_ok=True)

    paths = []
    source_groups = group_by_source_and_month(rows)
    for source_file, month_groups in source_groups.items():
        source_warnings = _warnings_for_source(warnings, source_file)
        xlsx_name, name_warnings = output_manager.make_safe_xlsx_name(source_file)
        for warning in name_warnings:
            warning["source_file"] = source_file
        source_warnings.extend(name_warnings)

        wb = Workbook()
        default_ws = wb.active
        wb.remove(default_ws)

        for sheet_name in ordered_sheet_names(month_groups):
            ws = wb.create_sheet(sheet_name)
            _write_data_sheet(ws, month_groups[sheet_name], source_warnings)

        warnings_writer.add_warnings_sheet(wb, source_warnings)
        if not source_warnings:
            wb.active = 0

        excel_path = _unique_xlsx_path(bank_dir, xlsx_name)
        wb.save(excel_path)
        paths.append(excel_path)

    return paths
