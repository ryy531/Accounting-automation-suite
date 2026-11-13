from dataclasses import dataclass
from pathlib import Path

from openpyxl import load_workbook


IIF_ENCODINGS = ("utf-8-sig", "cp1252", "mbcs")
EXCEL_VENDOR_EXTENSIONS = {".xlsx", ".xlsm"}
VENDOR_LIST_SHEET_NAME = "vendor_list"


@dataclass(frozen=True)
class Vendor:
    name: str
    active_status: str
    row: dict[str, str]


@dataclass(frozen=True)
class VendorList:
    vendors: dict[str, Vendor]

    def has_vendor(self, name: object) -> bool:
        text = _clean_text(name)
        return bool(text) and text in self.vendors


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _split_iif_line(line: str) -> list[str]:
    return line.rstrip("\r\n").split("\t")


def _row_from_values(headers: list[str], values: list[str]) -> dict[str, str]:
    row = {}
    for index, header in enumerate(headers):
        row[header] = values[index] if index < len(values) else ""
    return row


def _read_iif_lines(iif_path: Path) -> tuple[list[str], str]:
    failures = []
    for encoding in IIF_ENCODINGS:
        try:
            return iif_path.read_text(encoding=encoding).splitlines(), encoding
        except UnicodeDecodeError as exc:
            failures.append(f"{encoding}: {exc}")

    attempted = ", ".join(IIF_ENCODINGS)
    details = "; ".join(failures)
    raise UnicodeDecodeError(
        "iif",
        b"",
        0,
        1,
        f"Could not decode {iif_path} with encodings: {attempted}. {details}",
    )


def _header_indexes(values: tuple[object, ...]) -> dict[str, int]:
    indexes = {}
    for index, value in enumerate(values):
        header = _clean_text(value).lower()
        if header:
            indexes[header] = index
    return indexes


def _load_vendors_from_excel(excel_path: Path) -> VendorList:
    workbook = load_workbook(excel_path, read_only=True, data_only=True)
    vendors: dict[str, Vendor] = {}

    try:
        worksheets = list(workbook.worksheets)
        vendor_list_sheets = [
            worksheet
            for worksheet in worksheets
            if worksheet.title.strip().lower() == VENDOR_LIST_SHEET_NAME
        ]
        for worksheet in vendor_list_sheets + [
            worksheet for worksheet in worksheets if worksheet not in vendor_list_sheets
        ]:
            vendor_index: int | None = None
            active_index: int | None = None

            for values in worksheet.iter_rows(values_only=True):
                if vendor_index is None:
                    indexes = _header_indexes(values)
                    if "vendor" not in indexes and "name" in indexes:
                        indexes["vendor"] = indexes["name"]
                    if "vendor" not in indexes:
                        continue
                    vendor_index = indexes["vendor"]
                    active_index = indexes.get("active status")
                    continue

                name = _clean_text(values[vendor_index] if vendor_index < len(values) else "")
                if not name:
                    continue

                active_status = ""
                if active_index is not None and active_index < len(values):
                    active_status = _clean_text(values[active_index])
                if active_status and active_status.lower() != "active":
                    continue

                vendors[name] = Vendor(
                    name=name,
                    active_status=active_status,
                    row={
                        "NAME": name,
                        "ACTIVE_STATUS": active_status,
                        "_loaded_encoding": "excel",
                        "_source_sheet": worksheet.title,
                    },
                )

            if vendors:
                break
    finally:
        workbook.close()

    return VendorList(vendors=vendors)


def load_vendors(vendor_path: Path) -> VendorList:
    if vendor_path.suffix.lower() in EXCEL_VENDOR_EXTENSIONS:
        return _load_vendors_from_excel(vendor_path)

    headers: list[str] | None = None
    vendors: dict[str, Vendor] = {}
    lines, encoding = _read_iif_lines(vendor_path)

    for raw_line in lines:
        values = _split_iif_line(raw_line)
        if not values:
            continue

        record_type = values[0]
        if record_type == "!VEND":
            headers = values[1:]
            continue

        if record_type != "VEND" or headers is None:
            continue

        row = _row_from_values(headers, values[1:])
        name = _clean_text(row.get("NAME"))
        if not name:
            continue

        active_status = _clean_text(row.get("ACTIVE_STATUS") or row.get("HIDDEN"))
        row["_loaded_encoding"] = encoding
        vendors[name] = Vendor(name=name, active_status=active_status, row=row)

    return VendorList(vendors=vendors)
