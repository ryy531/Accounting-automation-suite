from pathlib import Path
from typing import Iterable


SUPPORTED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
_MAX_LISTED_NESTED_FILES = 12


def _warning(source_file: str, message: str, action_taken: str) -> dict:
    return {
        "level": "warning",
        "source_file": source_file,
        "message": message,
        "action_taken": action_taken,
    }


def _normalize_extensions(extensions: Iterable[str] | None) -> set[str]:
    if extensions is None:
        return set(SUPPORTED_EXTENSIONS)
    return {ext.lower() for ext in extensions}


def _describe_nested_files(folder: Path) -> str:
    files = sorted([child.name for child in folder.iterdir() if child.is_file()])
    if not files:
        return "No direct files found inside skipped folder"

    shown = files[:_MAX_LISTED_NESTED_FILES]
    suffix = ""
    if len(files) > len(shown):
        suffix = f"; plus {len(files) - len(shown)} more"
    return f"Ignored files: {', '.join(shown)}{suffix}"


def scan_input_folder(
    folder_path: Path,
    supported_extensions: Iterable[str] | None = None,
) -> tuple[list[Path], list[dict]]:
    if not folder_path.is_dir():
        raise ValueError(f"Input path is not a folder: {folder_path}")

    extensions = _normalize_extensions(supported_extensions)
    files = []
    warnings = []

    for child in sorted(folder_path.iterdir(), key=lambda path: path.name.lower()):
        if child.is_dir():
            warnings.append(
                _warning(
                    child.name,
                    f"Skipped nested folder: {child.name}",
                    _describe_nested_files(child),
                )
            )
            continue

        if child.is_file() and child.suffix.lower() in extensions and not child.name.startswith("._"):
            files.append(child)

    return files, warnings
