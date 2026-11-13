import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Sequence


_ILLEGAL_WINDOWS_CHARS = re.compile(r'[<>:"/\\|?*]')
_MAX_OUTPUT_STEM_LEN = 120


def _warning(source_file: str, message: str, action_taken: str) -> dict:
    return {
        "level": "warning",
        "source_file": source_file,
        "message": message,
        "action_taken": action_taken,
    }


def _sanitize_name(raw_name: str, fallback: str) -> tuple[str, list[dict]]:
    warnings = []
    original = raw_name or ""
    name = original.strip()

    if not name:
        name = fallback
        warnings.append(_warning("", "Empty name used for output naming", f"Used {fallback}"))

    sanitized = _ILLEGAL_WINDOWS_CHARS.sub("_", name).strip()
    if sanitized != name:
        warnings.append(
            _warning(
                "",
                f"Windows-illegal characters found in name: {original}",
                f"Replaced with safe name: {sanitized}",
            )
        )

    if not sanitized:
        sanitized = fallback
        warnings.append(_warning("", "Name became empty after sanitizing", f"Used {fallback}"))

    if len(sanitized) > _MAX_OUTPUT_STEM_LEN:
        truncated = sanitized[: _MAX_OUTPUT_STEM_LEN - len("..._trunc")] + "..._trunc"
        warnings.append(
            _warning(
                "",
                f"Name too long for output naming: {sanitized}",
                f"Truncated to: {truncated}",
            )
        )
        sanitized = truncated

    return sanitized, warnings


def make_safe_xlsx_name(source_name: str) -> tuple[str, list[dict]]:
    stem = Path(source_name).stem
    safe_stem, warnings = _sanitize_name(stem, "Untitled")
    return f"{safe_stem}.xlsx", warnings


def create_output_dir(input_path: Path, timestamp: datetime | None = None) -> tuple[Path, list[dict]]:
    warnings = []
    timestamp = timestamp or datetime.now()
    stamp = timestamp.strftime("%Y-%m-%d_%Hh%M")

    input_name = input_path.name if input_path.is_dir() else input_path.stem
    safe_input_name, name_warnings = _sanitize_name(input_name, "Untitled")
    warnings.extend(name_warnings)

    base_name = f"{safe_input_name}_Output_{stamp}"
    parent = input_path.parent
    output_dir = parent / base_name

    suffix = 2
    while output_dir.exists():
        output_dir = parent / f"{base_name}_{suffix}"
        suffix += 1

    if output_dir.name != base_name:
        warnings.append(
            _warning(
                "",
                f"Output folder already existed: {base_name}",
                f"Created alternate folder: {output_dir.name}",
            )
        )

    output_dir.mkdir(parents=True, exist_ok=False)
    return output_dir, warnings


def copy_for_rerun(source_path: Path, output_dir: Path) -> Path:
    rerun_dir = output_dir / "for_rerun"
    rerun_dir.mkdir(exist_ok=True)

    dest = rerun_dir / source_path.name
    if not dest.exists():
        shutil.copy2(source_path, dest)
        return dest

    stem = source_path.stem
    suffix = source_path.suffix
    counter = 2
    while True:
        candidate = rerun_dir / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            shutil.copy2(source_path, candidate)
            return candidate
        counter += 1


def open_outputs(output_dir: Path, excel_paths: Sequence[Path] = ()) -> None:
    if sys.platform != "win32":
        return

    os.startfile(str(output_dir))
    for excel_path in excel_paths:
        if excel_path and excel_path.exists():
            os.startfile(str(excel_path))
