import argparse
import sys
from pathlib import Path

from .account_config import AccountSettingsError, load_saved_account_path, save_account_settings
from .account_list import load_accounts
from .account_prefill import prefill_receipt_accounts
from .iif_writer import write_receipt_iif
from .report_writer import write_review_issues_to_workbook, write_validation_copy
from .schema import ValidationIssue
from .validator import summarize_issues, validate_rows
from .vendor_list import load_vendors
from .workbook_reader import read_workbook_rows


DEFAULT_ACCOUNT_LIST = Path(r"C:\Users\Saskatoon007\Desktop\chart_of_accounts.IIF")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m receipts.iif.cli",
        description="Validate reviewed workbooks for future QuickBooks Desktop IIF export.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="Validate a reviewed workbook")
    validate.add_argument("--workbook", required=True, help="Reviewed workbook path")
    validate.add_argument(
        "--source-mode",
        required=True,
        choices=("receipt", "bank"),
        help="Workbook source mode selected by the user",
    )
    validate.add_argument(
        "--accounts",
        default=str(DEFAULT_ACCOUNT_LIST),
        help="QuickBooks Desktop Chart of Accounts IIF export",
    )
    validate.add_argument(
        "--output",
        default=None,
        help="Optional explicit validation workbook copy path",
    )

    generate = subparsers.add_parser("generate", help="Generate a receipt-source IIF file")
    generate.add_argument("--workbook", required=True, help="Reviewed workbook path")
    generate.add_argument(
        "--source-mode",
        required=True,
        choices=("receipt", "bank"),
        help="Workbook source mode selected by the user",
    )
    generate.add_argument(
        "--accounts",
        default=None,
        help="QuickBooks Desktop Chart of Accounts IIF export",
    )
    generate.add_argument(
        "--vendors",
        default=None,
        help="QuickBooks Desktop Vendor List Excel or IIF export",
    )
    generate.add_argument(
        "--use-saved-accounts",
        action="store_true",
        help="Use the saved account list path from .iif_settings.json",
    )
    generate.add_argument(
        "--output-dir",
        required=True,
        help="Folder where export.iif and export_summary.txt will be written",
    )
    generate.add_argument(
        "--allow-review",
        action="store_true",
        help="Allow rows with needs_review=True to export for test-company import",
    )

    prefill = subparsers.add_parser(
        "prefill-accounts",
        help="Create a reviewed-workbook copy with QB account suggestions for receipt rows",
    )
    prefill.add_argument("--workbook", required=True, help="Source workbook path")
    prefill.add_argument(
        "--source-mode",
        required=True,
        choices=("receipt", "bank"),
        help="Workbook source mode selected by the user",
    )
    prefill.add_argument(
        "--output-workbook",
        required=True,
        help="Output workbook copy path",
    )
    prefill.add_argument(
        "--accounts",
        default=None,
        help="QuickBooks Desktop Chart of Accounts IIF export",
    )
    prefill.add_argument(
        "--vendors",
        default=None,
        help="QuickBooks Desktop Vendor List Excel or IIF export for QB Payee dropdown",
    )
    prefill.add_argument(
        "--use-saved-accounts",
        action="store_true",
        help="Use the saved account list path from .iif_settings.json",
    )
    prefill.add_argument(
        "--default-payment-account",
        default="",
        help="Optional payment account to fill into blank Payment Account cells",
    )
    return parser.parse_args(argv)


def _validate(args: argparse.Namespace) -> int:
    workbook_path = Path(args.workbook)
    account_path = Path(args.accounts)
    output_path = Path(args.output) if args.output else None

    accounts = load_accounts(account_path)
    rows, workbook_warnings = read_workbook_rows(workbook_path, args.source_mode)
    issues = validate_rows(
        rows,
        accounts,
        source_mode=args.source_mode,
        workbook_warnings=workbook_warnings,
    )
    summary = summarize_issues(issues)
    validation_copy = write_validation_copy(workbook_path, issues, output_path)

    print(f"Workbook rows checked: {len(rows)}")
    print(f"Validation blocks: {summary['blocks']}")
    print(f"Blocked rows: {summary['blocked_rows']}")
    print(f"Validation warnings: {summary['warnings']}")
    print(f"Validation copy: {validation_copy}")
    print("Original workbook unchanged.")
    return 0


