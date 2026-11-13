import time
from datetime import datetime
from pathlib import Path
from . import (
    batch_pdf_grouping,
    batch_scanner,
    batch_summary,
    cost_report,
    extract,
    excel_writer,
    folder_scanner,
    filename_pair_merge,
    normalize,
    output_manager,
    receipt_batch_writer,
    review_rules,
    source_audit,
)
from .bank_statement import review_rules as bank_review_rules
from .bank_statement import writer as bank_writer
from .run_logger import RunLogger

SUPPORTED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
RECEIPT_PROMPT_PATH = "receipts/prompts/receipt_extraction.md"
BANK_PROMPT_PATH = "receipts/prompts/bank_statement_extraction.md"
BANK_CONTEXT_PROMPT_PATH = "receipts/prompts/bank_statement_context.md"
BANK_DOC_TYPE = "bank_statement"
_SCRIPTS_ROOT = Path(__file__).resolve().parent.parent


def _warning(source_file: str, message: str, action_taken: str) -> dict:
    return {
        "level": "warning",
        "source_file": source_file,
        "message": message,
        "action_taken": action_taken,
    }


def _prompt_for_declared_type(declared_type: str) -> str:
    if declared_type == "bank":
        return BANK_PROMPT_PATH
    return RECEIPT_PROMPT_PATH


def _should_audit_source_coverage(declared_type: str) -> bool:
    return declared_type == "receipts"


def _read_prompt_text(prompt_path: str) -> str:
    return (_SCRIPTS_ROOT / prompt_path).read_text(encoding="utf-8")


def _extract_bank_statement_context(first_page: normalize.NormalizedPage) -> tuple[dict, str | None]:
    try:
        rows = extract.extract_from_file(first_page.image_path, BANK_CONTEXT_PROMPT_PATH)
    except Exception as e:
        return {}, f"{type(e).__name__}: {str(e)[:200]}"

    if not rows or not isinstance(rows[0], dict):
        return {}, "No statement context returned"
    return rows[0], None


def _bank_prompt_with_context(context: dict) -> str:
    base_prompt = _read_prompt_text(BANK_PROMPT_PATH)
    context_lines = [
        "",
        "Statement context for this source file:",
        f"- statement_year: {context.get('statement_year')}",
        f"- statement_date: {context.get('statement_date')}",
        f"- statement_period_start: {context.get('statement_period_start')}",
        f"- statement_period_end: {context.get('statement_period_end')}",
        f"- account_last_four: {context.get('account_last_four')}",
        f"- card_last_four: {context.get('card_last_four')}",
        "",
        "Use this statement context to complete transaction dates that show only",
        "month/day. If the period crosses years, assign December dates to the",
        "start year and January dates to the end year when that matches the period.",
    ]
    return base_prompt + "\n" + "\n".join(context_lines)


def _append_review_tag(row: dict, tag: str) -> None:
    note = row.get("note") or ""
    if tag in note:
        return
    row["note"] = f"{note} {tag}".strip()
    row["needs_review"] = True


def _normalize_doc_type(row: dict) -> str:
    doc_type = row.get("doc_type")
    if doc_type is None or str(doc_type).strip() == "":
        row["doc_type"] = "unknown"
        return "unknown"
    return str(doc_type).strip()


def _copy_source_for_rerun(
    source_file: str,
    source_paths: dict[str, Path],
    output_dir: Path,
    warnings: list[dict],
    copied_sources: set[str],
    reason: str,
) -> None:
    if source_file in copied_sources:
        return

    source_path = source_paths.get(source_file)
    if source_path is None or not source_path.exists():
        warnings.append(
            _warning(source_file, reason, "Could not copy source file to for_rerun")
        )
        copied_sources.add(source_file)
        return

    copied_path = output_manager.copy_for_rerun(source_path, output_dir)
    warnings.append(
        _warning(source_file, reason, f"Copied to {copied_path.relative_to(output_dir)}")
    )
    copied_sources.add(source_file)


