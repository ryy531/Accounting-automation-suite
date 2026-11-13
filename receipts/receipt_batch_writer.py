import re
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import output_manager, review_issues, warnings_writer
from .config import EXCEL_COLUMNS, ROW_COLORS
from .excel_writer import COLUMN_WIDTHS


_ILLEGAL_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")
_MAX_SHEET_TITLE_LEN = 31
_OUTPUT_MARKER = "_Output_"
_WRAP_COLUMNS = {"description", "note"}
_HEADER_FILL = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
_GRAY_BORDER = Border(
    left=Side(style="thin", color="D9D9D9"),
    right=Side(style="thin", color="D9D9D9"),
    top=Side(style="thin", color="D9D9D9"),
    bottom=Side(style="thin", color="D9D9D9"),
)


def _warning(source_file: str, message: str, action_taken: str) -> dict:
    return {
        "level": "warning",
        "source_file": source_file,
        "message": message,
        "action_taken": action_taken,
    }


def _truncate_sheet_title(title: str, suffix: str = "") -> str:
    max_base_len = _MAX_SHEET_TITLE_LEN - len(suffix)
    return f"{title[:max_base_len]}{suffix}"


def _safe_sheet_title(
    raw_title: str,
    used_titles: set[str],
    warnings: list[dict],
    group_name: str,
) -> str:
    original = raw_title or ""
    title = original.strip() or "_blank"
    title = _ILLEGAL_SHEET_CHARS.sub("_", title).strip("'") or "_blank"

    if title != original:
        warnings.append(
            _warning(
                group_name,
                f"Excel-illegal sheet characters found: {original}",
                f"Used sheet name: {title}",
            )
        )

    if len(title) > _MAX_SHEET_TITLE_LEN:
        truncated = _truncate_sheet_title(title)
        warnings.append(
            _warning(
                group_name,
                f"Sheet name too long: {title}",
                f"Truncated to: {truncated}",
            )
        )
        title = truncated

    candidate = title
    counter = 2
    while candidate.lower() in used_titles:
        suffix = f"_{counter}"
        candidate = _truncate_sheet_title(title, suffix)
        counter += 1

    used_titles.add(candidate.lower())
    return candidate


def _style_header(ws) -> None:
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = _HEADER_FILL
        cell.border = _GRAY_BORDER
        cell.alignment = Alignment(vertical="center")


def _issue_types_by_row(issue_rows: list[dict]) -> dict[int, list[str]]:
    by_row: dict[int, list[str]] = {}
    for issue_row in issue_rows:
        row_number = issue_row.get("row_number")
        if not isinstance(row_number, int):
            continue
        by_row.setdefault(row_number, []).append(str(issue_row.get("issue_type") or ""))
    return by_row


def _style_rows(ws, rows: list[dict], issue_rows: list[dict]) -> None:
    max_col = len(EXCEL_COLUMNS)
    issue_types_by_row = _issue_types_by_row(issue_rows)
    for row_idx in range(2, len(rows) + 2):
        tx = rows[row_idx - 2]
        fill = None
        if tx.get("extract_failed"):
            fill = PatternFill(
                start_color=ROW_COLORS["failed"],
                end_color=ROW_COLORS["failed"],
                fill_type="solid",
            )
        elif row_idx in issue_types_by_row:
            fill = review_issues.fill_for_issue_type(
                review_issues.highest_issue_type(issue_types_by_row[row_idx])
            )
        elif tx.get("needs_review"):
            fill = PatternFill(
                start_color=ROW_COLORS["review"],
                end_color=ROW_COLORS["review"],
                fill_type="solid",
            )

        for col_idx, col_name in enumerate(EXCEL_COLUMNS, start=1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.border = _GRAY_BORDER
            cell.alignment = Alignment(vertical="top")
            if col_name in _WRAP_COLUMNS:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
            if fill is not None:
                cell.fill = fill

        for col_idx in range(max_col + 1, ws.max_column + 1):
            ws.cell(row=row_idx, column=col_idx).fill = fill


def _set_column_widths(ws, rows: list[dict]) -> None:
    for col_idx, col_name in enumerate(EXCEL_COLUMNS, start=1):
        cap = COLUMN_WIDTHS.get(col_name, 20)
        letter = get_column_letter(col_idx)
        max_len = len(str(col_name))
        for row in rows:
            value = row.get(col_name)
            if value is not None:
                max_len = max(max_len, len(str(value)))
        ws.column_dimensions[letter].width = min(max_len + 2, cap)


def _write_receipt_sheet(ws, rows: list[dict], sheet_warnings: list[dict]) -> list[dict]:
    ws.append(EXCEL_COLUMNS)
    for row in rows:
        ws.append([row.get(column) for column in EXCEL_COLUMNS])

    max_col = len(EXCEL_COLUMNS)
    data_max_row = max(1, len(rows) + 1)
    review_issue_rows = review_issues.issue_rows_from_receipt_rows(rows, ws.title)

    _style_header(ws)
    _style_rows(ws, rows, review_issue_rows)
    _set_column_widths(ws, rows)

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(max_col)}{data_max_row}"
    warnings_writer.append_bottom_warnings(ws, sheet_warnings, max_col)
    return review_issue_rows


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


