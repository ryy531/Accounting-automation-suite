"""CLI tool to batch-rename Alex's monthly expense receipts to `date vendor amount`."""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

_LIB_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "_lib"
)
sys.path.insert(0, _LIB_DIR)

from connectors.ai_client import AIAuthError, AIClient, AIContentFilterError

_SCRIPTS_ROOT = Path(__file__).resolve().parent.parent
_REPORTS_DIR = _SCRIPTS_ROOT / "_reports"

_MIME_MAP = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}

_CONFIDENCE_REVIEW_THRESHOLD = 60

RENAME_PROMPT = """\
You are helping a small business owner organize personal monthly expense receipts.
Analyze the provided receipt/invoice document and extract three fields:

1. date
   - The transaction or invoice date on the document.
   - Format: YYYY-MM-DD
   - If only month and year are visible, use YYYY-MM-01.
   - If the date cannot be determined, use "unknown".

2. vendor
   - The vendor, store, or merchant name on the receipt.
   - Use the most recognizable short form. Max 30 characters.
   - If unknown, use "unknown".

3. amount
   - The final total amount paid on the receipt.
   - Format: plain decimal number, no currency symbol, no thousands separator (e.g. "12.34").
   - If no monetary amount is present, use "unknown".

4. confidence
   - An integer from 0 to 100 representing how confident you are in the above three fields combined.
   - 90-100: all three fields are clearly and unambiguously readable.
   - 60-89: most fields are readable but one may be uncertain or estimated.
   - 30-59: document is partially legible or fields are ambiguous.
   - 0-29: document is unreadable, blank, or unrelated to an expense receipt.

Respond ONLY with a valid JSON object. No explanation, no markdown, no extra text:
{"date": "...", "vendor": "...", "amount": "...", "confidence": 85}
"""


def collect_files(folder: Path) -> tuple[list[Path], list[str]]:
    """Recursively scan folder. Returns (supported_files, skipped_relative_paths)."""
    files = []
    skipped = []
    for root, _, filenames in os.walk(folder):
        for name in filenames:
            ext = os.path.splitext(name)[1].lower()
            path = Path(root) / name
            if ext in _MIME_MAP:
                files.append(path)
            else:
                skipped.append(str(path.relative_to(folder)))
    return sorted(files), sorted(skipped)


def sanitize(value: str, max_len: int) -> str:
    import re

    cleaned = re.sub(r'[\\/:*?"<>|\n\r\t]', "", str(value)).strip()
    return cleaned[:max_len]


def analyze_file(client: AIClient, path: Path) -> dict:
    """Run AI extraction on one file. Never raises - returns dict with status/provider/error."""
    ext = path.suffix.lower()
    mime_type = _MIME_MAP[ext]
    file_data = path.read_bytes()

    call_kwargs = dict(
        prompt=RENAME_PROMPT,
        file_parts=[{"mime_type": mime_type, "data": file_data}],
        json_mode=True,
        fallback=False,
        timeout=180,
        max_retries=1,
    )

    provider = "gemini"
    try:
        result = client.call(provider="gemini", **call_kwargs)
    except (AIAuthError, AIContentFilterError) as exc:
        return {"status": "error", "provider": "gemini", "error": str(exc)}
    except Exception:
        provider = "anthropic"
        try:
            result = client.call(provider="anthropic", **call_kwargs)
        except Exception as exc:
            return {"status": "error", "provider": "anthropic", "error": str(exc)}

    if not isinstance(result, dict):
        return {"status": "error", "provider": provider, "error": "non-dict response"}

    result["status"] = "ok"
    result["provider"] = provider
    result["error"] = None
    return result


