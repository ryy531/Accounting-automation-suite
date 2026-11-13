from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .account_list import AccountList


SETTINGS_PATH = Path(__file__).with_name(".iif_settings.json")


class AccountSettingsError(ValueError):
    pass


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _loaded_encoding(accounts: AccountList) -> str:
    for account in accounts.accounts.values():
        encoding = account.row.get("_loaded_encoding")
        if encoding:
            return encoding
    return ""


def describe_account_file(account_path: Path, accounts: AccountList) -> dict[str, Any]:
    stat = account_path.stat()
    return {
        "accounts_path": str(account_path.resolve()),
        "file_size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": _file_sha256(account_path),
        "parsed_account_count": len(accounts.accounts),
        "encoding": _loaded_encoding(accounts),
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }


def save_account_settings(account_path: Path, accounts: AccountList) -> Path:
    settings = describe_account_file(account_path, accounts)
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return SETTINGS_PATH


def load_saved_account_path() -> Path:
    if not SETTINGS_PATH.exists():
        raise AccountSettingsError(
            f"No saved Chart of Accounts path found. Provide --accounts to save one first."
        )

    try:
        settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AccountSettingsError(
            f"Could not read saved IIF settings at {SETTINGS_PATH}: {exc}. Provide --accounts again."
        ) from exc

    raw_path = str(settings.get("accounts_path") or "").strip()
    if not raw_path:
        raise AccountSettingsError(
            f"Saved IIF settings at {SETTINGS_PATH} do not contain accounts_path. Provide --accounts again."
        )

    account_path = Path(raw_path)
    if not account_path.exists():
        raise AccountSettingsError(
            f"Saved Chart of Accounts file does not exist: {account_path}. Provide --accounts again."
        )
    if not account_path.is_file():
        raise AccountSettingsError(
            f"Saved Chart of Accounts path is not a file: {account_path}. Provide --accounts again."
        )

    return account_path
