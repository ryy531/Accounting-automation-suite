# =============================================================================
# ai_extract_info.py — AI Tax Info Extraction (Menu 2)
#
# What it does:
#   1. User selects a client folder from the Intake workflow
#   2. Recursively scans the client's "1. Client Intake" subfolder
#   3. Sends each supported file to AI (Gemini or OpenAI) for tax field extraction
#   4. Merges results across files (best confidence wins, conflicts flagged)
#   5. Writes filled Info Template copy → client Intake subfolder (for TaxCycle import)
#   6. Writes source tracking report → shows where each value came from
#   7. Writes missing info report → lists fields still needed
#
# Dependencies:
#   - config.py for paths and settings
#   - ai_rename.py for shared AI utilities (temporary, pending utils refactor)
#
# Supported AI providers:
#   - Gemini (google-genai)
#   - OpenAI (openai)
#
# Menu: 2 (was originally planned as new menu; replaced old name_mapping)
# =============================================================================

import base64
import json
import os
import re
import shutil
import time
from datetime import date

import config

# ── Temporary: import shared functions from ai_rename (to be moved to utils.py) ─
from ai_rename import (
    RateLimiter,
    _machine_fernet,
    collect_files,
    load_api_key,
    prepare_parts,
    setup_api_key,
    SUPPORTED_EXTENSIONS as RENAME_EXTENSIONS,
)


# ── Constants ───────────────────────────────────────────────────────────────────

# File extensions to scan in intake folders
SCAN_EXTENSIONS = config.INTAKE_FILE_EXTENSIONS

# Primary fields extracted PER PERSON (spouse fields derived via cross-fill)
PERSON_FIELD_CODES = [f for f in config.CORE_FIELD_CODES if not f.startswith("Spouse")]

# Spouse template field → partner's primary field (for cross-fill in couple mode)
SPOUSE_TO_PRIMARY = {
    "SpouseID.SIN": "ID.SIN",
    "SpouseID.FirstName": "ID.FirstName",
    "SpouseID.LastName": "ID.LastName",
    "SpouseID.BirthDate": "ID.BirthDate",
    "SpouseID.Gender": "ID.Gender",
    "SpouseAddress.Email": "Address.Email",
}

# Group labels for report formatting (field code prefix → display name)
FIELD_GROUPS = {
    "ID.":       "Personal Identification",
    "Address.":  "Address & Contact",
    "Filing.":   "Filing Information",
    "Residency.":"Residency",
    "SpouseID.":     "Spouse — Identification",
    "SpouseAddress.":"Spouse — Contact",
}

# AI prompt template — multi-person tax field extraction
EXTRACT_PROMPT = """\
You are a Canadian personal tax document analyzer.

STEP 1 — Identify ALL individuals in this document (e.g., primary taxpayer, spouse/partner).
Look for names, SINs, or any other identifying information that distinguishes individuals.

STEP 2 — For EACH individual, extract the following fields if clearly present.

RULES:
- For fields with numbered options (e.g. "1=Male, 2=Female"), return the NUMBER only.
- SIN: 9 digits, no spaces or dashes.
- Dates: YYYY-MM-DD format.
- Postal codes: A1A1A1 or A1A 1A1 format.
- Province: use the number code from the field description.
- If a field is NOT present for a person, OMIT it entirely. Do NOT include null values.
- Do NOT guess or fabricate values.
- Extract each person's data INDEPENDENTLY.

FIELDS TO EXTRACT (per person):
{field_list}

Respond ONLY with valid JSON. No explanation, no markdown, no extra text:
{{
  "individuals": [
    {{
      "label": "Person's full name if known, otherwise 'Person 1'",
      "fields": {{
        "FIELD_CODE": {{"value": "extracted value", "confidence": 85}}
      }}
    }}
  ]
}}

If only one person is found, return a single-element array.
"""


# ── Field spec loading ──────────────────────────────────────────────────────────

def _load_field_specs() -> dict[str, dict]:
    """
    Load field specifications from Info Template.xlsx Export sheet.

    Reads Field Code (col A), Description (col D), Data Type (col E).
    Only returns fields listed in config.CORE_FIELD_CODES.

    Returns:
        {field_code: {"description": str, "data_type": str}}
        e.g. {"ID.SIN": {"description": "Social insurance number", "data_type": ""}}
    """
    import openpyxl

    if not os.path.exists(config.INFO_TEMPLATE_PATH):
        print(f"  [ERROR] Template not found: {config.INFO_TEMPLATE_PATH}")
        return {}

    wb = openpyxl.load_workbook(config.INFO_TEMPLATE_PATH, data_only=True)
    # "Export" sheet must exist — otherwise template is corrupted
    if "Export" not in wb.sheetnames:
        print(f"  [ERROR] Template has no 'Export' sheet. Sheets found: {wb.sheetnames}")
        wb.close()
        return {}
    ws = wb["Export"]

    core_set = set(config.CORE_FIELD_CODES)
    specs = {}

    for row in ws.iter_rows(min_row=2, max_col=5, values_only=True):
        field_code  = str(row[0] or "").strip()
        description = str(row[3] or "").strip()  # Column D
        data_type   = str(row[4] or "").strip()  # Column E

        if field_code in core_set:
            specs[field_code] = {
                "description": description,
                "data_type": data_type,
            }

    wb.close()
    return specs


