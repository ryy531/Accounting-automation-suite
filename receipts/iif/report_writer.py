import shutil
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .. import review_issues
from .account_list import AccountList
from .schema import VALIDATION_SHEET_NAME, ValidationIssue
from .vendor_list import VendorList


REPORT_COLUMNS = [
    "severity",
    "sheet_name",
    "row_number",
    "field",
    "current_value",
    "message",
]

_HEADER_FILL = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
_BLOCK_FILL = PatternFill(start_color="FFCDD2", end_color="FFCDD2", fill_type="solid")
_WARNING_FILL = PatternFill(start_color="FFF59D", end_color="FFF59D", fill_type="solid")
_BORDER = Border(
    left=Side(style="thin", color="D9D9D9"),
    right=Side(style="thin", color="D9D9D9"),
    top=Side(style="thin", color="D9D9D9"),
    bottom=Side(style="thin", color="D9D9D9"),
)


def _validation_copy_path(workbook_path: Path, now: datetime | None = None) -> Path:
    timestamp = (now or datetime.now()).strftime("%Y%m%d_%H%M")
    return workbook_path.with_name(f"{workbook_path.stem}_iif_validation_{timestamp}{workbook_path.suffix}")


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path

    counter = 2
    while True:
        candidate = path.with_name(f"{path.stem}_{counter}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def _style_report(ws, issue_count: int) -> None:
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = _HEADER_FILL
        cell.border = _BORDER
        cell.alignment = Alignment(vertical="center")

    for row_index in range(2, issue_count + 2):
        severity = ws.cell(row=row_index, column=1).value
        fill = _BLOCK_FILL if severity == "BLOCK" else _WARNING_FILL
        for column_index in range(1, len(REPORT_COLUMNS) + 1):
            cell = ws.cell(row=row_index, column=column_index)
            cell.border = _BORDER
            cell.fill = fill
            cell.alignment = Alignment(wrap_text=True, vertical="top")

    widths = {
        "severity": 12,
        "sheet_name": 24,
        "row_number": 12,
        "field": 24,
        "current_value": 32,
        "message": 80,
    }
    for column_index, column_name in enumerate(REPORT_COLUMNS, start=1):
        ws.column_dimensions[get_column_letter(column_index)].width = widths[column_name]

    max_row = max(1, issue_count + 1)
    max_col = len(REPORT_COLUMNS)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(max_col)}{max_row}"


def write_validation_copy(
    workbook_path: Path,
    issues: list[ValidationIssue],
    output_path: Path | None = None,
) -> Path:
    target_path = output_path or _unique_path(_validation_copy_path(workbook_path))
    shutil.copy2(workbook_path, target_path)

    workbook = load_workbook(target_path)
    if VALIDATION_SHEET_NAME in workbook.sheetnames:
        del workbook[VALIDATION_SHEET_NAME]

    worksheet = workbook.create_sheet(VALIDATION_SHEET_NAME)
    worksheet.append(REPORT_COLUMNS)
    for issue in issues:
        worksheet.append(
            [
                issue.severity,
                issue.sheet_name,
                issue.row_number or "",
                issue.field,
                issue.current_value,
                issue.message,
            ]
        )

    _style_report(worksheet, len(issues))
    workbook.active = workbook.index(worksheet)
    workbook.save(target_path)
    return target_path


def write_review_issues_to_workbook(
    workbook_path: Path,
    issues: list[ValidationIssue],
    accounts: AccountList | None = None,
    vendors: VendorList | None = None,
) -> Path:
    workbook = load_workbook(workbook_path)
    if VALIDATION_SHEET_NAME in workbook.sheetnames:
        del workbook[VALIDATION_SHEET_NAME]

    issue_rows = review_issues.issue_rows_from_validation_issues(issues)
    review_issues.write_review_issues_sheet(
        workbook,
        issue_rows,
        activate=review_issues.should_activate_review_sheet(issue_rows),
    )
    review_issues.apply_issue_fills(workbook, issue_rows)

    if accounts is not None:
        from .account_prefill import refresh_qb_review_controls
        refresh_qb_review_controls(workbook, accounts, vendors)

    workbook.save(workbook_path)
    return workbook_path
