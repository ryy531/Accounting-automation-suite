from dataclasses import dataclass
from pathlib import Path

from openpyxl import load_workbook


IIF_ENCODINGS = ("utf-8-sig", "cp1252", "mbcs")
EXCEL_ACCOUNT_EXTENSIONS = {".xlsx", ".xlsm"}
ACCOUNT_LIST_SHEET_NAME = "account_list"


@dataclass(frozen=True)
class Account:
    name: str
    account_type: str
    row: dict[str, str]


@dataclass(frozen=True)
class AccountList:
    accounts: dict[str, Account]

    def has_account(self, name: object) -> bool:
        text = _clean_text(name)
        return bool(text) and text in self.accounts

    def get(self, name: object) -> Account | None:
        return self.accounts.get(_clean_text(name))

    def bank_payment_account_names(self) -> list[str]:
        """Return only Bank-type payment account names (Type == Bank exactly), sorted."""
        names: list[str] = []
        for account in self.accounts.values():
            account_type = _clean_text(account.account_type)
            if account_type.lower() == "bank":
                names.append(account.name)
        return sorted(names, key=str.lower)

    def find_tax_account(self, tax_name: str) -> "TaxAccountLookup":
        """Find tax account by exact match or numbered-suffix match.

        Returns TaxAccountLookup with status: "found", "not_found", or "multiple_matches".
        """
        from .account_list import _clean_text  # noqa: F402

        tax_name_lower = _clean_text(tax_name).lower()
        candidates: list[Account] = []

        for account in self.accounts.values():
            name_lower = account.name.lower()
            # Exact match
            if name_lower == tax_name_lower:
                return TaxAccountLookup(account=account, lookup_status="found", candidates=[])
            # Match: "number · tax_name" pattern (e.g., "25500 · GST/HST Payable")
            if name_lower.endswith(f" · {tax_name_lower}"):
                candidates.append(account)

        if not candidates:
            return TaxAccountLookup(account=None, lookup_status="not_found", candidates=[])
        if len(candidates) == 1:
            return TaxAccountLookup(account=candidates[0], lookup_status="found", candidates=[])

        return TaxAccountLookup(
            account=None,
            lookup_status="multiple_matches",
            candidates=[c.name for c in candidates],
        )


@dataclass(frozen=True)
class TaxAccountLookup:
    """Result of tax account lookup with status and candidates."""

    account: Account | None
    lookup_status: str  # "found", "not_found", "multiple_matches"
    candidates: list[str]  # Account names when multiple matches


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


def _load_accounts_from_excel(excel_path: Path) -> AccountList:
    workbook = load_workbook(excel_path, read_only=True, data_only=True)
    accounts: dict[str, Account] = {}

    try:
        worksheets = list(workbook.worksheets)
        account_list_sheets = [
            worksheet
            for worksheet in worksheets
            if worksheet.title.strip().lower() == ACCOUNT_LIST_SHEET_NAME
        ]
        for worksheet in account_list_sheets + [
            worksheet for worksheet in worksheets if worksheet not in account_list_sheets
        ]:
            header_indexes: dict[str, int] | None = None
            account_index: int | None = None
            type_index: int | None = None

            for values in worksheet.iter_rows(values_only=True):
                if header_indexes is None:
                    indexes = _header_indexes(values)
                    if "account" not in indexes and "qb account" in indexes:
                        indexes["account"] = indexes["qb account"]
                    if "account" not in indexes:
                        continue
                    header_indexes = indexes
                    account_index = indexes["account"]
                    type_index = indexes.get("type")
                    continue

                if account_index is None:
                    continue

                name = _clean_text(values[account_index] if account_index < len(values) else "")
                if not name:
                    continue

                account_type = ""
                if type_index is not None and type_index < len(values):
                    account_type = _clean_text(values[type_index])

                accounts[name] = Account(
                    name=name,
                    account_type=account_type,
                    row={
                        "NAME": name,
                        "ACCNTTYPE": account_type,
                        "_loaded_encoding": "excel",
                        "_source_sheet": worksheet.title,
                    },
                )

            if accounts:
                break
    finally:
        workbook.close()

    return AccountList(accounts=accounts)


def load_accounts(iif_path: Path) -> AccountList:
    if iif_path.suffix.lower() in EXCEL_ACCOUNT_EXTENSIONS:
        return _load_accounts_from_excel(iif_path)

    headers: list[str] | None = None
    accounts: dict[str, Account] = {}
    lines, encoding = _read_iif_lines(iif_path)

    for raw_line in lines:
        values = _split_iif_line(raw_line)
        if not values:
            continue

        record_type = values[0]
        if record_type == "!ACCNT":
            headers = values[1:]
            continue

        if record_type != "ACCNT" or headers is None:
            continue

        row = _row_from_values(headers, values[1:])
        name = _clean_text(row.get("NAME"))
        if not name:
            continue

        row["_loaded_encoding"] = encoding
        accounts[name] = Account(
            name=name,
            account_type=_clean_text(row.get("ACCNTTYPE")),
            row=row,
        )

    return AccountList(accounts=accounts)