def _build_field_list_text(specs: dict[str, dict]) -> str:
    """
    Build the field list section for the AI prompt.

    Formats each field as: "FIELD_CODE — Description (Data Type hints)"

    Args:
        specs: Output of _load_field_specs().

    Returns:
        Multi-line string listing all fields with descriptions.
    """
    lines = []
    for code in PERSON_FIELD_CODES:
        if code not in specs:
            continue
        spec = specs[code]
        entry = f"{code} — {spec['description']}"
        if spec["data_type"]:
            entry += f" ({spec['data_type']})"
        lines.append(entry)
    return "\n".join(lines)


# ── Returning client check ─────────────────────────────────────────────────────

def _check_returning_client(client_name: str) -> list[dict]:
    """
    Check if the client existed in previous tax years.

    Searches the previous LOOK_BACK_YEARS years' Personal Tax folders
    for a matching client subfolder across all workflow stages.

    Args:
        client_name: The client folder name to search for.

    Returns:
        List of dicts sorted by year desc:
        [{"year": str, "path": str, "folder": str, "stage": str}]
        Empty list if client is not found in any previous year.
    """
    current_year = int(config.TAX_YEAR)
    look_back = config.LOOK_BACK_YEARS
    qb_path = config.QUICKBOOK_FILES_PATH

    if not os.path.isdir(qb_path):
        return []

    # Find all "X. YYYY Personal Tax" folders
    year_folders = {}
    for entry in os.listdir(qb_path):
        match = re.match(r"\d+\.\s*(\d{4})\s+Personal Tax", entry)
        if match:
            year_folders[int(match.group(1))] = os.path.join(qb_path, entry)

    found = []
    client_lower = client_name.lower().strip()

    for offset in range(1, look_back + 1):
        year = current_year - offset
        if year not in year_folders:
            continue

        year_path = year_folders[year]

        # Search across all workflow subfolders
        match_info = None
        for subfolder in config.WORKFLOW_FOLDERS_TO_SCAN:
            sub_path = os.path.join(year_path, subfolder)
            if not os.path.isdir(sub_path):
                continue
            for entry in os.listdir(sub_path):
                entry_path = os.path.join(sub_path, entry)
                if os.path.isdir(entry_path) and entry.lower().strip() == client_lower:
                    match_info = {
                        "year": str(year),
                        "path": entry_path,
                        "folder": entry,
                        "stage": subfolder,
                    }
                    break
            if match_info:
                break

        if match_info:
            found.append(match_info)

    return found


# ── Client folder selection ─────────────────────────────────────────────────────

def _list_client_folders() -> list[str]:
    """
    Scan the Intake workflow folder for all client subfolders.

    Returns:
        Sorted list of folder names (not full paths).
    """
    intake_path = os.path.join(config.BASE_WORKFLOW_PATH, config.INTAKE_FOLDER_NAME)
    if not os.path.isdir(intake_path):
        print(f"  [ERROR] Intake folder not found: {intake_path}")
        return []

    folders = []
    for name in os.listdir(intake_path):
        full = os.path.join(intake_path, name)
        if os.path.isdir(full) and not name.startswith("."):
            folders.append(name)
    return sorted(folders)


def _select_client() -> str | None:
    """
    Display client folder list and let user pick one.

    Returns:
        Full path to the selected client's folder, or None if cancelled.
    """
    folders = _list_client_folders()
    if not folders:
        print("  No client folders found in Intake.")
        return None

    print(f"\n  Client folders in {config.INTAKE_FOLDER_NAME}:")
    print(f"  {'─' * 50}")
    for i, name in enumerate(folders, 1):
        print(f"    {i:>3}. {name}")
    print(f"    {'0':>3}. Cancel")
    print(f"  {'─' * 50}")

    while True:
        choice = input("  Select client (number): ").strip()
        if choice == "0":
            return None
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(folders):
                intake_path = os.path.join(config.BASE_WORKFLOW_PATH, config.INTAKE_FOLDER_NAME)
                return os.path.join(intake_path, folders[idx])
            print("  Invalid number. Try again.")
        except ValueError:
            print("  Please enter a number.")


# ── AI extraction ───────────────────────────────────────────────────────────────

def _parse_person_results(parsed: object, file_name: str) -> dict[str, dict[str, dict]]:
    """
    Parse AI response into per-person extraction results.

    Handles both new multi-person format {"individuals": [...]} and
    legacy flat format {"FIELD_CODE": {...}} for backward compatibility.

    Args:
        parsed:     Parsed JSON from AI response.
        file_name:  Source file name for tracking.

    Returns:
        {person_label: {field_code: {"value": str, "confidence": int, "source": str}}}
    """
    # Legacy flat format: wrap as single person
    if "individuals" not in parsed:
        fields = {}
        for code in PERSON_FIELD_CODES:
            if code not in parsed:
                continue
            entry = parsed[code]
            if isinstance(entry, dict):
                val = entry.get("value")
                conf = int(entry.get("confidence", 50))
            else:
                val = entry
                conf = 50
            if val is not None and str(val).strip() != "":
                fields[code] = {
                    "value": str(val).strip(),
                    "confidence": max(0, min(100, conf)),
                    "source": file_name,
                }
        return {"person_1": fields} if fields else {}

    # New multi-person format
    result = {}
    for i, person in enumerate(parsed["individuals"]):
        label = person.get("label", f"Person {i + 1}")
        raw_fields = person.get("fields", {})
        fields = {}
        for code in PERSON_FIELD_CODES:
            if code not in raw_fields:
                continue
            entry = raw_fields[code]
            if isinstance(entry, dict):
                val = entry.get("value")
                conf = int(entry.get("confidence", 50))
            else:
                val = entry
                conf = 50
            if val is not None and str(val).strip() != "":
                fields[code] = {
                    "value": str(val).strip(),
                    "confidence": max(0, min(100, conf)),
                    "source": file_name,
                }
        if fields:
            result[label] = fields
    return result