def _write_export_summary(
    output_path: Path,
    workbook_path: Path,
    source_mode: str,
    row_count: int,
    validation_summary: dict[str, int],
    export_result,
    workbook_warnings: list[str],
) -> None:
    lines = [
        "QuickBooks Desktop IIF Export Summary",
        f"Workbook: {workbook_path}",
        f"Source mode: {source_mode}",
        f"Rows checked: {row_count}",
        f"Exported rows: {export_result.exported_rows}",
        f"Blocked rows: {export_result.blocked_rows}",
        f"Validation blocks: {validation_summary['blocks']}",
        f"Validation warnings: {validation_summary['warnings']}",
        f"IIF file: {export_result.output_path}",
        "",
    ]

    if workbook_warnings:
        lines.append("Workbook warnings:")
        lines.extend(f"- {warning}" for warning in workbook_warnings)
        lines.append("")

    if export_result.blocks:
        lines.append("Blocked row examples:")
        for block in export_result.blocks[:25]:
            lines.append(f"- {block.sheet_name}!{block.row_number}: {block.reason}")
        if len(export_result.blocks) > 25:
            lines.append(f"- ... {len(export_result.blocks) - 25} more block messages")
        lines.append("")

    if export_result.warnings:
        lines.append("Export warnings:")
        lines.extend(f"- {warning}" for warning in export_result.warnings)
        lines.append("")

    if export_result.new_vendors:
        lines.append("New vendors to be created in QuickBooks from reviewed QB Payee values:")
        lines.extend(f"- {vendor}" for vendor in export_result.new_vendors)
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def _write_blocked_export_summary(
    output_path: Path,
    workbook_path: Path,
    source_mode: str,
    row_count: int,
    validation_summary: dict[str, int],
    workbook_warnings: list[str],
    review_workbook: Path,
) -> None:
    lines = [
        "QuickBooks Desktop IIF Export Summary",
        f"Workbook: {workbook_path}",
        f"Source mode: {source_mode}",
        f"Rows checked: {row_count}",
        "Exported rows: 0",
        f"Blocked rows: {validation_summary['blocked_rows']}",
        f"Validation blocks: {validation_summary['blocks']}",
        f"Validation warnings: {validation_summary['warnings']}",
        f"Review workbook: {review_workbook}",
        "",
        "IIF file was not generated because one or more rows are blocked.",
        "Open the Review_Issues sheet, fix the listed fields in the reviewed workbook, then generate IIF again.",
        "",
    ]

    if workbook_warnings:
        lines.append("Workbook warnings:")
        lines.extend(f"- {warning}" for warning in workbook_warnings)
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def _load_accounts_for_company(args: argparse.Namespace):
    if args.accounts:
        account_path = Path(args.accounts)
        accounts = load_accounts(account_path)
        settings_path = save_account_settings(account_path, accounts)
        print(f"Saved Chart of Accounts path: {settings_path}")
        return account_path, accounts

    if not args.use_saved_accounts:
        raise AccountSettingsError(
            "Please provide this company's QuickBooks account list using --accounts."
        )

    account_path = load_saved_account_path()
    print(f"Using saved Chart of Accounts path: {account_path}")
    return account_path, load_accounts(account_path)