def _mixed_bank_placeholder(row: dict) -> dict:
    source_file = row.get("source_file") or ""
    page_num = row.get("page_num") or 1
    thumbnail_path = row.get("thumbnail_path") or ""
    placeholder = {
        "date": None,
        "description": "[Bank statement detected - rerun with Bank Statements mode]",
        "subtotal": None,
        "gst": None,
        "pst": None,
        "total": None,
        "payment_card_last_four": "",
        "has_line_items": False,
        "doc_type": BANK_DOC_TYPE,
        "note": "[REVIEW: from mixed run]",
        "source_file": source_file,
        "page_num": page_num,
        "row_num": 1,
        "needs_review": True,
        "thumbnail_path": thumbnail_path,
    }
    if "_batch_top_group" in row:
        placeholder["_batch_top_group"] = row["_batch_top_group"]
    if "_batch_sheet_key" in row:
        placeholder["_batch_sheet_key"] = row["_batch_sheet_key"]
    return placeholder


def _route_rows(
    rows: list[dict],
    declared_type: str,
    source_paths: dict[str, Path],
    output_dir: Path,
    warnings: list[dict],
) -> tuple[list[dict], list[dict]]:
    receipt_rows = []
    bank_rows = []
    copied_sources: set[str] = set()
    mixed_bank_sources: set[str] = set()

    for row in rows:
        doc_type = _normalize_doc_type(row)
        source_file = row.get("source_file") or ""

        if declared_type == "bank":
            if doc_type != BANK_DOC_TYPE and not row.get("extract_failed"):
                _append_review_tag(row, "[REVIEW: doc type mismatch]")
                _copy_source_for_rerun(
                    source_file,
                    source_paths,
                    output_dir,
                    warnings,
                    copied_sources,
                    "Bank run produced non-bank doc_type",
                )
            bank_rows.append(row)
            continue

        if declared_type == "mixed" and doc_type == BANK_DOC_TYPE:
            _copy_source_for_rerun(
                source_file,
                source_paths,
                output_dir,
                warnings,
                copied_sources,
                "Bank statement found in mixed run",
            )
            if source_file not in mixed_bank_sources:
                receipt_rows.append(_mixed_bank_placeholder(row))
                mixed_bank_sources.add(source_file)
            continue

        if declared_type == "receipts" and doc_type == BANK_DOC_TYPE:
            _append_review_tag(row, "[REVIEW: doc type mismatch]")
            _copy_source_for_rerun(
                source_file,
                source_paths,
                output_dir,
                warnings,
                copied_sources,
                "Receipts run produced bank_statement doc_type",
            )

        receipt_rows.append(row)

    return receipt_rows, bank_rows


def _review_receipt_rows(rows: list[dict]) -> None:
    for row in rows:
        if row.get("extract_failed"):
            continue
        already_review = bool(row.get("needs_review"))
        needs_review, reason = review_rules.evaluate(row)
        row["needs_review"] = already_review or needs_review
        row["review_reason"] = reason


def _review_bank_rows(rows: list[dict], declared_type: str) -> None:
    rows_by_source: dict[str, list[dict]] = {}
    for row in rows:
        source_file = row.get("source_file") or "unknown_source"
        rows_by_source.setdefault(source_file, []).append(row)

    for source_rows in rows_by_source.values():
        bank_review_rules.evaluate_rows(source_rows, declared_type=declared_type)


def _failed_row(description: str, error: Exception, source_file: str, page_num: int, thumbnail_path: str) -> dict:
    return {
        "description": description,
        "note": f"Error: {type(error).__name__}: {str(error)[:200]}",
        "doc_type": "unknown",
        "source_file": source_file,
        "page_num": page_num,
        "row_num": 1,
        "thumbnail_path": thumbnail_path,
        "extract_failed": True,
        "needs_review": True,
    }


