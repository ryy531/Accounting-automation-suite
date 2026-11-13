"""
Apply People person-group candidates to NocoDB.

Authorized scope:
- Create People records only.
- Source: accepted person_group_import_candidates.json.
- No schema changes, no Companies updates, no relations, no Engagements/Events.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / "_lib" / "config" / ".env"
SCHEMA_SNAPSHOT = ROOT / "payroll" / "_doc" / "nocodb_current_state_snapshot_2026-06-03.json"
ACCEPTED_DRY_RUN = ROOT / "payroll" / "_reports" / "people_person_group_dryrun_20260605_155109"
REPORT_ROOT = ROOT / "payroll" / "_reports"
HANDOFF = ROOT / "payroll" / "_doc" / "CodexTransfer.md"
VALID_LANGUAGES = {"Chinese", "English", "Vietnamese", "1"}


def load_schema_snapshot() -> dict[str, Any]:
    return json.loads(SCHEMA_SNAPSHOT.read_text(encoding="utf-8-sig"))


def table_by_name(schema: dict[str, Any], name: str) -> dict[str, Any]:
    for table in schema.get("tables", []):
        if table.get("name") == name:
            return table
    raise KeyError(name)


def people_table_id() -> str:
    fallback = table_by_name(load_schema_snapshot(), "People").get("table_id", "")
    return os.getenv("NOCODB_PEOPLE_TABLE_ID") or fallback


def api_base() -> str:
    base = os.getenv("NOCODB_URL", "").rstrip("/")
    if not base:
        raise RuntimeError("NOCODB_URL missing")
    return base


def headers() -> dict[str, str]:
    token = os.getenv("NOCODB_TOKEN", "")
    if not token:
        raise RuntimeError("NOCODB_TOKEN missing")
    return {"xc-token": token, "Content-Type": "application/json"}


def fetch_people_rows(table_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        response = requests.get(
            f"{api_base()}/api/v2/tables/{table_id}/records",
            headers=headers(),
            params={"limit": 100, "offset": offset},
            timeout=15,
        )
        response.raise_for_status()
        batch = response.json().get("list", [])
        rows.extend(batch)
        if len(batch) < 100:
            break
        offset += 100
    return rows


def fetch_people_metadata(table_id: str) -> dict[str, Any]:
    response = requests.get(
        f"{api_base()}/api/v2/meta/tables/{table_id}",
        headers=headers(),
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def preflight(table_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    metadata = fetch_people_metadata(table_id)
    columns = {column.get("title"): column for column in metadata.get("columns", [])}
    required = ["Full Name", "Email", "Language", "Is Primary Owner", "Notes", "Related Companies"]
    missing = [field for field in required if field not in columns]
    related = columns.get("Related Companies", {})
    related_ok = bool(related) and (related.get("uidt") == "LongText" or related.get("dt") == "text")
    rows = fetch_people_rows(table_id)
    result = {
        "missing_required_fields": missing,
        "related_companies_exists": "Related Companies" in columns,
        "related_companies_type": related.get("uidt", ""),
        "related_companies_db_type": related.get("dt", ""),
        "related_companies_suitable": related_ok,
        "people_row_count": len(rows),
        "live_field_titles": sorted(columns.keys()),
    }
    return result, rows, metadata


def build_payload(candidate: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "Full Name": candidate["full_name"],
        "Is Primary Owner": True,
        "Related Companies": candidate["company_summary"],
        "Notes": candidate["notes"],
    }
    if candidate.get("email"):
        payload["Email"] = candidate["email"]
    if candidate.get("language") in VALID_LANGUAGES:
        payload["Language"] = candidate["language"]
    return payload


def create_people_row(table_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = requests.post(
        f"{api_base()}/api/v2/tables/{table_id}/records",
        headers=headers(),
        json=[payload],
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_handoff(report: dict[str, Any]) -> None:
    status = report["status"]
    preflight = report["preflight"]
    text = f"""# Codex 对接报告 - People Person-Group Apply

日期: 2026-06-05  
状态: {status}  
当前暂停点: People-only apply 已结束，等待 Riley / Claude Code 审查。

## 1. 执行范围

本轮按授权只执行 People table create。

安全确认:

- NocoDB schema change: 0
- Excel 修改: 0
- Companies update: 0
- People-Company relation: 0
- Engagement / Events / cursor / payroll: 0
- CRA / invoice / outbound email: 0
- password / token / key / SIN / secrets 打印: 0

## 2. Preflight

| 项目 | 结果 |
| --- | --- |
| People row count before apply | {preflight['people_row_count']} |
| Related Companies detected | {preflight['related_companies_exists']} |
| Related Companies type | {preflight['related_companies_type']} / {preflight['related_companies_db_type']} |
| Related Companies suitable | {preflight['related_companies_suitable']} |
| Missing required fields | {', '.join(preflight['missing_required_fields']) if preflight['missing_required_fields'] else 'none'} |

