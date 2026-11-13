from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


SUPPORTED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
ROOT_GROUP = "_root"
ROOT_SHEET = "_root"


@dataclass(frozen=True)
class BatchJob:
    file_path: Path
    relative_folder: str
    top_group: str
    sheet_key: str


@dataclass(frozen=True)
class BatchScanResult:
    jobs: list[BatchJob]
    warnings: list[dict]
    folder_count: int


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


def _relative_folder(root_folder: Path, file_path: Path) -> str:
    parent = file_path.parent
    if parent == root_folder:
        return "."
    return str(parent.relative_to(root_folder))


def _group_and_sheet(relative_folder: str) -> tuple[str, str]:
    if relative_folder == ".":
        return ROOT_GROUP, ROOT_SHEET

    parts = list(Path(relative_folder).parts)
    top_group = parts[0]
    nested_parts = parts[1:]
    if not nested_parts:
        return top_group, ROOT_SHEET

    return top_group, "__".join(nested_parts)


def scan_batch_folder(
    root_folder: Path,
    supported_extensions: Iterable[str] | None = None,
) -> BatchScanResult:
    if not root_folder.is_dir():
        raise ValueError(f"Input path is not a folder: {root_folder}")

    extensions = _normalize_extensions(supported_extensions)
    jobs = []
    warnings = []
    folders_with_supported_files = set()

    for child in sorted(root_folder.rglob("*"), key=lambda path: str(path).lower()):
        if not child.is_file():
            continue

        if child.suffix.lower() not in extensions:
            continue

        if child.name.startswith("._"):
            continue

        relative_folder = _relative_folder(root_folder, child)
        top_group, sheet_key = _group_and_sheet(relative_folder)
        folders_with_supported_files.add(relative_folder)
        jobs.append(
            BatchJob(
                file_path=child,
                relative_folder=relative_folder,
                top_group=top_group,
                sheet_key=sheet_key,
            )
        )

    if not jobs:
        warnings.append(
            _warning(
                "",
                "No supported files found in batch folder",
                "Supported file types: PDF, JPG, JPEG, PNG",
            )
        )

    return BatchScanResult(
        jobs=jobs,
        warnings=warnings,
        folder_count=len(folders_with_supported_files),
    )
