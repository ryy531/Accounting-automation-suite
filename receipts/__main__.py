import argparse
import sys
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

from . import batch_scanner, output_manager, ui_selector
from .iif.account_list import load_accounts
from .iif.account_prefill import prefill_receipt_accounts
from .iif.iif_writer import write_receipt_iif
from .iif.report_writer import write_review_issues_to_workbook
from .iif.schema import ValidationIssue
from .iif.validator import summarize_issues, validate_rows
from .iif.vendor_list import load_vendors
from .iif.workbook_reader import read_workbook_rows
from .pipeline import process_folder, process_folder_batch, process_single_file


IIF_IMPORT_ROOT = Path(r"\\file-server\Shared Folder\IIF Import")
INVALID_FOLDER_NAME_CHARS = '<>:"/\\|?*'
VENDOR_LIST_SHEET_NAME = "vendor_list"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m receipts",
        description="Extract receipt or bank statement data from a file or folder.",
    )
    parser.add_argument("input_path", help="File or folder to process")
    parser.add_argument(
        "--type",
        dest="declared_type",
        choices=("receipts", "bank", "mixed"),
        required=False,
        help="User-declared document type for routing",
    )
    parser.add_argument(
        "--batch",
        action="store_true",
        help="Recursively process a folder into receipt batch workbooks",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip batch size confirmation prompt",
    )
    return parser.parse_args(argv)


def _confirm_batch(input_path: Path, skip_confirm: bool) -> bool:
    scan_result = batch_scanner.scan_batch_folder(input_path)
    print(f"Found {scan_result.folder_count} folder(s)")
    print(f"Found {len(scan_result.jobs)} supported file(s)")
    print("Estimated pages: unknown until PDF conversion")

    if skip_confirm:
        return True

    answer = input("Continue? Y/N: ").strip().lower()
    return answer in ("y", "yes")


def _as_list(output_paths) -> list[Path]:
    if not output_paths:
        return []
    if isinstance(output_paths, list):
        return [Path(path) for path in output_paths]
    return [Path(output_paths)]


def _receipt_workbooks(output_paths) -> list[Path]:
    paths = _as_list(output_paths)
    return [
        path
        for path in paths
        if path.suffix.lower() in {".xlsx", ".xlsm"}
        and path.name.lower() != "batch_summary.xlsx"
        and "bank_statement" not in path.name.lower()
    ]


def _prefill_qb_accounts(output_paths, account_list_path: str, vendor_list_path: str = "", default_payment_account: str = "") -> list[Path]:
    accounts = load_accounts(Path(account_list_path))
    vendors = load_vendors(Path(vendor_list_path)) if vendor_list_path else None
    prefilled_paths: list[Path] = []

    for workbook_path in _receipt_workbooks(output_paths):
        result = prefill_receipt_accounts(
            workbook_path,
            workbook_path,
            accounts,
            default_payment_account=default_payment_account,
            allow_in_place=True,
            vendors=vendors,
        )
        print(f"QuickBooks fields added to workbook: {result.output_workbook}")
        print(f"QB Account cells filled: {result.accounts_filled}")
        print(f"Rows marked for account review: {result.review_rows}")
        prefilled_paths.append(result.output_workbook)

    if not prefilled_paths:
        print("WARNING: No receipt workbook found for QuickBooks account prefill.")
    return prefilled_paths


