import json
import os
import re
import sys
from pathlib import Path


_LIB_DIR = os.path.join (
    os.path.dirname (
        os.path.abspath(__file__)
    ),"..","_lib"
)
sys.path.insert(0, _LIB_DIR)

from connectors.ai_client import AIAuthError, AIClient, AIContentFilterError

from .config import PROMPT_PATH

_SCRIPTS_ROOT = Path(__file__).resolve().parent.parent

_MIME_MAP = {
    ".pdf" : "application/pdf",
    ".jpg" : "image/jpeg",
    ".jpeg" : "image/jpeg",
    ".png" : "image/png",
}

def _load_prompt(prompt_path: str | Path | None = None) -> str:
    selected_prompt = Path(prompt_path or PROMPT_PATH)
    if not selected_prompt.is_absolute():
        selected_prompt = _SCRIPTS_ROOT / selected_prompt
    prompt_path = selected_prompt
    return prompt_path.read_text(encoding="utf-8")

def _read_file(file_path : Path) -> tuple[bytes,str]:
    ext = file_path.suffix.lower()
    if ext not in _MIME_MAP :
        raise ValueError(f"Unsupported file type : {ext}")
    data = file_path.read_bytes()
    return data, _MIME_MAP[ext]


def _parse_json_array(text: str) -> list[dict]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```[a-z]*\s*","",cleaned)
    cleaned = re.sub(r"\s*```$","",cleaned)
    result = json.loads(cleaned)
    if not isinstance(result,list):
        result = [result]
    return result

def extract_from_file (
    file_path : Path,
    prompt_path: str | Path | None = None,
    prompt_text: str | None = None,
) -> list[dict]:
    prompt = prompt_text if prompt_text is not None else _load_prompt(prompt_path)
    file_data, mime_type = _read_file(file_path)

    client = AIClient(timeout=180, max_retries=1)
    call_kwargs = dict(
        prompt = prompt,
        file_parts = [{"mime_type" : mime_type, "data" : file_data}],
        json_mode = False,
        fallback = False,
        timeout = 180,
        max_retries = 1,
    )
    try:
        raw = client.call(provider = "gemini", **call_kwargs)
    except (AIAuthError, AIContentFilterError):
        raise
    except Exception:
        # Gemini failed (timeout/quota/provider error) — fall back to Claude.
        raw = client.call(provider = "anthropic", **call_kwargs)

    transactions = _parse_json_array(raw)

    for tx in transactions :
        if "has_line_items" not in tx or tx["has_line_items"] is None:
            tx["has_line_items"] = False
    return transactions
