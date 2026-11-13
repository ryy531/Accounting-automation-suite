import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile


SCHEMA_VERSION = 1
CACHE_NOTE = "[CACHE: reused OCR result; thumbnail not regenerated]"
_MAX_SAFE_NAME_LEN = 80
_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*\s]+')


@dataclass(frozen=True)
class FileSignature:
    source_key: str
    file_size: int
    mtime_ns: int
    sha256: str


def _safe_root_name(root_folder: Path) -> str:
    name = root_folder.name.strip() or "root"
    safe_name = _UNSAFE_CHARS.sub("_", name).strip("._") or "root"
    return safe_name[:_MAX_SAFE_NAME_LEN]


def _root_hash(root_folder: Path) -> str:
    resolved = str(root_folder.resolve()).lower()
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:12]


def checkpoint_dir_for(root_folder: Path) -> Path:
    folder_name = f"{_safe_root_name(root_folder)}_{_root_hash(root_folder)}"
    return root_folder.parent / ".receipts_checkpoint" / folder_name


def _manifest_path(root_folder: Path) -> Path:
    return checkpoint_dir_for(root_folder) / "manifest.json"


def compute_signature(file_path: Path, source_key: str) -> FileSignature:
    stat = file_path.stat()
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return FileSignature(
        source_key=source_key,
        file_size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
        sha256=digest.hexdigest(),
    )


class CheckpointStore:
    def __init__(self, root_folder: Path):
        self.root_folder = root_folder
        self.checkpoint_dir = checkpoint_dir_for(root_folder)
        self.manifest_path = _manifest_path(root_folder)
        self.manifest = self._load_manifest()

    def _empty_manifest(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "root_folder": str(self.root_folder.resolve()),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "updated_at": None,
            "files": {},
        }

    def _load_manifest(self) -> dict:
        if not self.manifest_path.exists():
            return self._empty_manifest()
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._empty_manifest()
        if data.get("schema_version") != SCHEMA_VERSION:
            return self._empty_manifest()
        if not isinstance(data.get("files"), dict):
            return self._empty_manifest()
        return data

    def _write_manifest(self) -> None:
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.manifest["updated_at"] = datetime.now().isoformat(timespec="seconds")
        text = json.dumps(self.manifest, ensure_ascii=False, indent=2)
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=self.checkpoint_dir,
            prefix="manifest_",
            suffix=".tmp",
        ) as handle:
            handle.write(text)
            temp_path = Path(handle.name)
        temp_path.replace(self.manifest_path)

    def get_success_rows(
        self,
        source_key: str,
        signature: FileSignature,
        declared_type: str,
    ) -> list[dict] | None:
        result = self.get_success_result(source_key, signature, declared_type)
        if result is None:
            return None
        return result["rows"]

    def get_success_result(
        self,
        source_key: str,
        signature: FileSignature,
        declared_type: str,
    ) -> dict | None:
        record = self.manifest.get("files", {}).get(source_key)
        if not isinstance(record, dict):
            return None
        if record.get("schema_version") != SCHEMA_VERSION:
            return None
        if record.get("status") != "success":
            return None
        if record.get("source_key") != source_key:
            return None
        if record.get("declared_type") != declared_type:
            return None
        if record.get("file_size") != signature.file_size:
            return None
        if record.get("mtime_ns") != signature.mtime_ns:
            return None
        if record.get("sha256") != signature.sha256:
            return None

        rows = record.get("rows")
        if not isinstance(rows, list):
            return None
        warnings = record.get("warnings")
        if not isinstance(warnings, list):
            warnings = []
        return {
            "rows": _prepare_cached_rows(rows),
            "warnings": deepcopy(warnings),
        }

    def miss_reason(
        self,
        source_key: str,
        signature: FileSignature,
        declared_type: str,
    ) -> str:
        record = self.manifest.get("files", {}).get(source_key)
        if not isinstance(record, dict):
            return "no_record"
        if record.get("schema_version") != SCHEMA_VERSION:
            return "schema_version_mismatch"
        if record.get("status") != "success":
            return f"status_{record.get('status') or 'unknown'}"
        if record.get("source_key") != source_key:
            return "source_key_mismatch"
        if record.get("declared_type") != declared_type:
            return "declared_type_mismatch"
        if record.get("file_size") != signature.file_size:
            return "file_size_changed"
        if record.get("mtime_ns") != signature.mtime_ns:
            return "mtime_changed"
        if record.get("sha256") != signature.sha256:
            return "sha256_changed"
        if not isinstance(record.get("rows"), list):
            return "rows_missing"
        return "unknown"

    def save_success(
        self,
        source_key: str,
        signature: FileSignature,
        declared_type: str,
        rows: list[dict],
        page_count: int,
        warnings: list[dict] | None = None,
    ) -> None:
        self.manifest.setdefault("files", {})[source_key] = {
            "schema_version": SCHEMA_VERSION,
            "status": "success",
            "source_key": source_key,
            "declared_type": declared_type,
            "file_size": signature.file_size,
            "mtime_ns": signature.mtime_ns,
            "sha256": signature.sha256,
            "page_count": page_count,
            "processed_at": datetime.now().isoformat(timespec="seconds"),
            "rows": deepcopy(rows),
            "warnings": deepcopy(warnings or []),
            "error": None,
        }
        self._write_manifest()

    def save_failed(
        self,
        source_key: str,
        signature: FileSignature,
        declared_type: str,
        error: str,
    ) -> None:
        self.manifest.setdefault("files", {})[source_key] = {
            "schema_version": SCHEMA_VERSION,
            "status": "failed",
            "source_key": source_key,
            "declared_type": declared_type,
            "file_size": signature.file_size,
            "mtime_ns": signature.mtime_ns,
            "sha256": signature.sha256,
            "processed_at": datetime.now().isoformat(timespec="seconds"),
            "rows": [],
            "warnings": [],
            "error": error[:500],
        }
        self._write_manifest()


def _prepare_cached_rows(rows: list[dict]) -> list[dict]:
    cached_rows = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_copy = deepcopy(row)
        row_copy["thumbnail_path"] = ""
        note = str(row_copy.get("note") or "").strip()
        if CACHE_NOTE not in note:
            row_copy["note"] = f"{note} {CACHE_NOTE}".strip()
        cached_rows.append(row_copy)
    return cached_rows