def _write_export_summary(
    output_path: Path,
    workbook_path: Path,
    row_count: int,
    validation_summary: dict[str, int],
    export_result,
    workbook_warnings: list[str],
) -> None:
    lines = [
        "QuickBooks Desktop IIF Export Summary",
        f"Workbook: {workbook_path}",
        "Source mode: receipt",
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
    row_count: int,
    validation_summary: dict[str, int],
    workbook_warnings: list[str],
    review_workbook: Path,
) -> None:
    lines = [
        "QuickBooks Desktop IIF Export Summary",
        f"Workbook: {workbook_path}",
        "Source mode: receipt",
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


def _safe_folder_name(value: str) -> str:
    cleaned = "".join("_" if char in INVALID_FOLDER_NAME_CHARS else char for char in value)
    cleaned = " ".join(cleaned.split()).strip(" .")
    if not cleaned:
        return "workbook"
    return cleaned[:80]


def _iif_output_dir(workbook_path: Path, timestamp: str) -> Path:
    folder_name = f"{timestamp}_{_safe_folder_name(workbook_path.stem)}"
    return IIF_IMPORT_ROOT / folder_name


def _has_embedded_vendor_list(workbook_path: Path) -> bool:
    try:
        workbook = load_workbook(workbook_path, read_only=True, data_only=True)
        try:
            return any(
                sheet_name.strip().lower() == VENDOR_LIST_SHEET_NAME
                for sheet_name in workbook.sheetnames
            )
        finally:
            workbook.close()
    except Exception:
        return False


def _generate_iif_package(
    workbook_path: Path,
    account_list_path: str = "",
    vendor_list_path: str = "",
) -> tuple[Path, list[Path]]:
    account_source = Path(account_list_path) if account_list_path else workbook_path
    accounts = load_accounts(account_source)
    vendor_source = Path(vendor_list_path) if vendor_list_path else None
    if vendor_source is None and _has_embedded_vendor_list(workbook_path):
        vendor_source = workbook_path
    vendors = load_vendors(vendor_source) if vendor_source else None
    rows, workbook_warnings = read_workbook_rows(workbook_path, "receipt")
    issues = validate_rows(
        rows,
        accounts,
        source_mode="receipt",
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
        print("QuickBooks IIF was not generated because validation blocks were found.")
        print(f"Blocked rows: {validation_summary['blocked_rows']}")
        print(f"Review workbook: {workbook_path}")
        print("Open the Review_Issues sheet, fix the listed fields, then generate IIF again.")
        return workbook_path.parent, [workbook_path]

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = _iif_output_dir(workbook_path, timestamp)
    output_dir.mkdir(parents=True, exist_ok=True)

    export_result = write_receipt_iif(
        rows,
        accounts,
        output_dir / "export.iif",
        preblocked_rows=preblocked_rows,
        vendors=vendors,
    )
    if export_result.blocked_rows > 0:
        iif_path = output_dir / "export.iif"
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
        summary_path = output_dir / "export_summary.txt"
        _write_blocked_export_summary(
            summary_path,
            workbook_path,
            len(rows),
            fallback_summary,
            workbook_warnings,
            workbook_path,
        )
        print(f"QuickBooks IIF package: {output_dir}")
        print("IIF file was removed because export blocks were found.")
        print(f"Blocked rows: {export_result.blocked_rows}")
        print(f"Review workbook: {workbook_path}")
        return output_dir, [workbook_path, summary_path]

    _write_export_summary(
        output_dir / "export_summary.txt",
        workbook_path,
        len(rows),
        validation_summary,
        export_result,
        workbook_warnings,
    )

    print(f"QuickBooks IIF package: {output_dir}")
    print(f"Exported rows: {export_result.exported_rows}")
    print(f"Blocked rows: {export_result.blocked_rows}")
    print(f"New vendors to create: {len(export_result.new_vendors)}")
    print(f"Validation warnings: {validation_summary['warnings']}")
    return output_dir, [output_dir / "export.iif", output_dir / "export_summary.txt"]


def main():
    args = _parse_args(sys.argv[1:])

    input_path = Path(args.input_path).resolve()
    if not input_path.exists():
        print(f"ERROR : file not found : {input_path}")
        sys.exit(1)

    if args.batch and not input_path.is_dir():
        print("ERROR : batch mode requires a folder input")
        sys.exit(1)

    selected = None
    if args.declared_type:
        selected = {"mode": "ocr", "declared_type": args.declared_type, "qb_prefill": False}
    else:
        selected = ui_selector.select_run_options(input_path)

    if not selected:
        print("Cancelled.")
        sys.exit(1)

    if selected.get("mode") == "generate_iif":
        output_dir, paths_to_open = _generate_iif_package(
            input_path,
            selected.get("account_list_path", ""),
            selected.get("vendor_list_path", ""),
        )
        output_manager.open_outputs(output_dir, paths_to_open)
        return

    declared_type = selected["declared_type"]

    if args.batch and not _confirm_batch(input_path, args.yes):
        print("Cancelled.")
        sys.exit(0)

    output_dir, output_warnings = output_manager.create_output_dir(input_path)
    for warning in output_warnings:
        print(f"WARNING: {warning['message']} -> {warning['action_taken']}")

    print(f"Document type: {declared_type}")

    if args.batch:
        print(f"Batch processing folder: {input_path}")
        output_paths = process_folder_batch(
            input_path,
            output_dir,
            declared_type=declared_type,
            output_warnings=output_warnings,
        )
    elif input_path.is_dir():
        print(f"Processing folder: {input_path}")
        output_paths = process_folder(
            input_path,
            output_dir,
            declared_type=declared_type,
            output_warnings=output_warnings,
        )
    else:
        print(f"Processing {input_path.name} ...")
        output_paths = process_single_file(
            input_path,
            output_dir,
            declared_type=declared_type,
        )
        print(f"Done! Output : {output_paths}")

    prefilled_paths = []
    if selected.get("qb_prefill"):
        prefilled_paths = _prefill_qb_accounts(
            output_paths,
            selected["account_list_path"],
            selected.get("vendor_list_path", ""),
            selected.get("default_payment_account", ""),
        )

    if output_paths:
        print("Opening output ...")
        if isinstance(output_paths, list):
            paths_to_open = output_paths
        else:
            paths_to_open = [output_paths]

        excel_to_open = []
        if prefilled_paths:
            excel_to_open = prefilled_paths
        elif declared_type in ("receipts", "bank") and len(paths_to_open) == 1:
            excel_to_open = paths_to_open

        output_manager.open_outputs(output_dir, excel_to_open)

if __name__ == "__main__" :
    main()