def process_single_file(file_path: Path, output_dir: Path, declared_type: str = "receipts") -> Path:
    run_started_at = datetime.now().isoformat(timespec="seconds")
    print(f"Processing {file_path.name} ...", flush=True)
    transactions = extract.extract_from_file(file_path, _prompt_for_declared_type(declared_type))
    for tx in transactions:
        tx["source_file"] = file_path.name
        tx["page_num"] = 1
        tx["row_num"] = 1
        tx["thumbnail_path"] = ""
        _normalize_doc_type(tx)
    source_audit_rows = []
    if _should_audit_source_coverage(declared_type):
        coverage = source_audit.apply_page_coverage(
            transactions,
            file_path.name,
            1,
            "",
            declared_type,
        )
        transactions = coverage.rows
        source_audit_rows.append(coverage.audit_row)
    print(f"Extracted {len(transactions)} transaction(s)")
    excel_path = output_dir / "receipts_results.xlsx"
    excel_writer.write_excel(transactions, excel_path, source_audit_rows=source_audit_rows)
    _, cost_txt_path = cost_report.write_cost_report(output_dir, run_started_at)
    print(f"Cost report: {cost_txt_path}")
    return excel_path


def process_folder(
    folder_path: Path,
    output_dir: Path,
    declared_type: str = "receipts",
    output_warnings: list[dict] | None = None,
):
    files, scan_warnings = folder_scanner.scan_input_folder(folder_path, SUPPORTED_EXTENSIONS)
    warnings = list(output_warnings or [])
    warnings.extend(scan_warnings)
    source_paths = {file_path.name: file_path for file_path in files}
    total = len(files)
    if total == 0:
        print("No supported files found.")
        return None

    print(f"Found {total} file(s) to process")

    run_log = RunLogger(output_dir / "run.log")
    run_start = time.time()
    run_started_at = datetime.now().isoformat(timespec="seconds")
    run_log.log(
        "run_started",
        folder_path=str(folder_path),
        file_count=total,
        declared_type=declared_type,
    )

    all_rows = []
    source_audit_rows = []
    prompt_path = _prompt_for_declared_type(declared_type)

    for idx, f in enumerate(files, start=1):
        run_log.log("file_started", source_file=f.name)
        file_start = time.time()
        print(f"[{idx}/{total}] Starting {f.name} ...", flush=True)

        try:
            pages = normalize.normalize_file(f, output_dir)
            run_log.log(
                "file_normalized",
                source_file=f.name,
                page_count=len(pages),
            )
            print(f"[{idx}/{total}] {f.name}: {len(pages)} page(s) ready", flush=True)
            file_prompt_text = None
            if declared_type == "bank" and pages:
                context, context_error = _extract_bank_statement_context(pages[0])
                if context_error:
                    warnings.append(
                        _warning(
                            f.name,
                            "Could not extract bank statement context",
                            context_error,
                        )
                    )
                elif context:
                    file_prompt_text = _bank_prompt_with_context(context)

            file_tx_count = 0
            page_total = len(pages)
            for page_idx, page in enumerate(pages, start=1):
                page_start = time.time()
                run_log.log(
                    "page_started",
                    source_file=page.source_file,
                    page_num=page.page_num,
                    page_index=page_idx,
                    page_count=page_total,
                )
                print(
                    f"  AI extracting page {page_idx}/{page_total} "
                    f"({page.source_file} p{page.page_num}) ...",
                    flush=True,
                )
                try:
                    transactions = extract.extract_from_file(
                        page.image_path,
                        prompt_path,
                        prompt_text=file_prompt_text,
                    )
                    thumbnail_path = (
                        str(page.thumbnail_path.relative_to(output_dir))
                        if page.thumbnail_path.exists()
                        else ""
                    )
                    for row_num, tx in enumerate(transactions, start=1):
                        tx["source_file"] = page.source_file
                        tx["page_num"] = page.page_num
                        tx["row_num"] = row_num
                        tx["row_index_within_page"] = row_num
                        tx["thumbnail_path"] = thumbnail_path
                        _normalize_doc_type(tx)
                    if _should_audit_source_coverage(declared_type):
                        coverage = source_audit.apply_page_coverage(
                            transactions,
                            page.source_file,
                            page.page_num,
                            thumbnail_path,
                            declared_type,
                        )
                        transactions = coverage.rows
                        source_audit_rows.append(coverage.audit_row)
                    file_tx_count += len(transactions)
                    all_rows.extend(transactions)
                    page_duration = round(time.time() - page_start, 1)
                    run_log.log(
                        "page_completed",
                        source_file=page.source_file,
                        page_num=page.page_num,
                        transactions_count=len(transactions),
                        duration_seconds=page_duration,
                    )
                    print(
                        f"  Done page {page_idx}/{page_total}: "
                        f"{len(transactions)} transaction(s), {page_duration}s",
                        flush=True,
                    )
                except Exception as e:
                    page_duration = round(time.time() - page_start, 1)
                    thumbnail_path = (
                        str(page.thumbnail_path.relative_to(output_dir))
                        if page.thumbnail_path.exists()
                        else ""
                    )
                    all_rows.append(
                        _failed_row(
                            "[Failed to extract]",
                            e,
                            page.source_file,
                            page.page_num,
                            thumbnail_path,
                        )
                    )
                    if _should_audit_source_coverage(declared_type):
                        source_audit_rows.append(
                            source_audit.failed_page_audit_row(
                                page.source_file,
                                page.page_num,
                                thumbnail_path,
                                e,
                            )
                        )
                    warnings.append(
                        _warning(
                            page.source_file,
                            f"Page {page.page_num} extraction failed",
                            "Added failed row to output workbook",
                        )
                    )
                    run_log.log(
                        "page_failed",
                        source_file=page.source_file,
                        page_num=page.page_num,
                        error=str(e)[:200],
                        duration_seconds=page_duration,
                    )
                    print(
                        f"  FAILED page {page_idx}/{page_total}: "
                        f"{type(e).__name__}",
                        flush=True,
                    )

            duration = round(time.time() - file_start, 1)
            run_log.log(
                "file_completed",
                source_file=f.name,
                page_count=len(pages),
                transactions_count=file_tx_count,
                duration_seconds=duration,
            )
            print(
                f"[{idx}/{total}] {f.name} ({len(pages)} page(s))"
                f" -> {file_tx_count} transaction(s), {duration}s",
                flush=True,
            )

        except Exception as e:
            all_rows.append(_failed_row("[Failed to process file]", e, f.name, 1, ""))
            warnings.append(
                _warning(
                    f.name,
                    "File processing failed",
                    "Added failed row to output workbook",
                )
            )
            run_log.log("file_failed", source_file=f.name, error=str(e)[:200])
            print(f"[{idx}/{total}] {f.name} -> FAILED ({type(e).__name__})", flush=True)

    all_rows = batch_pdf_grouping.group_batch_pdf_rows(all_rows)

    receipt_rows, bank_rows = _route_rows(
        all_rows,
        declared_type,
        source_paths,
        output_dir,
        warnings,
    )
    receipt_rows = filename_pair_merge.merge_filename_pairs(receipt_rows)
    _review_receipt_rows(receipt_rows)
    _review_bank_rows(bank_rows, declared_type)

    output_paths = []
    if receipt_rows:
        receipt_path = output_dir / "receipts_results.xlsx"
        excel_writer.write_excel(
            receipt_rows,
            receipt_path,
            warnings,
            source_audit_rows=source_audit_rows,
        )
        output_paths.append(receipt_path)

    bank_paths = bank_writer.write_bank_statement_workbooks(bank_rows, output_dir, warnings)
    output_paths.extend(bank_paths)
    cost_json_path, cost_txt_path = cost_report.write_cost_report(output_dir, run_started_at)

    total_duration = round(time.time() - run_start, 1)
    all_output_rows = receipt_rows + bank_rows
    ok_count = sum(
        1 for row in all_output_rows
        if not row.get("needs_review") and not row.get("extract_failed")
    )
    review_count = sum(
        1 for row in all_output_rows
        if row.get("needs_review") and not row.get("extract_failed")
    )
    failed_count = sum(
        1 for row in all_output_rows if row.get("extract_failed")
    )
    run_log.log(
        "run_completed",
        total_transactions=len(all_output_rows),
        total_ok=ok_count,
        total_review=review_count,
        total_failed=failed_count,
        output_count=len(output_paths),
        total_duration_seconds=total_duration,
        cost_report=str(cost_txt_path.relative_to(output_dir)),
    )
    run_log.write()

    print()
    print("Done.")
    print(f"  Total: {len(all_output_rows)} transactions")
    print(f"  OK: {ok_count} (white rows)")
    print(f"  Need review: {review_count} (yellow rows)")
    print(f"  Failed: {failed_count} (red rows)")
    print(f"  Warnings: {len(warnings)}")
    print(f"  Cost report: {cost_txt_path}")
    for output_path in output_paths:
        print(f"Output: {output_path}")

    if len(output_paths) == 1:
        return output_paths[0]
    return output_paths