def _generate(args: argparse.Namespace) -> int:
    if args.source_mode == "bank":
        print("Bank IIF generation is not implemented yet.")
        return 2

    workbook_path = Path(args.workbook)
    output_dir = Path(args.output_dir)

    try:
        _account_path, accounts = _load_accounts_for_company(args)
    except AccountSettingsError as exc:
        print(exc)
        return 2
    vendors = load_vendors(Path(args.vendors)) if args.vendors else None

    rows, workbook_warnings = read_workbook_rows(workbook_path, args.source_mode)
    issues = validate_rows(
        rows,
        accounts,
        source_mode=args.source_mode,
        workbook_warnings=workbook_warnings,
    )
    validation_summary = summarize_issues(issues)
    preblocked_rows = {
        (issue.sheet_name, issue.row_number)
        for issue in issues
        if issue.severity == "BLOCK" and issue.row_number > 0
    }

    write_review_issues_to_workbook(workbook_path, issues, accounts, vendors)

    if validation_summary["blocked_rows"] > 0:
        print(f"Workbook rows checked: {len(rows)}")
        print("IIF file was not generated because validation blocks were found.")
        print(f"Blocked rows: {validation_summary['blocked_rows']}")
        print(f"Review workbook: {workbook_path}")
        print("Open the Review_Issues sheet, fix the listed fields, then generate IIF again.")
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)
    iif_path = output_dir / "export.iif"
    summary_path = output_dir / "export_summary.txt"

    export_result = write_receipt_iif(
        rows,
        accounts,
        iif_path,
        allow_review=args.allow_review,
        preblocked_rows=preblocked_rows,
        vendors=vendors,
    )
    if export_result.blocked_rows > 0:
        if iif_path.exists():
            iif_path.unlink()
        fallback_issues = issues + [
            ValidationIssue(
                severity="BLOCK",
                sheet_name=block.sheet_name,
                row_number=block.row_number,
                field="IIF export",
                message=block.reason,
                current_value="",
            )
            for block in export_result.blocks
        ]
        write_review_issues_to_workbook(workbook_path, fallback_issues, accounts, vendors)
        fallback_summary = {
            "blocks": validation_summary["blocks"] + len(export_result.blocks),
            "warnings": validation_summary["warnings"],
            "blocked_rows": export_result.blocked_rows,
        }
        _write_blocked_export_summary(
            summary_path,
            workbook_path,
            args.source_mode,
            len(rows),
            fallback_summary,
            workbook_warnings,
            workbook_path,
        )
        print(f"Workbook rows checked: {len(rows)}")
        print("IIF file was removed because export blocks were found.")
        print(f"Blocked rows: {export_result.blocked_rows}")
        print(f"Review workbook: {workbook_path}")
        print(f"Export summary: {summary_path}")
        return 1

    _write_export_summary(
        summary_path,
        workbook_path,
        args.source_mode,
        len(rows),
        validation_summary,
        export_result,
        workbook_warnings,
    )

    print(f"Workbook rows checked: {len(rows)}")
    print(f"Exported rows: {export_result.exported_rows}")
    print(f"Blocked rows: {export_result.blocked_rows}")
    print(f"Export warnings: {len(export_result.warnings)}")
    print(f"New vendors to create: {len(export_result.new_vendors)}")
    print(f"IIF file: {iif_path}")
    print(f"Export summary: {summary_path}")
    print("Review workbook updated with Review_Issues.")
    return 0 if export_result.exported_rows > 0 else 1


def _prefill_accounts(args: argparse.Namespace) -> int:
    if args.source_mode == "bank":
        print("Bank account prefill is not implemented yet.")
        return 2

    workbook_path = Path(args.workbook)
    output_workbook = Path(args.output_workbook)

    try:
        _account_path, accounts = _load_accounts_for_company(args)
        vendors = load_vendors(Path(args.vendors)) if args.vendors else None
        result = prefill_receipt_accounts(
            workbook_path,
            output_workbook,
            accounts,
            default_payment_account=args.default_payment_account,
            vendors=vendors,
        )
    except (AccountSettingsError, ValueError) as exc:
        print(exc)
        return 2

    print(f"Workbook rows checked: {result.rows_checked}")
    print(f"QB Account cells filled: {result.accounts_filled}")
    print(f"Payment Account cells filled: {result.payment_accounts_filled}")
    print(f"QB Tax Code cells filled: {result.tax_codes_filled}")
    print(f"Rows marked for account review: {result.review_rows}")
    print(f"AI failures: {result.ai_failures}")
    print(f"Output workbook: {result.output_workbook}")
    print("Original workbook unchanged.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if args.command == "validate":
        return _validate(args)
    if args.command == "generate":
        return _generate(args)
    if args.command == "prefill-accounts":
        return _prefill_accounts(args)
    raise ValueError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
