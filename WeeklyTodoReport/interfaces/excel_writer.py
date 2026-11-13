"""
Builds the two-sheet weekly report workbook:
  Sheet1 "聊天记录代办"   — AI-extracted ad-hoc items from WhatsApp chat (may be empty)
  Sheet2 "NocoDB代办明细" — deterministic NocoDB review/blocked/P0 rows

Both sheets get a checkbox-style "完成" column (data-validation dropdown ☐/☑).
"""
import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation


def _build_sheet(ws, headers, rows):
    ws.append(headers)
    header_fill = PatternFill(start_color="305496", end_color="305496", fill_type="solid")
    header_font = Font(bold=True, color="FFFFFF")
    thin = Side(style="thin", color="B0B0B0")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    ncols = len(headers)

    for col_idx in range(1, ncols + 1):
        c = ws.cell(row=1, column=col_idx)
        c.fill = header_fill
        c.font = header_font
        c.alignment = Alignment(horizontal="center", vertical="center")

    for i, row_data in enumerate(rows, start=1):
        r = i + 1
        ws.cell(row=r, column=1, value=i)
        for j, val in enumerate(row_data, start=2):
            ws.cell(row=r, column=j, value=val)
        ws.cell(row=r, column=ncols, value="☐")
        for col in range(1, ncols + 1):
            cell = ws.cell(row=r, column=col)
            cell.border = border
            cell.alignment = Alignment(wrap_text=True, vertical="top")

    if rows:
        dv = DataValidation(type="list", formula1='"☐,☑"', allow_blank=True)
        dv.error = "请选择 ☐ 或 ☑"
        dv.errorTitle = "无效输入"
        ws.add_data_validation(dv)
        dv.add(f"{get_column_letter(ncols)}2:{get_column_letter(ncols)}{len(rows) + 1}")

        for r in range(2, len(rows) + 2):
            cb = ws.cell(row=r, column=ncols)
            cb.alignment = Alignment(horizontal="center", vertical="center")
            cb.font = Font(size=14)

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(ncols)}{max(len(rows) + 1, 1)}"


def build_workbook(*, chat_rows: list[tuple], noco_rows: list[tuple]) -> openpyxl.Workbook:
    wb = openpyxl.Workbook()

    ws1 = wb.active
    ws1.title = "聊天记录代办"
    _build_sheet(ws1, ["序号", "日期", "类别", "事项", "备注", "完成"], chat_rows)
    widths1 = {1: 6, 2: 10, 3: 14, 4: 50, 5: 50, 6: 8}
    for col, w in widths1.items():
        ws1.column_dimensions[get_column_letter(col)].width = w

    ws2 = wb.create_sheet("NocoDB代办明细")
    _build_sheet(
        ws2,
        ["序号", "类别", "公司", "类型", "到期日", "当前状态", "说明", "完成"],
        noco_rows,
    )
    widths2 = {1: 6, 2: 14, 3: 32, 4: 8, 5: 12, 6: 24, 7: 40, 8: 8}
    for col, w in widths2.items():
        ws2.column_dimensions[get_column_letter(col)].width = w

    return wb
