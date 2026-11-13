from dataclasses import dataclass


QB_FIELD_INCLUDE = "Include in IIF"
QB_FIELD_TRANSACTION_TYPE = "QB Transaction Type"
QB_FIELD_PAYEE = "QB Payee"
QB_FIELD_ACCOUNT = "QB Account"
QB_FIELD_PAYMENT_ACCOUNT = "Payment Account"
QB_FIELD_MEMO = "QB Memo"
QB_FIELD_REF_NO = "Ref No."
QB_FIELD_READY = "IIF Ready"
QB_FIELD_NOTE = "IIF Note"

QB_FIELDS = [
    QB_FIELD_INCLUDE,
    QB_FIELD_TRANSACTION_TYPE,
    QB_FIELD_ACCOUNT,
    QB_FIELD_PAYMENT_ACCOUNT,
    QB_FIELD_MEMO,
    QB_FIELD_REF_NO,
    QB_FIELD_READY,
    QB_FIELD_NOTE,
]

SUPPORTED_TRANSACTION_TYPES = {
    "cheque": "Cheque",
    "credit card charge": "Credit Card Charge",
    "deposit": "Deposit",
}

GENERATED_SHEET_NAMES = {
    "account_list",
    "iif_validation_report",
    "iif_export",
    "review_issues",
    "warnings",
    "batch summary",
    "vendor_list",
    "source_audit",
}

RECEIPT_REQUIRED_COLUMNS = {
    "date",
    "description",
    "total",
    "needs_review",
}

BANK_REQUIRED_COLUMNS = {
    "date",
    "description",
    "amount",
    "needs_review",
}

VALIDATION_SHEET_NAME = "IIF_Validation_Report"
REVIEW_ISSUES_SHEET_NAME = "Review_Issues"


@dataclass(frozen=True)
class WorkbookRow:
    sheet_name: str
    row_number: int
    values: dict[str, object]


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    sheet_name: str
    row_number: int
    field: str
    message: str
    current_value: str = ""
