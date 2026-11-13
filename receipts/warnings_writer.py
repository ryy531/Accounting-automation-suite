from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


WARNING_SHEET_TITLE = "⚠ Warnings"
WARNING_COLUMNS = ["level", "source_file", "message", "action_taken"]

_HEADER_FILL = PatternFill(start_color="C00000", end_color="C00000", fill_type="solid")
_WARNING_FILL = PatternFill(start_color="FFCDD2", end_color="FFCDD2", fill_type="solid")
_BOTTOM_FILL = PatternFill(start_color="C00000", end_color="C00000", fill_type="solid")
_WHITE_BOLD = Font(bold=True, color="FFFFFF")
_GRAY_BORDER = Border(
    left=Side(style="thin", color="D9D9D9"),
    right=Side(style="thin", color="D9D9D9"),
    top=Side(style="thin", color="D9D9D9"),
    bottom=Side(style="thin", color="D9D9D9"),
)


def _warning_text(warning: dict) -> str:
    message = warning.get("message") or ""
    action = warning.get("action_taken") or ""
    if action:
        return f"WARNING: {message} -> {action}"
    return f"WARNING: {message}"


def add_warnings_sheet(workbook, warnings: list[dict]):
    if not warnings:
        return None

    if WARNING_SHEET_TITLE in workbook.sheetnames:
        del workbook[WARNING_SHEET_TITLE]

    ws = workbook.create_sheet(WARNING_SHEET_TITLE, 0)
    ws.sheet_properties.tabColor = "C00000"
    ws.append(WARNING_COLUMNS)

    for cell in ws[1]:
        cell.font = _WHITE_BOLD
        cell.fill = _HEADER_FILL
        cell.border = _GRAY_BORDER
        cell.alignment = Alignment(vertical="center")

    for warning in warnings:
        ws.append([warning.get(col, "") for col in WARNING_COLUMNS])

    max_row = ws.max_row
    max_col = len(WARNING_COLUMNS)
    for row_idx in range(2, max_row + 1):
        for col_idx in range(1, max_col + 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.border = _GRAY_BORDER
            cell.fill = _WARNING_FILL
            cell.alignment = Alignment(wrap_text=True, vertical="top")

    widths = {
        "level": 12,
        "source_file": 35,
        "message": 60,
        "action_taken": 70,
    }
    for col_idx, col_name in enumerate(WARNING_COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(col_idx)].width = widths[col_name]

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(max_col)}{max_row}"
    workbook.active = workbook.index(ws)
    return ws


def append_bottom_warnings(worksheet, warnings: list[dict], max_col: int) -> None:
    if not warnings:
        return

    row_idx = worksheet.max_row + 2
    for warning in warnings:
        cell = worksheet.cell(row=row_idx, column=1)
        cell.value = _warning_text(warning)
        cell.font = _WHITE_BOLD
        cell.fill = _BOTTOM_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        cell.border = _GRAY_BORDER

        if max_col > 1:
            worksheet.merge_cells(
                start_row=row_idx,
                start_column=1,
                end_row=row_idx,
                end_column=max_col,
            )
            for col_idx in range(2, max_col + 1):
                merged_cell = worksheet.cell(row=row_idx, column=col_idx)
                merged_cell.fill = _BOTTOM_FILL
                merged_cell.border = _GRAY_BORDER

        row_idx += 1
