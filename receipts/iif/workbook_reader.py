from pathlib import Path

from openpyxl import load_workbook

from .schema import (
    BANK_REQUIRED_COLUMNS,
    GENERATED_SHEET_NAMES,
    QB_FIELDS,
    RECEIPT_REQUIRED_COLUMNS,
    WorkbookRow,
)


class WorkbookReadError(ValueError):
    pass


def _clean_header(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _sheet_is_generated(sheet_name: str) -> bool:
    return sheet_name.strip().lower() in GENERATED_SHEET_NAMES


def _required_columns(source_mode: str) -> set[str]:
    if source_mode == "receipt":
        return RECEIPT_REQUIRED_COLUMNS
    if source_mode == "bank":
        return BANK_REQUIRED_COLUMNS
    raise WorkbookReadError(f"Unsupported source mode: {source_mode}")


def _is_empty_row(values: dict[str, object]) -> bool:
    return all(value is None or str(value).strip() == "" for value in values.values())


def read_workbook_rows(workbook_path: Path, source_mode: str) -> tuple[list[WorkbookRow], list[str]]:
    workbook = load_workbook(workbook_path, data_only=False)
    required = _required_columns(source_mode)
    rows: list[WorkbookRow] = []
    workbook_warnings: list[str] = []

    for worksheet in workbook.worksheets:
        if _sheet_is_generated(worksheet.title):
            continue

        headers = [_clean_header(cell.value) for cell in worksheet[1]]
        header_set = {header for header in headers if header}
        if not header_set:
            continue

        if not required.issubset(header_set):
            missing = sorted(required - header_set)
            workbook_warnings.append(
                f"Skipped sheet '{worksheet.title}' because required columns are missing: {', '.join(missing)}"
            )
            continue

        if not set(QB_FIELDS).issubset(header_set):
            missing = sorted(set(QB_FIELDS) - header_set)
            workbook_warnings.append(
                f"Sheet '{worksheet.title}' is missing QB validation columns: {', '.join(missing)}"
            )

        for excel_row in range(2, worksheet.max_row + 1):
            values = {}
            for column_index, header in enumerate(headers, start=1):
                if header:
                    values[header] = worksheet.cell(row=excel_row, column=column_index).value

            if _is_empty_row(values):
                continue

            rows.append(
                WorkbookRow(
                    sheet_name=worksheet.title,
                    row_number=excel_row,
                    values=values,
                )
            )

    return rows, workbook_warnings