def _extract_from_file(
    client_obj: object,
    file_path: str,
    field_prompt: str,
    rate_limiter: RateLimiter,
) -> dict[str, dict[str, dict]]:
    """
    Send one file to Gemini and extract tax fields per person.

    Args:
        client_obj:    google.genai.Client instance.
        file_path:     Path to the file to analyze.
        field_prompt:  Pre-built field list text for the AI prompt.
        rate_limiter:  RateLimiter instance.

    Returns:
        {person_label: {field_code: {"value": str, "confidence": int, "source": str}}}
    """
    from google.genai import types

    file_name = os.path.basename(file_path)

    # Prepare file content parts using shared prepare_parts
    parts = prepare_parts(file_path)
    if not parts:
        print(f"    [SKIP] Unsupported format: {file_name}")
        return {}

    # Build content for Gemini: file parts + prompt
    content = []
    for p in parts:
        if isinstance(p, str):
            content.append(p)
        elif isinstance(p, dict):
            content.append(
                types.Part.from_bytes(data=p["data"], mime_type=p["mime_type"])
            )

    prompt_text = EXTRACT_PROMPT.format(field_list=field_prompt)
    content.append(prompt_text)

    # Call Gemini with retry logic
    for attempt in range(config.GEMINI_MAX_RETRIES):
        try:
            rate_limiter.wait()
            response = client_obj.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=content,
            )

            # Parse JSON response
            raw = response.text.strip()
            raw = re.sub(r"^```[a-z]*\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            parsed = json.loads(raw)

            return _parse_person_results(parsed, file_name)

        except json.JSONDecodeError:
            # Gemini returned non-JSON — skip this file
            print(f"    [WARN] Non-JSON response for {file_name}")
            return {}

        except Exception as exc:
            # Check for fatal errors (bad model, invalid key)
            err_str = str(exc).lower()
            is_fatal = any(k in err_str for k in (
                "not found", "invalid", "permission", "unauthenticated",
                "api_key", "does not exist", "unknown model",
            ))
            if is_fatal:
                print(f"\n  [ERROR] Fatal AI error: {exc}")
                return {}

            # Check for quota errors
            is_quota = any(k in err_str for k in ("429", "resource_exhausted", "quota", "rate limit"))
            is_last = (attempt == config.GEMINI_MAX_RETRIES - 1)

            if is_quota:
                wait_sec = 60 * (attempt + 1)
                print(f"\n    [Quota] Waiting {wait_sec}s (retry {attempt + 1}/{config.GEMINI_MAX_RETRIES})...", end="", flush=True)
                time.sleep(wait_sec)
            elif not is_last:
                print(f"\n    [WARN] {exc} — retrying ({attempt + 1}/{config.GEMINI_MAX_RETRIES})...")
                time.sleep(10 * (attempt + 1))
            else:
                print(f"\n    [ERROR] {file_name}: {exc}")
                return {}

    return {}


# ── OpenAI API key management ──────────────────────────────────────────────────

def _load_openai_key() -> str | None:
    """
    Load and decrypt the OpenAI API key.

    Returns:
        Decrypted API key string, or None if not set up.
    """
    if not os.path.exists(config.OPENAI_KEY_FILE):
        return None
    try:
        f = _machine_fernet()
        with open(config.OPENAI_KEY_FILE, "rb") as fh:
            return f.decrypt(fh.read()).decode()
    except Exception:
        return None


def _save_openai_key(key: str) -> None:
    """
    Encrypt and save the OpenAI API key.

    Args:
        key: Plain-text API key to encrypt and store.
    """
    os.makedirs(os.path.dirname(config.OPENAI_KEY_FILE), exist_ok=True)
    f = _machine_fernet()
    with open(config.OPENAI_KEY_FILE, "wb") as fh:
        fh.write(f.encrypt(key.encode()))


def _setup_openai_key() -> str | None:
    """
    Prompt for OpenAI API key if not stored. Returns key or None if cancelled.
    """
    key = _load_openai_key()
    if key:
        return key

    print("\n  OpenAI API key not found.")
    print("  Enter your API key (or press Enter to cancel):")
    key = input("  API Key: ").strip()
    if not key:
        return None
    _save_openai_key(key)
    print("  OpenAI API key saved (encrypted).")
    return key


# ── OpenAI extraction ──────────────────────────────────────────────────────────

def _extract_from_file_openai(
    client_obj: object,
    file_path: str,
    field_prompt: str,
    rate_limiter: RateLimiter,
) -> dict[str, dict[str, dict]]:
    """
    Send one file to OpenAI and extract tax fields per person.

    Args:
        client_obj:    openai.OpenAI instance.
        file_path:     Path to the file to analyze.
        field_prompt:  Pre-built field list text for the AI prompt.
        rate_limiter:  RateLimiter instance.

    Returns:
        {person_label: {field_code: {"value": str, "confidence": int, "source": str}}}
    """
    file_name = os.path.basename(file_path)

    # Prepare file content parts using shared prepare_parts
    parts = prepare_parts(file_path)
    if not parts:
        print(f"    [SKIP] Unsupported format: {file_name}")
        return {}

    # Build OpenAI message content: text parts + images as base64 data URLs
    content = []
    for p in parts:
        if isinstance(p, str):
            content.append({"type": "text", "text": p})
        elif isinstance(p, dict):
            # Convert raw image/pdf bytes to base64 data URL for OpenAI
            b64 = base64.b64encode(p["data"]).decode("utf-8")
            mime = p["mime_type"]
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}", "detail": "high"},
            })

    # Append the extraction prompt as text
    prompt_text = EXTRACT_PROMPT.format(field_list=field_prompt)
    content.append({"type": "text", "text": prompt_text})

    # Call OpenAI with retry logic
    max_retries = config.GEMINI_MAX_RETRIES  # Reuse same retry count
    for attempt in range(max_retries):
        try:
            rate_limiter.wait()
            response = client_obj.chat.completions.create(
                model=config.OPENAI_MODEL,
                messages=[{"role": "user", "content": content}],
                timeout=config.GEMINI_TIMEOUT_SEC,  # Reuse same timeout
            )

            # Parse JSON response
            raw = response.choices[0].message.content.strip()
            raw = re.sub(r"^```[a-z]*\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            parsed = json.loads(raw)

            return _parse_person_results(parsed, file_name)

        except json.JSONDecodeError:
            # OpenAI returned non-JSON — skip this file
            print(f"    [WARN] Non-JSON response for {file_name}")
            return {}

        except Exception as exc:
            # Check for fatal errors (bad model, invalid key)
            err_str = str(exc).lower()
            is_fatal = any(k in err_str for k in (
                "not found", "invalid", "permission", "unauthorized",
                "api_key", "does not exist", "model_not_found",
            ))
            if is_fatal:
                print(f"\n  [ERROR] Fatal AI error: {exc}")
                return {}

            # Check for quota/rate limit errors
            is_quota = any(k in err_str for k in ("429", "rate_limit", "quota", "too many"))
            is_last = (attempt == max_retries - 1)

            if is_quota:
                wait_sec = 60 * (attempt + 1)
                print(f"\n    [Quota] Waiting {wait_sec}s (retry {attempt + 1}/{max_retries})...", end="", flush=True)
                time.sleep(wait_sec)
            elif not is_last:
                print(f"\n    [WARN] {exc} — retrying ({attempt + 1}/{max_retries})...")
                time.sleep(10 * (attempt + 1))
            else:
                print(f"\n    [ERROR] {file_name}: {exc}")
                return {}

    return {}


def _make_person_key(person_fields: dict[str, dict], label: str = "") -> str:
    """
    Create a normalized key for matching the same person across files.

    Uses FirstName + LastName if available, otherwise falls back to label.
    """
    first = person_fields.get("ID.FirstName", {}).get("value", "").strip().lower()
    last = person_fields.get("ID.LastName", {}).get("value", "").strip().lower()
    if first and last:
        return f"{first} {last}"
    return label.strip().lower() or "person_1"


def _merge_fields(entries: list[dict]) -> dict[str, dict]:
    """
    Merge field entries from multiple files for one person.

    Same logic as the old _merge_results but for a single person's fields.
    """
    # Collect all values per field
    field_values: dict[str, list[dict]] = {}
    for entry in entries:
        code = entry["_code"]
        if code not in field_values:
            field_values[code] = []
        field_values[code].append(entry)

    merged = {}

    for code, field_entries in field_values.items():
        by_value: dict[str, list[dict]] = {}
        for e in field_entries:
            val = e["value"].lower().strip()
            if val not in by_value:
                by_value[val] = []
            by_value[val].append(e)

        all_sources = [e["source"] for e in field_entries]

        if len(by_value) == 1:
            best = max(field_entries, key=lambda e: e["confidence"])
            merged[code] = {
                "value": best["value"],
                "confidence": min(100, best["confidence"] + 10),
                "sources": all_sources,
                "status": "filled",
            }
        else:
            sorted_groups = sorted(
                by_value.values(),
                key=lambda group: max(e["confidence"] for e in group),
                reverse=True,
            )
            best_entries = sorted_groups[0]
            second_best_entries = sorted_groups[1] if len(sorted_groups) > 1 else []
            best_conf = max(e["confidence"] for e in best_entries)
            second_conf = max(e["confidence"] for e in second_best_entries) if second_best_entries else 0

            if second_conf >= 60:
                best = max(best_entries, key=lambda e: e["confidence"])
                second = max(second_best_entries, key=lambda e: e["confidence"])
                merged[code] = {
                    "value": f"CONFLICT: {best['source']}({best['value']}) vs {second['source']}({second['value']})",
                    "confidence": best_conf,
                    "sources": all_sources,
                    "status": "conflict",
                }
            else:
                best = max(best_entries, key=lambda e: e["confidence"])
                merged[code] = {
                    "value": best["value"],
                    "confidence": best_conf,
                    "sources": all_sources,
                    "status": "filled",
                }

    return merged


def _merge_results(per_file_results: list[dict]) -> dict[str, dict]:
    """
    Merge per-person extraction results from multiple files.

    Args:
        per_file_results: List of {person_label: {field_code: {value, confidence, source}}}.

    Returns:
        {person_key: {
            "label": str,
            "fields": {field_code: {value, confidence, sources, status}}
        }}
    """
    # Group all field entries by normalized person key
    person_groups: dict[str, list[dict]] = {}
    person_labels: dict[str, str] = {}

    for file_result in per_file_results:
        for label, fields in file_result.items():
            key = _make_person_key(fields, label)
            if key not in person_groups:
                person_groups[key] = []
                person_labels[key] = label
            # Flatten: tag each entry with its field code
            for code, entry in fields.items():
                person_groups[key].append({**entry, "_code": code})

    merged = {}
    for key, entries in person_groups.items():
        merged_fields = _merge_fields(entries)
        if merged_fields:
            # Use best available label
            best_label = person_labels.get(key, key)
            first = merged_fields.get("ID.FirstName", {}).get("value", "")
            last = merged_fields.get("ID.LastName", {}).get("value", "")
            if first and last:
                best_label = f"{first} {last}"
            merged[key] = {
                "label": best_label,
                "fields": merged_fields,
            }

    return merged


# ── Output generation ───────────────────────────────────────────────────────────

def _get_output_dir(client_folder: str) -> str:
    """
    Determine the output directory inside the client folder.

    Looks for CRA_INTAKE_SUBFOLDER ("1.Client Intake") first,
    then CLIENT_SUBFOLDERS[0] ("1. Client Intake"), then falls back to client folder root.

    Args:
        client_folder: Path to the client's main folder.

    Returns:
        Path to the output subdirectory.
    """
    # Try exact CRA subfolder name (from config)
    cra_sub = os.path.join(client_folder, config.CRA_INTAKE_SUBFOLDER)
    if os.path.isdir(cra_sub):
        return cra_sub

    # Try standard subfolder name (from config)
    std_sub = os.path.join(client_folder, config.CLIENT_SUBFOLDERS[0])
    if os.path.isdir(std_sub):
        return std_sub

    # Fall back to client folder root
    return client_folder


def _write_info_templates(
    merged: dict[str, dict],
    client_folder: str,
    specs: dict[str, dict],
) -> list[str]:
    """
    Create one Info Template per detected person.

    For a single person: creates one template with their primary fields.
    For a couple: creates two templates, cross-filling spouse fields from partner.

    Args:
        merged:         Per-person merged results from _merge_results().
        client_folder:  Path to the client's main folder.
        specs:          Field specifications from _load_field_specs().

    Returns:
        List of full paths to the written xlsx files.
    """
    import openpyxl

    if not os.path.exists(config.INFO_TEMPLATE_PATH):
        raise FileNotFoundError(f"Template not found: {config.INFO_TEMPLATE_PATH}")

    output_dir = _get_output_dir(client_folder)
    client_name = os.path.basename(client_folder)
    persons = list(merged.values())
    is_couple = len(persons) >= 2

    # Build field_row_map from template (same for all copies)
    try:
        wb_ref = openpyxl.load_workbook(config.INFO_TEMPLATE_PATH)
    except Exception:
        raise FileNotFoundError(f"Cannot open template: {config.INFO_TEMPLATE_PATH}")
    if "Export" not in wb_ref.sheetnames:
        wb_ref.close()
        raise ValueError("Template has no 'Export' sheet")
    ws_ref = wb_ref["Export"]
    field_row_map = {}
    for row_idx in range(2, ws_ref.max_row + 1):
        code = str(ws_ref.cell(row_idx, 1).value or "").strip()
        if code:
            field_row_map[code] = row_idx
    wb_ref.close()

    written = []

    for i, person in enumerate(persons):
        fields = person["fields"]
        label = person["label"]

        # Determine output filename
        if not is_couple:
            output_name = config.INFO_OUTPUT_TEMPLATE.format(client=client_name)
        else:
            # Couple: use client folder name + person's first name
            suffix = fields.get("ID.FirstName", {}).get("value", label.split()[0] if label else f"Person{i+1}")
            output_name = f"Info Template {client_name} - {suffix}.xlsx"
        output_path = os.path.join(output_dir, output_name)

        # Partner for cross-fill (if couple)
        partner = persons[1 - i] if is_couple and len(persons) == 2 else None
        partner_fields = partner["fields"] if partner else {}

        # Copy template
        shutil.copy2(config.INFO_TEMPLATE_PATH, output_path)

        try:
            wb = openpyxl.load_workbook(output_path)
        except Exception:
            if os.path.exists(output_path):
                os.remove(output_path)
            continue

        if "Export" not in wb.sheetnames:
            wb.close()
            if os.path.exists(output_path):
                os.remove(output_path)
            continue

        ws = wb["Export"]

        # Fill primary fields
        for code, entry in fields.items():
            if code in field_row_map and entry["status"] != "conflict":
                ws.cell(field_row_map[code], 2).value = entry["value"]

        # Cross-fill spouse fields from partner
        if partner_fields:
            for spouse_code, primary_code in SPOUSE_TO_PRIMARY.items():
                if spouse_code in field_row_map and primary_code in partner_fields:
                    p_entry = partner_fields[primary_code]
                    if p_entry.get("status") != "conflict" and p_entry.get("value"):
                        ws.cell(field_row_map[spouse_code], 2).value = p_entry["value"]

        # Delete Sheet1 — TaxCycle requires exactly one sheet
        if "Sheet1" in wb.sheetnames:
            del wb["Sheet1"]

        try:
            wb.save(output_path)
            written.append(output_path)
        except (PermissionError, OSError):
            wb.close()
            if os.path.exists(output_path):
                os.remove(output_path)
            continue
        wb.close()

    return written


def _write_source_report(
    merged: dict[str, dict],
    client_folder: str,
    specs: dict[str, dict],
) -> str:
    """
    Generate a source tracking report showing where each value came from.
    Supports multi-person output.

    Returns:
        Full path to the written report file. Empty string if nothing to report.
    """
    if not merged:
        return ""

    output_dir = _get_output_dir(client_folder)
    client_name = os.path.basename(client_folder)
    today = date.today().isoformat()
    report_name = config.INFO_SOURCE_REPORT.format(client=client_name, date=today)
    report_path = os.path.join(output_dir, report_name)

    lines = []
    lines.append(f"Tax Info — Source Tracking Report")
    lines.append(f"Client: {client_name}")
    lines.append(f"Generated: {today}")
    is_couple = len(merged) >= 2
    if is_couple:
        names = [p["label"] for p in merged.values()]
        lines.append(f"Persons detected: {', '.join(names)}")
    lines.append(f"=" * 60)
    lines.append("")

    for person_key, person in merged.items():
        if is_couple:
            lines.append(f"  >>> {person['label']} <<<")
            lines.append("")

        current_group = ""
        for code in PERSON_FIELD_CODES:
            if code not in specs:
                continue
            group_name = "Other"
            for prefix, name in FIELD_GROUPS.items():
                if code.startswith(prefix):
                    group_name = name
                    break
            if group_name != current_group:
                current_group = group_name
                lines.append(f"--- {group_name} ---")

            desc = specs[code].get("description", code)
            fields = person["fields"]

            if code in fields:
                entry = fields[code]
                sources = ", ".join(entry["sources"])
                if entry["status"] == "conflict":
                    lines.append(f"  [CONFLICT] {code}")
                    lines.append(f"    Value: {entry['value']}")
                    lines.append(f"    From: {sources}")
                else:
                    lines.append(f"  [OK] {code} — {desc}")
                    lines.append(f"    Value: {entry['value']}")
                    lines.append(f"    Confidence: {entry['confidence']}%")
                    lines.append(f"    From: {sources}")
            lines.append("")

    # Spouse cross-fill summary for couples
    if is_couple:
        lines.append(f"--- Spouse Cross-Fill ---")
        for spouse_code, primary_code in SPOUSE_TO_PRIMARY.items():
            lines.append(f"  {spouse_code} <- {primary_code}")
        lines.append("")

    total_fields = sum(len(p["fields"]) for p in merged.values())
    lines.append(f"=" * 60)
    lines.append(f"Total persons: {len(merged)} | Total fields: {total_fields}")

    content = "\n".join(lines)
    try:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(content)
    except (PermissionError, OSError) as e:
        print(f"  [WARN] Could not write source report: {e}")
        return ""

    return report_path


def _write_missing_report(
    merged: dict[str, dict],
    client_folder: str,
    specs: dict[str, dict],
) -> str:
    """
    Generate a missing information report per person.

    Returns:
        Full path to the written report file. Empty string if no fields are missing.
    """
    output_dir = _get_output_dir(client_folder)
    client_name = os.path.basename(client_folder)
    today = date.today().isoformat()
    report_name = config.INFO_MISSING_REPORT.format(client=client_name, date=today)
    report_path = os.path.join(output_dir, report_name)

    has_anything = False
    lines = []
    lines.append(f"Missing Tax Information")
    lines.append(f"Client: {client_name}")
    lines.append(f"Generated: {today}")
    lines.append(f"=" * 60)
    lines.append("")

    for person_key, person in merged.items():
        fields = person["fields"]
        missing = []
        conflicts = []

        for code in PERSON_FIELD_CODES:
            if code not in specs:
                continue
            desc = specs[code].get("description", code)
            if code not in fields:
                missing.append((code, desc))
            elif fields[code]["status"] == "conflict":
                conflicts.append((code, desc, fields[code]["value"]))

        if not missing and not conflicts:
            continue

        has_anything = True
        lines.append(f"  >>> {person['label']} <<<")
        lines.append("")

        if conflicts:
            lines.append(f"  --- Conflicts (needs manual review) ---")
            for code, desc, val in conflicts:
                lines.append(f"    [!] {code} — {desc}")
                lines.append(f"        {val}")
            lines.append("")

        if missing:
            current_group = ""
            lines.append(f"  --- Missing Fields ---")
            for code in PERSON_FIELD_CODES:
                if code not in specs:
                    continue
                if not any(m[0] == code for m in missing):
                    continue
                desc = specs[code].get("description", code)
                group_name = "Other"
                for prefix, name in FIELD_GROUPS.items():
                    if code.startswith(prefix):
                        group_name = name
                        break
                if group_name != current_group:
                    current_group = group_name
                    lines.append(f"")
                    lines.append(f"    {group_name}:")
                lines.append(f"      [ ] {code} — {desc}")

        total_person_fields = len(fields)
        lines.append("")
        lines.append(f"  Missing: {len(missing)} | Conflicts: {len(conflicts)} | Extracted: {total_person_fields} / {len(specs)}")
        lines.append("")

    if not has_anything:
        return ""

    lines.append(f"=" * 60)
    content = "\n".join(lines)
    try:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(content)
    except (PermissionError, OSError) as e:
        print(f"  [WARN] Could not write missing info report: {e}")
        return ""

    return report_path


# ── Logging ─────────────────────────────────────────────────────────────────────

def _log_run(client_name: str, merged: dict, total_fields: int) -> None:
    """
    Append a run record to the AI extraction log.

    Args:
        client_name:  Name of the client folder.
        merged:       Per-person merged results.
        total_fields: Total number of spec fields per person.
    """
    log_path = config.AI_EXTRACT_LOG
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    records = []
    if os.path.exists(log_path):
        try:
            with open(log_path, encoding="utf-8") as f:
                records = json.load(f)
        except (json.JSONDecodeError, ValueError):
            records = []

    persons_info = []
    for person_key, person in merged.items():
        filled = sum(1 for v in person["fields"].values() if v["status"] == "filled")
        missing = total_fields - len(person["fields"])
        persons_info.append({
            "name": person["label"],
            "extracted": filled,
            "missing": missing,
        })

    records.append({
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "client": client_name,
        "persons": persons_info,
        "person_count": len(merged),
    })

    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)