## 3. Apply Result

| 指标 | 数量 |
| --- | ---: |
| People records attempted | {report['attempted']} |
| People records created | {report['created']} |
| People records skipped | {report['skipped']} |
| Failures | {len(report['failures'])} |

Apply report folder:

`{report['report_dir']}`

Generated files:

- `apply_summary.json`
- `created_records.json`
- `failures.json`

## 4. 失败 / Blocker

{report['failure_text']}

## 5. Final Pause Point

已暂停。

请 Riley / Claude Code 审查 apply report folder 和 NocoDB People 表。

本轮没有执行任何授权范围外动作。
"""
    HANDOFF.write_text(text, encoding="utf-8")


def main() -> None:
    load_dotenv(ENV_PATH)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = REPORT_ROOT / f"people_person_group_apply_{timestamp}"
    report_dir.mkdir(parents=True, exist_ok=False)

    table_id = people_table_id()
    candidates = json.loads((ACCEPTED_DRY_RUN / "person_group_import_candidates.json").read_text(encoding="utf-8"))
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "status": "success",
        "report_dir": str(report_dir),
        "accepted_dry_run": str(ACCEPTED_DRY_RUN),
        "attempted": 0,
        "created": 0,
        "skipped": 0,
        "failures": [],
        "created_records": [],
        "preflight": {},
        "failure_text": "无失败。",
    }

    preflight_result, current_rows, _metadata = preflight(table_id)
    report["preflight"] = preflight_result
    if preflight_result["missing_required_fields"] or not preflight_result["related_companies_suitable"]:
        report["status"] = "blocked"
        report["skipped"] = len(candidates)
        report["failure_text"] = "Preflight failed: required People fields missing or Related Companies is not suitable."
        write_json(report_dir / "apply_summary.json", report)
        write_json(report_dir / "created_records.json", [])
        write_json(report_dir / "failures.json", report["failures"])
        write_handoff(report)
        print("PEOPLE_APPLY_BLOCKED")
        print(f"report_dir={report_dir}")
        return
    if current_rows:
        report["status"] = "blocked"
        report["skipped"] = len(candidates)
        possible_names = [row.get("Full Name", "") for row in current_rows[:20]]
        report["failures"].append(
            {
                "reason": "people_table_not_empty_preflight",
                "people_row_count": len(current_rows),
                "sample_full_names": possible_names,
            }
        )
        report["failure_text"] = f"Preflight stopped before writing because People row count is {len(current_rows)}, expected 0."
        write_json(report_dir / "apply_summary.json", report)
        write_json(report_dir / "created_records.json", [])
        write_json(report_dir / "failures.json", report["failures"])
        write_handoff(report)
        print("PEOPLE_APPLY_BLOCKED")
        print(f"report_dir={report_dir}")
        return

    for index, candidate in enumerate(candidates, start=1):
        payload = build_payload(candidate)
        if payload["Related Companies"] != candidate["company_summary"]:
            report["status"] = "failed"
            report["failures"].append(
                {
                    "candidate_index": index,
                    "normalized_name": candidate.get("normalized_name"),
                    "reason": "related_companies_payload_mismatch",
                }
            )
            break
        try:
            report["attempted"] += 1
            created = create_people_row(table_id, payload)
            report["created"] += 1
            report["created_records"].append(
                {
                    "candidate_index": index,
                    "normalized_name": candidate.get("normalized_name"),
                    "full_name": candidate.get("full_name"),
                    "source_rows": candidate.get("source_rows"),
                    "response": created,
                }
            )
        except Exception as exc:
            report["status"] = "failed"
            report["failures"].append(
                {
                    "candidate_index": index,
                    "normalized_name": candidate.get("normalized_name"),
                    "full_name": candidate.get("full_name"),
                    "reason": type(exc).__name__,
                    "detail": str(exc)[:500],
                    "created_before_failure": report["created"],
                }
            )
            break

    if report["status"] == "failed":
        report["skipped"] = len(candidates) - report["attempted"]
        report["failure_text"] = "Apply stopped after first unexpected failure. No rollback/delete attempted."
    else:
        report["skipped"] = len(candidates) - report["created"]
        report["failure_text"] = "无失败。"

    write_json(report_dir / "apply_summary.json", report)
    write_json(report_dir / "created_records.json", report["created_records"])
    write_json(report_dir / "failures.json", report["failures"])
    write_handoff(report)
    print("PEOPLE_APPLY_COMPLETE")
    print(f"report_dir={report_dir}")
    print(json.dumps({"status": report["status"], "attempted": report["attempted"], "created": report["created"], "skipped": report["skipped"], "failures": len(report["failures"])}, indent=2))


if __name__ == "__main__":
    main()