def _source_key(root_folder: Path, file_path: Path) -> str:
    return str(file_path.relative_to(root_folder))


def _tag_batch_row(row: dict, job: batch_scanner.BatchJob) -> None:
    row["_batch_top_group"] = job.top_group
    row["_batch_sheet_key"] = job.sheet_key


def _group_receipt_rows(rows: list[dict]) -> dict[str, dict[str, list[dict]]]:
    grouped: dict[str, dict[str, list[dict]]] = {}
    for row in rows:
        group_name = row.get("_batch_top_group") or batch_scanner.ROOT_GROUP
        sheet_key = row.get("_batch_sheet_key") or batch_scanner.ROOT_SHEET
        grouped.setdefault(group_name, {}).setdefault(sheet_key, []).append(row)
    return grouped


def _warnings_by_group(
    warnings: list[dict],
    source_group_map: dict[str, str],
) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for warning in warnings:
        source_file = warning.get("source_file") or ""
        group_name = source_group_map.get(source_file, batch_scanner.ROOT_GROUP)
        grouped.setdefault(group_name, []).append(warning)
    return grouped


def _source_audit_by_group(
    audit_rows: list[dict],
    source_group_map: dict[str, str],
) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for audit_row in audit_rows:
        source_file = audit_row.get("source_file") or ""
        group_name = source_group_map.get(source_file, batch_scanner.ROOT_GROUP)
        grouped.setdefault(group_name, []).append(audit_row)
    return grouped