# ── Main entry ──────────────────────────────────────────────────────────────────

def ai_extract_info() -> None:
    """
    Menu 2 entry: AI Tax Info Extraction.

    Flow:
    1. Setup Gemini API key
    2. User selects a client folder
    3. Collect files from client's Intake subfolder
    4. Phase 1: AI extracts fields from each file
    5. Phase 2: Merge results across files
    6. Phase 3: Write filled Info Template copy
    7. Phase 4: Generate source tracking + missing info reports
    """
    print("\n" + "=" * 60)
    print("  AI Tax Info Extraction")
    print("=" * 60)

    # ── Step 1: Select AI provider ──────────────────────────────────────────
    print("\n  Select AI provider:")
    print("    1. Gemini (Google)")
    print("    2. OpenAI")
    print("    0. Cancel")
    provider_choice = input("  Enter choice: ").strip()

    if provider_choice == "0":
        return

    # provider: "gemini" or "openai"
    if provider_choice == "2":
        provider = "openai"
    else:
        provider = "gemini"

    # ── Step 2: API key ──────────────────────────────────────────────────────
    if provider == "gemini":
        api_key = setup_api_key()
        if not api_key:
            print("\n  No API key — cancelled.")
            return
    else:
        api_key = _setup_openai_key()
        if not api_key:
            print("\n  No API key — cancelled.")
            return

    # ── Step 3: Load field specs from template ──────────────────────────────
    print("\n  Loading field specifications...")
    specs = _load_field_specs()
    if not specs:
        print("  [ERROR] Could not load field specs from Info Template.")
        return
    print(f"  Loaded {len(specs)} core fields.")

    # Build the field list text for AI prompt (do this once, reuse for all files)
    field_prompt = _build_field_list_text(specs)

    # ── Step 4: Select client ───────────────────────────────────────────────
    client_folder = _select_client()
    if not client_folder:
        print("\n  Cancelled.")
        return

    client_name = os.path.basename(client_folder)
    print(f"\n  Selected: {client_name}")

    # ── Step 4.5: Check if returning client ────────────────────────────────
    returning = _check_returning_client(client_name)
    if returning:
        print(f"\n  *** Returning client detected! ***")
        for match in returning:
            print(f"    {match['year']} tax year: found in '{match['stage']}'")
            print(f"      Path: {match['path']}")
        print(f"\n  Action required:")
        print(f"    Open the previous year folder and use TaxCycle carry forward")
        print(f"    to import intake data before running AI extraction.")

        open_choice = input("\n  Open the most recent year folder in Explorer? (Y/N): ").strip().upper()
        if open_choice == "Y":
            os.startfile(returning[0]["path"])

        cont = input("\n  Press Enter to continue with AI extraction, or '0' to cancel: ").strip()
        if cont == "0":
            return
    else:
        print(f"  New client — no previous year records found.")

    # ── Step 5: Collect files from Intake subfolder ─────────────────────────
    # Determine which subfolder to scan
    intake_sub = _get_output_dir(client_folder)
    print(f"  Scanning: {intake_sub}")

    # Collect files with intake extensions
    files = []
    for root, _, filenames in os.walk(intake_sub):
        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext in SCAN_EXTENSIONS:
                full_path = os.path.join(root, fname)
                # Skip files over size limit
                if os.path.getsize(full_path) <= config.GEMINI_MAX_FILE_MB * 1024 * 1024:
                    files.append(full_path)

    # Also exclude the template output itself if this was run before
    files = [f for f in files if not os.path.basename(f).startswith("Info Template")]
    files.sort()

    if not files:
        print(f"\n  No supported files found in Intake subfolder.")
        print(f"  Supported formats: {', '.join(sorted(SCAN_EXTENSIONS))}")
        return

    print(f"  Found {len(files)} file(s).")

    # ── Step 6: Init AI client + connection test ────────────────────────────
    if provider == "gemini":
        from google import genai
        ai_client = genai.Client(
            api_key=api_key,
            http_options={"timeout": config.GEMINI_TIMEOUT_SEC * 1000},  # ms
        )
        model_name = config.GEMINI_MODEL
        extract_fn = _extract_from_file
    else:
        from openai import OpenAI
        ai_client = OpenAI(api_key=api_key)
        model_name = config.OPENAI_MODEL
        extract_fn = _extract_from_file_openai

    print(f"\n  Testing connection to {model_name} ({provider})...", end="", flush=True)
    try:
        if provider == "gemini":
            ai_client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=["Reply with the single word: OK"],
            )
        else:
            ai_client.chat.completions.create(
                model=config.OPENAI_MODEL,
                messages=[{"role": "user", "content": "Reply with the single word: OK"}],
                timeout=config.GEMINI_TIMEOUT_SEC,
                max_tokens=10,
            )
        print(" OK")
    except Exception as exc:
        print(f"\n  [ERROR] Connection test failed: {exc}")
        print(f"  Check {provider.upper()} model and API key.")
        return

    # ── Step 7: Phase 1 — Extract fields from each file ────────────────────
    rate_limiter = RateLimiter(config.GEMINI_RPM)
    per_file_results = []
    width = len(str(len(files)))

    print(f"\n  Extracting tax info from {len(files)} file(s)...\n")

    for i, file_path in enumerate(files, 1):
        rel_name = os.path.relpath(file_path, intake_sub)
        print(f"  [{i:{width}}/{len(files)}] {rel_name[:60]:<60}", end="", flush=True)

        result = extract_fn(ai_client, file_path, field_prompt, rate_limiter)
        person_count = len(result)
        field_count = sum(len(fields) for fields in result.values())
        per_file_results.append(result)

        print(f"  →  {person_count} person(s), {field_count} field(s)")

    # ── Step 8: Phase 2 — Merge results ────────────────────────────────────
    print(f"\n  Merging results...")
    merged = _merge_results(per_file_results)

    is_couple = len(merged) >= 2
    person_type = "Couple" if is_couple else ("Single" if len(merged) == 1 else "None")
    print(f"  Detected: {person_type} ({len(merged)} person(s))")

    for person_key, person in merged.items():
        filled = sum(1 for v in person["fields"].values() if v["status"] == "filled")
        conflicts = sum(1 for v in person["fields"].values() if v["status"] == "conflict")
        missing = len(PERSON_FIELD_CODES) - len(person["fields"])
        print(f"    {person['label']}: {filled} extracted, {conflicts} conflicts, {missing} missing / {len(PERSON_FIELD_CODES)}")

    total_filled = sum(
        sum(1 for v in p["fields"].values() if v["status"] == "filled")
        for p in merged.values()
    )

    if total_filled == 0:
        print("\n  [WARN] No fields could be extracted from any file.")
        confirm = input("  Continue to generate reports anyway? (Y/N): ").strip().upper()
        if confirm != "Y":
            return

    # ── Step 9: Phase 3 — Write filled template(s) ─────────────────────────
    print(f"\n  Writing Info Template(s)...")
    try:
        template_paths = _write_info_templates(merged, client_folder, specs)
        for tp in template_paths:
            print(f"  [OK] Saved: {os.path.basename(tp)}")
    except Exception as e:
        print(f"  [ERROR] Failed to write template: {e}")
        template_paths = []

    # ── Step 10: Phase 4 — Generate reports ────────────────────────────────
    print(f"\n  Generating reports...")

    source_path = _write_source_report(merged, client_folder, specs)
    if source_path:
        print(f"  [OK] Source report: {os.path.basename(source_path)}")

    missing_path = _write_missing_report(merged, client_folder, specs)
    if missing_path:
        print(f"  [OK] Missing info:  {os.path.basename(missing_path)}")
    else:
        print(f"  [OK] All core fields extracted — no missing info report needed.")

    # ── Step 11: Log ────────────────────────────────────────────────────────
    _log_run(client_name, merged, len(PERSON_FIELD_CODES))

    # ── Summary ─────────────────────────────────────────────────────────────
    print(f"\n  {'─' * 50}")
    print(f"  Client: {client_name}")
    print(f"  Files processed: {len(files)}")
    print(f"  Persons detected: {len(merged)}")
    for person_key, person in merged.items():
        filled = sum(1 for v in person["fields"].values() if v["status"] == "filled")
        print(f"    {person['label']}: {filled} fields")
    if template_paths:
        print(f"\n  Output files in: {_get_output_dir(client_folder)}")
    print(f"  {'─' * 50}")


if __name__ == "__main__":
    ai_extract_info()
    input("\nPress Enter to exit...")