def _root_name_from_output_dir(output_dir: Path) -> str:
    output_name = output_dir.name
    if _OUTPUT_MARKER in output_name:
        return output_name.split(_OUTPUT_MARKER, 1)[0]
    return output_name


def _workbook_base_name(root_name: str, group_name: str) -> str:
    if group_name == "_root":
        return f"{root_name}__root"
    if group_name.startswith(f"{root_name}."):
        return group_name
    if group_name.startswith(f"{root_name}_"):
        return group_name
    if group_name.startswith(f"{root_name}-"):
        return group_name
    return f"{root_name}__{group_name}"


def _safe_workbook_name(raw_name: str) -> tuple[str, list[dict]]:
    safe_name, warnings = output_manager._sanitize_name(raw_name, "Untitled")
    return f"{safe_name}.xlsx", warnings


def write_receipt_batch_workbooks(
    grouped_rows: dict[str, dict[str, list[dict]]],
    output_dir: Path,
    warnings_by_group: dict[str, list[dict]] | None = None,
    source_audit_by_group: dict[str, list[dict]] | None = None,
) -> dict[str, Path]:
    if not grouped_rows:
        return {}

    warnings_by_group = warnings_by_group or {}
    source_audit_by_group = source_audit_by_group or {}
    batch_dir = output_dir / "receipt_batches"
    batch_dir.mkdir(exist_ok=True)
    root_name = _root_name_from_output_dir(output_dir)

    written_paths = {}
    for group_name, sheet_rows in grouped_rows.items():
        group_warnings = list(warnings_by_group.get(group_name, []))
        workbook_base_name = _workbook_base_name(root_name, group_name)
        xlsx_name, name_warnings = _safe_workbook_name(workbook_base_name)
        for warning in name_warnings:
            warning["source_file"] = group_name
        group_warnings.extend(name_warnings)

        wb = Workbook()
        default_ws = wb.active
        wb.remove(default_ws)
        used_titles = set()
        workbook_review_rows: list[dict] = []

        for sheet_key in sorted(sheet_rows.keys(), key=str.lower):
            rows = sheet_rows[sheet_key]
            sheet_title = _safe_sheet_title(
                sheet_key,
                used_titles,
                group_warnings,
                group_name,
            )
            ws = wb.create_sheet(sheet_title)
            workbook_review_rows.extend(_write_receipt_sheet(ws, rows, group_warnings))

        warnings_writer.add_warnings_sheet(wb, group_warnings)
        review_issues.write_review_issues_sheet(
            wb,
            workbook_review_rows,
            activate=review_issues.should_activate_review_sheet(workbook_review_rows),
        )
        if not group_warnings and not workbook_review_rows:
            wb.active = 0

        excel_path = _unique_xlsx_path(batch_dir, xlsx_name)
        wb.save(excel_path)
        written_paths[group_name] = excel_path

    return written_paths