def _summary_row_for_group(
    group_name: str,
    sheet_rows: dict[str, list[dict]],
    workbook_path: Path,
    output_dir: Path,
    warning_count: int,
) -> dict:
    rows = [row for sheet in sheet_rows.values() for row in sheet]
    source_files = {
        row.get("source_file")
        for row in rows
        if row.get("source_file")
    }
    ok_count = sum(
        1 for row in rows
        if not row.get("needs_review") and not row.get("extract_failed")
    )
    review_count = sum(
        1 for row in rows
        if row.get("needs_review") and not row.get("extract_failed")
    )
    failed_count = sum(1 for row in rows if row.get("extract_failed"))

    notes = []
    if failed_count:
        notes.append("failed rows present")
    if review_count:
        notes.append("review rows present")
    if warning_count:
        notes.append("warnings present")

    return {
        "relative_group": group_name,
        "workbook_path": str(workbook_path.relative_to(output_dir)),
        "sheet_count": len(sheet_rows),
        "source_file_count": len(source_files),
        "transaction_rows": len(rows),
        "ok_count": ok_count,
        "review_count": review_count,
        "failed_count": failed_count,
        "warning_count": warning_count,
        "notes": "; ".join(notes),
    }


def process_folder_batch(
    folder_path: Path,
    output_dir: Path,
    declared_type: str = "receipts",
    output_warnings: list[dict] | None = None,
):
    from . import checkpoint

    scan_result = batch_scanner.scan_batch_folder(folder_path, SUPPORTED_EXTENSIONS)
    warnings = list(output_warnings or [])
    warnings.extend(scan_result.warnings)
    jobs = scan_result.jobs
    total = len(jobs)
    if total == 0:
        print("No supported files found.")
        if warnings:
            summary_path = batch_summary.write_batch_summary(
                [
                    {
                        "relative_group": batch_scanner.ROOT_GROUP,
                        "workbook_path": "",
                        "sheet_count": 0,
                        "source_file_count": 0,
                        "transaction_rows": 0,
                        "ok_count": 0,
                        "review_count": 0,
                        "failed_count": 0,
                        "warning_count": len(warnings),
                        "notes": "no supported files",
                    }
                ],
                output_dir,
            )
            return [summary_path]
        return None

    print(f"Found {scan_result.folder_count} folder(s)")
    print(f"Found {total} supported file(s)")

    source_paths = {}
    source_group_map = {}
    for job in jobs:
        key = _source_key(folder_path, job.file_path)
        source_paths[key] = job.file_path
        source_group_map[key] = job.top_group

    checkpoint_store = checkpoint.CheckpointStore(folder_path)
    print(f"Checkpoint folder: {checkpoint_store.checkpoint_dir}")

    run_log = RunLogger(output_dir / "run.log")
    run_start = time.time()
    run_started_at = datetime.now().isoformat(timespec="seconds")
    run_log.log(
        "batch_run_started",
        folder_path=str(folder_path),
        folder_count=scan_result.folder_count,
        file_count=total,
        declared_type=declared_type,
    )
    run_log.log(
        "checkpoint_ready",
        checkpoint_dir=str(checkpoint_store.checkpoint_dir),
        schema_version=checkpoint.SCHEMA_VERSION,
    )

    all_rows = []
    source_audit_rows = []
    prompt_path = _prompt_for_declared_type(declared_type)

    for idx, job in enumerate(jobs, start=1):
        source_key = _source_key(folder_path, job.file_path)
        run_log.log("file_started", source_file=source_key)
        file_start = time.time()
        print(f"[{idx}/{total}] Starting {source_key} ...", flush=True)
        file_signature = None
        file_rows = []
        file_warnings = []
        file_failed = False

        try:
            file_signature = checkpoint.compute_signature(job.file_path, source_key)
            cached = checkpoint_store.get_success_result(
                source_key,
                file_signature,
                declared_type,
            )
            if cached is not None:
                cached_rows = cached["rows"]
                cached_warnings = cached["warnings"]
                if _should_audit_source_coverage(declared_type):
                    if cached_rows:
                        all_rows.extend(cached_rows)
                        source_audit_rows.extend(
                            source_audit.cache_hit_audit_rows(source_key, cached_rows)
                        )
                    else:
                        coverage = source_audit.apply_page_coverage(
                            [],
                            source_key,
                            1,
                            "",
                            declared_type,
                        )
                        all_rows.extend(coverage.rows)
                        source_audit_rows.append(coverage.audit_row)
                else:
                    all_rows.extend(cached_rows)
                warnings.extend(cached_warnings)
                warnings.append(
                    _warning(
                        source_key,
                        "Used cached OCR result",
                        "Skipped OCR; thumbnail not regenerated",
                    )
                )
                duration = round(time.time() - file_start, 1)
                run_log.log(
                    "file_cache_hit",
                    source_file=source_key,
                    rows_count=len(cached_rows),
                    duration_seconds=duration,
                )
                print(f"[CACHE HIT] {source_key}", flush=True)
                print(
                    f"[{idx}/{total}] {source_key} -> reused cache "
                    f"({len(cached_rows)} row(s)), {duration}s",
                    flush=True,
                )
                continue

            cache_miss_reason = checkpoint_store.miss_reason(
                source_key,
                file_signature,
                declared_type,
            )
            run_log.log("file_cache_miss", source_file=source_key)
            print(f"[CACHE MISS] {source_key} reason={cache_miss_reason}", flush=True)
            pages = normalize.normalize_file(job.file_path, output_dir)
            run_log.log(
                "file_normalized",
                source_file=source_key,
                page_count=len(pages),
            )
            print(f"[{idx}/{total}] {source_key}: {len(pages)} page(s) ready", flush=True)
            file_prompt_text = None
            if declared_type == "bank" and pages:
                context, context_error = _extract_bank_statement_context(pages[0])
                if context_error:
                    warning = _warning(
                        source_key,
                        "Could not extract bank statement context",
                        context_error,
                    )
                    warnings.append(warning)
                    file_warnings.append(warning)
                elif context:
                    file_prompt_text = _bank_prompt_with_context(context)

            file_tx_count = 0
            page_total = len(pages)
            for page_idx, page in enumerate(pages, start=1):
                page_start = time.time()
                run_log.log(
                    "page_started",
                    source_file=source_key,
                    page_num=page.page_num,
                    page_index=page_idx,
                    page_count=page_total,
                )
                print(
                    f"  AI extracting page {page_idx}/{page_total} "
                    f"({source_key} p{page.page_num}) ...",
                    flush=True,
                )
                try:
                    transactions = extract.extract_from_file(
                        page.image_path,
                        prompt_path,
                        prompt_text=file_prompt_text,
                    )
                    thumbnail_path = (
                        str(page.thumbnail_path.relative_to(output_dir))
                        if page.thumbnail_path.exists()
                        else ""
                    )
                    for row_num, tx in enumerate(transactions, start=1):
                        tx["source_file"] = source_key
                        tx["page_num"] = page.page_num
                        tx["row_num"] = row_num
                        tx["row_index_within_page"] = row_num
                        tx["thumbnail_path"] = thumbnail_path
                        _normalize_doc_type(tx)
                        _tag_batch_row(tx, job)
                    if _should_audit_source_coverage(declared_type):
                        coverage = source_audit.apply_page_coverage(
                            transactions,
                            source_key,
                            page.page_num,
                            thumbnail_path,
                            declared_type,
                        )
                        transactions = coverage.rows
                        source_audit_rows.append(coverage.audit_row)
                    file_tx_count += len(transactions)
                    file_rows.extend(transactions)
                    all_rows.extend(transactions)
                    page_duration = round(time.time() - page_start, 1)
                    run_log.log(
                        "page_completed",
                        source_file=source_key,
                        page_num=page.page_num,
                        transactions_count=len(transactions),
                        duration_seconds=page_duration,
                    )
                    print(
                        f"  Done page {page_idx}/{page_total}: "
                        f"{len(transactions)} transaction(s), {page_duration}s",
                        flush=True,
                    )
                except Exception as e:
                    file_failed = True
                    page_duration = round(time.time() - page_start, 1)
                    thumbnail_path = (
                        str(page.thumbnail_path.relative_to(output_dir))
                        if page.thumbnail_path.exists()
                        else ""
                    )
                    failed = _failed_row(
                        "[Failed to extract]",
                        e,
                        source_key,
                        page.page_num,
                        thumbnail_path,
                    )
                    _tag_batch_row(failed, job)
                    all_rows.append(failed)
                    if _should_audit_source_coverage(declared_type):
                        source_audit_rows.append(
                            source_audit.failed_page_audit_row(
                                source_key,
                                page.page_num,
                                thumbnail_path,
                                e,
                            )
                        )
                    warnings.append(
                        _warning(
                            source_key,
                            f"Page {page.page_num} extraction failed",
                            "Added failed row to output workbook",
                        )
                    )
                    run_log.log(
                        "page_failed",
                        source_file=source_key,
                        page_num=page.page_num,
                        error=str(e)[:200],
                        duration_seconds=page_duration,
                    )
                    print(
                        f"  FAILED page {page_idx}/{page_total}: "
                        f"{type(e).__name__}",
                        flush=True,
                    )

            duration = round(time.time() - file_start, 1)
            run_log.log(
                "file_completed",
                source_file=source_key,
                page_count=len(pages),
                transactions_count=file_tx_count,
                duration_seconds=duration,
            )
            print(
                f"[{idx}/{total}] {source_key} ({len(pages)} page(s))"
                f" -> {file_tx_count} transaction(s), {duration}s",
                flush=True,
            )
            if file_failed:
                checkpoint_store.save_failed(
                    source_key,
                    file_signature,
                    declared_type,
                    "One or more pages failed during extraction",
                )
                print(
                    f"[CACHE FAILED] {source_key} error=one_or_more_pages_failed",
                    flush=True,
                )
            else:
                checkpoint_store.save_success(
                    source_key,
                    file_signature,
                    declared_type,
                    file_rows,
                    page_count=len(pages),
                    warnings=file_warnings,
                )
                print(f"[CACHE SAVE] {source_key} rows={len(file_rows)}", flush=True)

        except Exception as e:
            failed = _failed_row("[Failed to process file]", e, source_key, 1, "")
            _tag_batch_row(failed, job)
            all_rows.append(failed)
            warnings.append(
                _warning(
                    source_key,
                    "File processing failed",
                    "Added failed row to output workbook",
                )
            )
            run_log.log("file_failed", source_file=source_key, error=str(e)[:200])
            print(f"[{idx}/{total}] {source_key} -> FAILED ({type(e).__name__})", flush=True)
            if file_signature is not None:
                checkpoint_store.save_failed(
                    source_key,
                    file_signature,
                    declared_type,
                    f"{type(e).__name__}: {str(e)[:200]}",
                )
                print(
                    f"[CACHE FAILED] {source_key} error={type(e).__name__}",
                    flush=True,
                )

    all_rows = batch_pdf_grouping.group_batch_pdf_rows(all_rows)

    receipt_rows, bank_rows = _route_rows(
        all_rows,
        declared_type,
        source_paths,
        output_dir,
        warnings,
    )
    receipt_rows = filename_pair_merge.merge_filename_pairs(receipt_rows)
    _review_receipt_rows(receipt_rows)
    _review_bank_rows(bank_rows, declared_type)

    output_paths = []
    grouped_receipts = _group_receipt_rows(receipt_rows)
    group_warnings = _warnings_by_group(warnings, source_group_map)
    group_source_audit = _source_audit_by_group(source_audit_rows, source_group_map)
    receipt_paths = receipt_batch_writer.write_receipt_batch_workbooks(
        grouped_receipts,
        output_dir,
        group_warnings,
        group_source_audit,
    )
    output_paths.extend(receipt_paths.values())

    bank_paths = bank_writer.write_bank_statement_workbooks(bank_rows, output_dir, warnings)
    output_paths.extend(bank_paths)

    summary_rows = []
    for group_name, sheet_rows in grouped_receipts.items():
        workbook_path = receipt_paths.get(group_name)
        if workbook_path is None:
            continue
        summary_rows.append(
            _summary_row_for_group(
                group_name,
                sheet_rows,
                workbook_path,
                output_dir,
                len(group_warnings.get(group_name, [])),
            )
        )

    if bank_paths:
        summary_rows.append(
            {
                "relative_group": "bank_statements",
                "workbook_path": "bank_statements",
                "sheet_count": "",
                "source_file_count": len(bank_paths),
                "transaction_rows": len(bank_rows),
                "ok_count": sum(
                    1 for row in bank_rows
                    if not row.get("needs_review") and not row.get("extract_failed")
                ),
                "review_count": sum(
                    1 for row in bank_rows
                    if row.get("needs_review") and not row.get("extract_failed")
                ),
                "failed_count": sum(1 for row in bank_rows if row.get("extract_failed")),
                "warning_count": len(warnings),
                "notes": "bank statement output",
            }
        )

    summary_path = batch_summary.write_batch_summary(summary_rows, output_dir)
    output_paths.append(summary_path)
    cost_json_path, cost_txt_path = cost_report.write_cost_report(output_dir, run_started_at)

    total_duration = round(time.time() - run_start, 1)
    all_output_rows = receipt_rows + bank_rows
    ok_count = sum(
        1 for row in all_output_rows
        if not row.get("needs_review") and not row.get("extract_failed")
    )
    review_count = sum(
        1 for row in all_output_rows
        if row.get("needs_review") and not row.get("extract_failed")
    )
    failed_count = sum(1 for row in all_output_rows if row.get("extract_failed"))
    run_log.log(
        "batch_run_completed",
        total_transactions=len(all_output_rows),
        total_ok=ok_count,
        total_review=review_count,
        total_failed=failed_count,
        output_count=len(output_paths),
        total_duration_seconds=total_duration,
        cost_report=str(cost_txt_path.relative_to(output_dir)),
    )
    run_log.write()

    print()
    print("Done.")
    print(f"  Total: {len(all_output_rows)} transactions")
    print(f"  OK: {ok_count} (white rows)")
    print(f"  Need review: {review_count} (yellow rows)")
    print(f"  Failed: {failed_count} (red rows)")
    print(f"  Warnings: {len(warnings)}")
    print(f"  Cost report: {cost_txt_path}")
    for output_path in output_paths:
        print(f"Output: {output_path}")

    return output_paths