def build_new_name(result: dict, original_path: Path, used_names: dict[str, set]) -> tuple[str, str]:
    """Returns (new_name, status) where status is 'ok', 'review', or 'unread'.

    used_names is keyed by parent directory so files in different folders that
    happen to generate the same stem don't get a needless _1 suffix -- they
    don't actually collide on disk.
    """
    ext = original_path.suffix.lower()

    if result.get("status") != "ok":
        stem = f"[UNREAD] {sanitize(original_path.stem, 50)}"
        status = "unread"
    else:
        confidence = int(result.get("confidence", 0) or 0)
        date = sanitize(result.get("date", "unknown") or "unknown", 10)
        vendor = sanitize(result.get("vendor", "unknown") or "unknown", 30)
        amount = sanitize(result.get("amount", "unknown") or "unknown", 15)

        all_unknown = date == "unknown" and vendor == "unknown" and amount == "unknown"

        if all_unknown or confidence < 20:
            stem = f"[UNREAD] {sanitize(original_path.stem, 50)}"
            status = "unread"
        elif confidence < _CONFIDENCE_REVIEW_THRESHOLD:
            stem = f"[REVIEW] {date} {vendor} {amount}"
            status = "review"
        else:
            stem = f"{date} {vendor} {amount}"
            status = "ok"

    names_in_dir = used_names.setdefault(str(original_path.parent), set())

    candidate = stem + ext
    if candidate not in names_in_dir:
        names_in_dir.add(candidate)
        return candidate, status

    i = 1
    while True:
        candidate = f"{stem}_{i}{ext}"
        if candidate not in names_in_dir:
            names_in_dir.add(candidate)
            return candidate, status
        i += 1


def write_report(plan: list[dict]) -> Path:
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = _REPORTS_DIR / f"receipt_rename_plan_{timestamp}.json"
    report_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    return report_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m receipts.rename_receipts",
        description="Batch-rename expense receipts to 'date vendor amount' using AI extraction.",
    )
    parser.add_argument("folder", nargs="?", help="Folder to scan (will prompt if omitted)")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Rename files immediately without an interactive confirmation prompt",
    )
    args = parser.parse_args(argv)

    folder_str = args.folder
    if not folder_str:
        folder_str = input("Folder to scan: ").strip().strip('"')

    folder = Path(folder_str)
    if not folder.is_dir():
        print(f"[ERROR] Folder not found: {folder}")
        return 1

    files, skipped = collect_files(folder)
    if skipped:
        print(f"Skipping {len(skipped)} unsupported file(s) ({', '.join(sorted(_MIME_MAP))} only):")
        for rel in skipped:
            print(f"  {rel}")
        print()

    if not files:
        print("No supported files found.")
        return 0

    print(f"Found {len(files)} file(s). Analyzing...\n")

    client = AIClient(timeout=180, max_retries=1)
    used_names: dict[str, set] = {}
    plan = []

    width = len(str(len(files)))
    for i, path in enumerate(files, 1):
        rel = path.relative_to(folder)
        print(f"[{i:{width}}/{len(files)}] {str(rel)[:55]:<55}", end="", flush=True)

        result = analyze_file(client, path)
        new_name, status = build_new_name(result, path, used_names)
        new_path = path.with_name(new_name)

        plan.append({
            "old_path": str(path),
            "new_path": str(new_path),
            "status": status,
            "provider": result.get("provider"),
            "confidence": result.get("confidence"),
            "error": result.get("error"),
        })
        print(f"  ->  {new_name}")

    print(f"\n{'-' * 80}")
    print(f"{'Status':<8} {'Original':<38} New Name")
    print(f"{'-' * 8} {'-' * 38} {'-' * 38}")
    for entry in plan:
        orig_name = Path(entry["old_path"]).name[:38]
        new_name = Path(entry["new_path"]).name
        print(f"{entry['status']:<8} {orig_name:<38} {new_name}")

    review_count = sum(1 for e in plan if e["status"] == "review")
    unread_count = sum(1 for e in plan if e["status"] == "unread")
    print(f"\nTotal: {len(plan)}  |  Review: {review_count}  |  Unread: {unread_count}")

    report_path = write_report(plan)
    print(f"Plan written to: {report_path}")

    if not args.apply:
        confirm = input("\nRename all listed files now? (y/N): ").strip().lower()
        if confirm != "y":
            print("Cancelled - no files were changed.")
            return 0

    renamed = failed = 0
    for entry in plan:
        old_path = Path(entry["old_path"])
        new_path = Path(entry["new_path"])
        try:
            old_path.rename(new_path)
            renamed += 1
        except Exception as exc:
            print(f"[ERROR] {old_path.name}: {exc}")
            failed += 1

    print(f"\nDone - Renamed: {renamed}  |  Failed: {failed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
